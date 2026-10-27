"""Let's Encrypt certificates for the direct mode (domains.py), via DNS-01.

The challenge is a TXT record set through the registrar's API, so no port
80 has to be open and it works the same behind any firewall. RFC 8555 with
an ES256 account key; certificates live in ~/.config/remote-control/tls/,
renewed when less than 30 days remain.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

from .gate import _write_private, preference_path

DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
RENEW_DAYS = 30
PROPAGATION_S = 600


class AcmeError(RuntimeError):
    pass


def tls_dir(home: Path | None = None) -> Path:
    return preference_path(home).with_name("tls")


def cert_paths(host: str, home: Path | None = None) -> tuple[Path, Path]:
    folder = tls_dir(home) / host
    return folder / "fullchain.pem", folder / "key.pem"


def days_left(cert: Path) -> float:
    try:
        data = x509.load_pem_x509_certificate(cert.read_bytes())
    except (OSError, ValueError):
        return -1
    # not_valid_after_utc is cryptography 42+; Ubuntu 24.04 ships 41.
    end = getattr(data, "not_valid_after_utc", None) or data.not_valid_after.replace(tzinfo=dt.timezone.utc)
    return (end - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwk(key: ec.EllipticCurvePrivateKey) -> dict:
    nums = key.public_key().public_numbers()
    return {"crv": "P-256", "kty": "EC", "x": b64(nums.x.to_bytes(32, "big")), "y": b64(nums.y.to_bytes(32, "big"))}


def thumbprint(jwk: dict) -> str:
    # RFC 7638: required members only, sorted, no spaces.
    return b64(hashlib.sha256(json.dumps(jwk, sort_keys=True, separators=(",", ":")).encode()).digest())


def sign_es256(key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    """JWS wants r||s (64 bytes), not the DER that cryptography returns."""
    r, s = decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def txt_value(token: str, jwk: dict) -> str:
    key_auth = f"{token}.{thumbprint(jwk)}"
    return b64(hashlib.sha256(key_auth.encode()).digest())


def _load_or_make_key(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if isinstance(key, ec.EllipticCurvePrivateKey):
            return key
    except (OSError, ValueError):
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    _write_private(path, pem.decode("ascii"))
    return key


class Client:
    def __init__(self, account_key: ec.EllipticCurvePrivateKey, directory: str = DIRECTORY) -> None:
        self.key = account_key
        self.jwk = _jwk(account_key)
        self.dir = self._get(directory)
        self.kid: str | None = None
        self._nonce: str | None = None

    def _get(self, url: str) -> dict:
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AcmeError(f"Let's Encrypt no responde: {exc}") from exc

    def _new_nonce(self) -> str:
        req = urllib.request.Request(self.dir["newNonce"], method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.headers["Replay-Nonce"]

    def post(self, url: str, payload: dict | None, retry: bool = True) -> tuple[dict | str, dict]:
        """Signed POST; payload None is POST-as-GET. Returns (body, headers)."""
        protected = {"alg": "ES256", "nonce": self._nonce or self._new_nonce(), "url": url}
        if self.kid:
            protected["kid"] = self.kid
        else:
            protected["jwk"] = self.jwk
        self._nonce = None
        p64 = b64(json.dumps(protected).encode())
        y64 = "" if payload is None else b64(json.dumps(payload).encode())
        body = json.dumps({"protected": p64, "payload": y64, "signature": b64(sign_es256(self.key, f"{p64}.{y64}".encode()))})
        req = urllib.request.Request(url, data=body.encode(), method="POST", headers={"Content-Type": "application/jose+json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, headers, raw = resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            status, headers, raw = exc.code, dict(exc.headers), exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise AcmeError(f"Let's Encrypt no responde: {exc}") from exc
        self._nonce = headers.get("Replay-Nonce")
        ctype = headers.get("Content-Type", "")
        data: dict | str = json.loads(raw) if "json" in ctype and raw else raw.decode("utf-8", "replace")
        if status >= 400:
            if retry and isinstance(data, dict) and data.get("type", "").endswith(":badNonce"):
                return self.post(url, payload, retry=False)
            detail = data.get("detail") if isinstance(data, dict) else data
            raise AcmeError(f"Let's Encrypt: {detail}")
        return data, headers

    def account(self, email: str = "") -> None:
        payload: dict = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = [f"mailto:{email}"]
        _data, headers = self.post(self.dir["newAccount"], payload)
        self.kid = headers.get("Location")

    def poll(self, url: str, done: tuple[str, ...], timeout: float = 120) -> dict:
        deadline = time.time() + timeout
        while True:
            data, _headers = self.post(url, None)
            assert isinstance(data, dict)
            if data.get("status") in done:
                return data
            if data.get("status") == "invalid":
                errors = [c.get("error", {}).get("detail") for c in data.get("challenges") or [] if c.get("error")]
                raise AcmeError("Let's Encrypt rechazó la validación: " + ("; ".join(e for e in errors if e) or json.dumps(data)[:300]))
            if time.time() > deadline:
                raise AcmeError("Let's Encrypt tardó demasiado en validar")
            time.sleep(3)


# --- is the TXT record out there yet ------------------------------------------------------


def _doh(name: str, rtype: str, server: str = "https://1.1.1.1/dns-query") -> list[str]:
    req = urllib.request.Request(f"{server}?name={name}&type={rtype}", headers={"Accept": "application/dns-json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [str(a.get("data", "")).strip('"').rstrip(".") for a in data.get("Answer") or []]


def _authoritative(zone: str) -> list[str]:
    return [ns for ns in _doh(zone, "NS") if ns]


def _dig_txt(name: str, server: str) -> list[str]:
    dig = shutil.which("dig")
    if not dig:
        return []
    res = subprocess.run([dig, f"@{server}", "+time=3", "+tries=1", "+short", name, "TXT"], capture_output=True, text=True, check=False)
    return [line.strip().strip('"') for line in res.stdout.splitlines()]


def txt_visible(name: str, value: str, zone: str) -> bool:
    """Let's Encrypt asks the zone's own servers; ask them too (dig), else 1.1.1.1."""
    servers = _authoritative(zone) if shutil.which("dig") else []
    if servers:
        return all(value in _dig_txt(name, ns) for ns in servers[:2])
    return value in _doh(name, "TXT")


# --- the whole thing -------------------------------------------------------------------


def ensure_cert(
    host: str,
    zone: str,
    set_txt: Callable[[str], None],
    clear_txt: Callable[[], None],
    email: str = "",
    home: Path | None = None,
    directory: str = DIRECTORY,
    log: Callable[[str], None] = lambda _msg: None,
) -> tuple[Path, Path]:
    """(fullchain, key) for host, issuing a new one when missing or near expiry."""
    cert, key_path = cert_paths(host, home)
    if days_left(cert) > RENEW_DAYS and key_path.is_file():
        return cert, key_path
    log(f"Pidiendo certificado HTTPS para {host}…")
    client = Client(_load_or_make_key(tls_dir(home) / "account.key"), directory)
    client.account(email)
    order, headers = client.post(client.dir["newOrder"], {"identifiers": [{"type": "dns", "value": host}]})
    assert isinstance(order, dict)
    order_url = headers.get("Location", "")
    record = f"_acme-challenge.{host}"
    try:
        for authz_url in order.get("authorizations") or []:
            authz, _h = client.post(authz_url, None)
            assert isinstance(authz, dict)
            if authz.get("status") == "valid":
                continue
            chall = next((c for c in authz.get("challenges") or [] if c.get("type") == "dns-01"), None)
            if chall is None:
                raise AcmeError("Let's Encrypt no ofreció el reto DNS-01")
            value = txt_value(chall["token"], client.jwk)
            set_txt(value)
            log("Esperando que el registro TXT se publique…")
            deadline = time.time() + PROPAGATION_S
            while not txt_visible(record, value, zone):
                if time.time() > deadline:
                    raise AcmeError("el registro TXT no apareció en el DNS a tiempo")
                time.sleep(10)
            client.post(chall["url"], {})
            client.poll(authz_url, ("valid",))
    finally:
        try:
            clear_txt()
        except Exception:
            pass
    cert_key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(cert_key, hashes.SHA256())
    )
    client.post(order["finalize"], {"csr": b64(csr.public_bytes(serialization.Encoding.DER))})
    done = client.poll(order_url, ("valid",), timeout=180)
    chain, _h = client.post(done["certificate"], None)
    if not isinstance(chain, str) or "BEGIN CERTIFICATE" not in chain:
        raise AcmeError("Let's Encrypt no devolvió el certificado")
    _write_private(
        key_path,
        cert_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
    )
    _write_private(cert, chain)
    log(f"Certificado listo (vence en {days_left(cert):.0f} días).")
    return cert, key_path
