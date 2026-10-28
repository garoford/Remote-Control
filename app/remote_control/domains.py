"""Your own domain (rc.example.com) instead of a random trycloudflare.com one.

The domain stays at its registrar: name.com, GoDaddy or Namecheap, driven
through their APIs with the user's key (``~/.config/remote-control/domain.json``,
mode 0600). Two ways to serve it, picked by where this runs:

- direct (a server with a public IP of its own, e.g. a VPS): an A record
  points the name at this machine, Let's Encrypt gives it a certificate
  (acme.py, DNS-01 through the same API) and the proxy serves HTTPS itself.
  No Cloudflare in between: less latency and none of its limits.
- tunnel (a PC behind a router or CGNAT): the quick tunnel stays, and the
  name redirects (302) to its address of the moment, refreshed on each start.

API references (checked 2026-10):
- name.com CORE API: https://docs.name.com (OpenAPI: namecom.api.yaml),
  Basic auth user:token, base https://api.name.com/core/v1, TTL >= 300.
- GoDaddy: records in Domains v1 (/v1/domains/{d}/records/{type}/{name}),
  forwarding in Domains v2 (/v2/customers/{customerId}/domains/forwards/{fqdn},
  customerId = the account's customer number). Auth: a Personal Access Token
  (Bearer) or the legacy key + secret (sso-key). TTL >= 600.
- Namecheap XML API: https://api.namecheap.com/xml.response,
  namecheap.domains.getList / dns.getHosts / dns.setHosts (setHosts replaces
  every record, so it is always read-modify-write). The caller's IP must be
  whitelisted in the account (Profile > Tools > API Access).
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .gate import _write_private, preference_path

TIMEOUT = 25
USER_AGENT = "remote-control"
# Shown in the window and the CLI, in this order.
PROVIDERS = {"namecom": "name.com", "godaddy": "GoDaddy", "namecheap": "Namecheap"}
# (key, label, secret, required)
FIELDS = {
    "namecom": [
        ("user", "Usuario de name.com", False, True),
        ("token", "API token", True, True),
    ],
    "godaddy": [
        ("key", "API key o token personal (PAT)", True, True),
        ("secret", "API secret (vacío si usas un PAT)", True, False),
    ],
    "namecheap": [
        ("user", "Usuario de Namecheap", False, True),
        ("key", "API key", True, True),
    ],
}
# Kept in the settings file for older versions; the domain is always served
# directly now (direct_reach decides whether it can be).
MODES = {"auto": "Automático", "direct": "Directo", "tunnel": "Túnel"}
FORWARD_TYPES = ("A", "AAAA", "CNAME")


class DomainError(RuntimeError):
    pass


# --- settings -------------------------------------------------------------------------


@dataclass
class DomainConfig:
    provider: str = ""
    host: str = ""
    # The account's domain the host belongs to (picked from its list).
    zone: str = ""
    creds: dict = field(default_factory=dict)
    mode: str = "auto"
    # Let's Encrypt account contact (optional).
    email: str = ""

    def problems(self) -> list[str]:
        out = []
        if self.provider not in PROVIDERS:
            out.append("elige un proveedor")
            return out
        for key, label, _secret, required in FIELDS[self.provider]:
            if required and not self.creds.get(key):
                out.append(f"falta {label}")
        # An account without a name for this machine (multi: each container
        # has its own, router.py) is fine.
        if self.host and not valid_host(self.host):
            out.append("el dominio no es válido (ej. rc.midominio.com)")
        if self.mode not in MODES:
            out.append("modo desconocido")
        return out


def config_path(home: Path | None = None) -> Path:
    return preference_path(home).with_name("domain.json")


def load_config(home: Path | None = None) -> DomainConfig | None:
    try:
        data = json.loads(config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("provider"):
        return None
    creds = data.get("creds") if isinstance(data.get("creds"), dict) else {}
    return DomainConfig(
        provider=str(data.get("provider", "")),
        host=str(data.get("host", "")).strip().lower().rstrip("."),
        zone=str(data.get("zone", "")).strip().lower().rstrip("."),
        creds={str(k): str(v) for k, v in creds.items()},
        mode=str(data.get("mode") or "auto"),
        email=str(data.get("email", "")),
    )


def save_config(cfg: DomainConfig, home: Path | None = None) -> None:
    cfg.host = cfg.host.strip().lower().rstrip(".")
    cfg.creds = {k: v.strip() for k, v in cfg.creds.items() if v and v.strip()}
    _write_private(config_path(home), json.dumps(asdict(cfg), indent=2) + "\n")


def clear_config(home: Path | None = None) -> None:
    config_path(home).unlink(missing_ok=True)


def valid_host(host: str) -> bool:
    labels = host.split(".")
    if len(labels) < 2 or len(host) > 253:
        return False
    for label in labels:
        if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
            return False
        if not all(c.isalnum() or c == "-" for c in label) or not label.isascii():
            return False
    return not labels[-1].isdigit()


def compose_host(sub: str, zone: str) -> str:
    """rc + midominio.com → rc.midominio.com; an empty sub is the domain itself."""
    sub = sub.strip().lower().strip(".")
    zone = zone.strip().lower().strip(".")
    return f"{sub}.{zone}" if sub else zone


def locate(cfg: DomainConfig, prov: Provider) -> tuple[str, str]:
    """(zone, relative name): the picked domain, or the account's list."""
    if cfg.zone and (cfg.host == cfg.zone or cfg.host.endswith("." + cfg.zone)):
        return split_host(cfg.host, [cfg.zone])
    return split_host(cfg.host, prov.domains())


def split_host(host: str, zones: list[str]) -> tuple[str, str]:
    """(zone, name relative to it, "" for the apex) using the account's own domains."""
    host = host.lower().rstrip(".")
    best = ""
    for zone in zones:
        zone = zone.lower().rstrip(".")
        if (host == zone or host.endswith("." + zone)) and len(zone) > len(best):
            best = zone
    if not best:
        raise DomainError(f"{host} no es de ningún dominio de esta cuenta")
    return best, host[: -len(best)].rstrip(".")


# --- HTTP -------------------------------------------------------------------------------


def _http(method: str, url: str, headers: dict | None = None, body: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DomainError(f"no hay conexión con {urllib.parse.urlparse(url).hostname}: {exc}") from exc


def _api_message(raw: bytes) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace").strip()[:300]
    if isinstance(data, dict):
        parts = [str(data.get(k)) for k in ("message", "details", "detail", "code") if data.get(k)]
        for item in data.get("fields") or data.get("errors") or []:
            if isinstance(item, dict) and item.get("message"):
                parts.append(str(item["message"]))
        if parts:
            return ": ".join(parts)[:300]
    return raw.decode("utf-8", "replace").strip()[:300]


# --- providers --------------------------------------------------------------------------


class Provider:
    label = ""

    def domains(self) -> list[str]:
        raise NotImplementedError

    def set_record(self, zone: str, name: str, rtype: str, value: str, ttl: int = 300) -> None:
        """Make ``value`` the only ``rtype`` record of ``name``."""
        raise NotImplementedError

    def delete_record(self, zone: str, name: str, rtype: str) -> None:
        raise NotImplementedError

    def forward(self, zone: str, name: str, url: str) -> None:
        raise NotImplementedError

    def unforward(self, zone: str, name: str) -> None:
        raise NotImplementedError

    def records_at(self, zone: str, name: str) -> list[tuple[str, str]]:
        """(type, value) of every record ``name`` already has (to not take a used one)."""
        raise NotImplementedError

    # The two ways to serve the name (module docstring).

    def point(self, zone: str, name: str, ip: str) -> None:
        self.unforward(zone, name)
        self.delete_record(zone, name, "CNAME")
        self.set_record(zone, name, "AAAA" if ":" in ip else "A", ip)

    def redirect(self, zone: str, name: str, url: str) -> None:
        for rtype in FORWARD_TYPES:
            self.delete_record(zone, name, rtype)
        self.forward(zone, name, url)

    def _fail(self, what: str, status: int, raw: bytes) -> DomainError:
        if status in (401, 403):
            return DomainError(f"{self.label} rechazó la clave ({status}): {_api_message(raw)}")
        return DomainError(f"{self.label}: no se pudo {what} ({status}): {_api_message(raw)}")


class NameCom(Provider):
    label = "name.com"
    base = "https://api.name.com/core/v1"

    def __init__(self, user: str, token: str) -> None:
        auth = base64.b64encode(f"{user}:{token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

    def _call(self, method: str, path: str, what: str, payload: dict | None = None, ok404: bool = False) -> dict:
        headers = dict(self.headers)
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        status, raw = _http(method, self.base + path, headers, body)
        if status == 404 and ok404:
            return {}
        if status >= 300:
            raise self._fail(what, status, raw)
        try:
            return json.loads(raw) if raw.strip() else {}
        except ValueError:
            return {}

    def _pages(self, path: str, key: str, what: str) -> list[dict]:
        out, page = [], 1
        while page:
            sep = "&" if "?" in path else "?"
            data = self._call("GET", f"{path}{sep}perPage=1000&page={page}", what)
            out += [item for item in data.get(key) or [] if isinstance(item, dict)]
            page = data.get("nextPage") or 0
        return out

    def domains(self) -> list[str]:
        return [d["domainName"] for d in self._pages("/domains", "domains", "listar los dominios") if d.get("domainName")]

    def _records(self, zone: str, name: str, rtype: str) -> list[dict]:
        rows = self._pages(f"/domains/{zone}/records", "records", "leer los registros")
        return [r for r in rows if (r.get("host") or "") == name and r.get("type") == rtype]

    def set_record(self, zone: str, name: str, rtype: str, value: str, ttl: int = 300) -> None:
        body = {"host": name, "type": rtype, "answer": value, "ttl": max(300, ttl)}
        found = self._records(zone, name, rtype)
        if found:
            first = found.pop(0)
            self._call("PUT", f"/domains/{zone}/records/{first['id']}", "cambiar el registro", {**body, "id": first["id"]})
        else:
            self._call("POST", f"/domains/{zone}/records", "crear el registro", body)
        for extra in found:
            self._call("DELETE", f"/domains/{zone}/records/{extra['id']}", "borrar el registro", ok404=True)

    def delete_record(self, zone: str, name: str, rtype: str) -> None:
        for row in self._records(zone, name, rtype):
            self._call("DELETE", f"/domains/{zone}/records/{row['id']}", "borrar el registro", ok404=True)

    def records_at(self, zone: str, name: str) -> list[tuple[str, str]]:
        rows = self._pages(f"/domains/{zone}/records", "records", "leer los registros")
        found = [(r.get("type", ""), r.get("answer", "")) for r in rows if (r.get("host") or "") == name]
        if self._forwarding(zone, name):
            found.append(("URL", "redirección"))
        return found

    def _forwarding(self, zone: str, name: str) -> dict | None:
        rows = self._pages(f"/domains/{zone}/url/forwarding", "urlForwarding", "leer las redirecciones")
        fqdn = f"{name}.{zone}" if name else zone
        for row in rows:
            if (row.get("host") or "").rstrip(".") in (name, fqdn):
                return row
        return None

    def forward(self, zone: str, name: str, url: str) -> None:
        body = {"forwardsTo": url, "type": "302"}
        row = self._forwarding(zone, name)
        if row and row.get("id") is not None:
            self._call("PATCH", f"/urlforwarding/{zone}/{row['id']}", "cambiar la redirección", body)
        else:
            self._call("POST", f"/domains/{zone}/url/forwarding", "crear la redirección", {**body, "host": name})

    def unforward(self, zone: str, name: str) -> None:
        row = self._forwarding(zone, name)
        if row and row.get("id") is not None:
            self._call("DELETE", f"/urlforwarding/{zone}/{row['id']}", "quitar la redirección", ok404=True)

    def redirect(self, zone: str, name: str, url: str) -> None:
        """name.com points the name at its redirect servers with an A record of
        its own, made only when the forwarding is created. So: a forwarding
        whose A is there only changes its target; anything else starts over,
        and name.com makes that A again."""
        row = self._forwarding(zone, name)
        if row and row.get("id") is not None and self._records(zone, name, "A"):
            self._call("PATCH", f"/urlforwarding/{zone}/{row['id']}", "cambiar la redirección", {"forwardsTo": url, "type": "302"})
            return
        self.unforward(zone, name)
        for rtype in FORWARD_TYPES:
            self.delete_record(zone, name, rtype)
        self._call("POST", f"/domains/{zone}/url/forwarding", "crear la redirección", {"forwardsTo": url, "type": "302", "host": name})
        if not self._records(zone, name, "A"):
            raise DomainError(f"name.com creó la redirección pero no el registro A de {name or zone}")


class GoDaddy(Provider):
    label = "GoDaddy"
    base = "https://api.godaddy.com"

    def __init__(self, key: str, secret: str = "", customer: str = "") -> None:
        auth = f"sso-key {key}:{secret}" if secret else f"Bearer {key}"
        self.headers = {"Authorization": auth, "Accept": "application/json"}
        self.customer = customer

    def _call(self, method: str, path: str, what: str, payload=None, ok404: bool = False) -> tuple[int, object]:
        headers = dict(self.headers)
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        status, raw = _http(method, self.base + path, headers, body)
        if status == 404 and ok404:
            return status, None
        if status >= 300:
            raise self._fail(what, status, raw)
        try:
            return status, json.loads(raw) if raw.strip() else None
        except ValueError:
            return status, None

    def domains(self) -> list[str]:
        _status, data = self._call("GET", "/v1/domains?statuses=ACTIVE&limit=1000", "listar los dominios")
        return [d["domain"] for d in data or [] if isinstance(d, dict) and d.get("domain")]

    def set_record(self, zone: str, name: str, rtype: str, value: str, ttl: int = 600) -> None:
        self._call(
            "PUT",
            f"/v1/domains/{zone}/records/{rtype}/{name or '@'}",
            "guardar el registro",
            [{"data": value, "ttl": max(600, ttl)}],
        )

    def delete_record(self, zone: str, name: str, rtype: str) -> None:
        self._call("DELETE", f"/v1/domains/{zone}/records/{rtype}/{name or '@'}", "borrar el registro", ok404=True)

    def records_at(self, zone: str, name: str) -> list[tuple[str, str]]:
        found = []
        for rtype in ("A", "AAAA", "CNAME", "MX", "TXT"):
            _status, data = self._call("GET", f"/v1/domains/{zone}/records/{rtype}/{name or '@'}", "leer los registros", ok404=True)
            found += [(rtype, str(d.get("data", ""))) for d in data or [] if isinstance(d, dict)]
        return found

    def _forward_path(self, zone: str, name: str) -> str:
        fqdn = f"{name}.{zone}" if name else zone
        return f"/v2/customers/{urllib.parse.quote(self.customer)}/domains/forwards/{fqdn}"

    def forward(self, zone: str, name: str, url: str) -> None:
        if not self.customer:
            raise DomainError("GoDaddy pide tu número de cliente para redirigir el dominio (modo túnel)")
        body = {"type": "REDIRECT_TEMPORARY", "url": url}
        status, _data = self._call("PUT", self._forward_path(zone, name), "cambiar la redirección", body, ok404=True)
        if status == 404:
            self._call("POST", self._forward_path(zone, name), "crear la redirección", body)

    def unforward(self, zone: str, name: str) -> None:
        if self.customer:
            self._call("DELETE", self._forward_path(zone, name), "quitar la redirección", ok404=True)


NC_NS = "{http://api.namecheap.com/xml.response}"


class Namecheap(Provider):
    label = "Namecheap"
    base = "https://api.namecheap.com/xml.response"
    redirect_types = ("URL", "URL301", "FRAME")

    def __init__(self, user: str, key: str, client_ip: str = "") -> None:
        self.user = user
        self.key = key
        self._client_ip = client_ip

    def client_ip(self) -> str:
        if not self._client_ip:
            # Namecheap's own echo service: the address its API will see.
            self._client_ip = public_ipv4("https://dynamicdns.park-your-domain.com/getip") or public_ipv4() or ""
        if not self._client_ip:
            raise DomainError("no pude saber la IP pública de esta máquina (Namecheap la pide)")
        return self._client_ip

    def _call(self, command: str, params: dict[str, str], what: str) -> ET.Element:
        form = {
            "ApiUser": self.user,
            "ApiKey": self.key,
            "UserName": self.user,
            "ClientIp": self.client_ip(),
            "Command": command,
            **params,
        }
        body = urllib.parse.urlencode(form).encode()
        status, raw = _http("POST", self.base, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise self._fail(what, status, raw) from None
        if root.get("Status") != "OK":
            errors = [(e.get("Number") or "", (e.text or "").strip()) for e in root.iter(NC_NS + "Error")]
            text = "; ".join(msg for _num, msg in errors) or f"HTTP {status}"
            if any("IP" in msg for _num, msg in errors):
                text += f" (agrega {self._client_ip} a la lista blanca en Profile > Tools > API Access)"
            raise DomainError(f"Namecheap: no se pudo {what}: {text}")
        return root

    def domains(self) -> list[str]:
        out, page = [], 1
        while True:
            root = self._call("namecheap.domains.getList", {"PageSize": "100", "Page": str(page)}, "listar los dominios")
            names = [d.get("Name") for d in root.iter(NC_NS + "Domain") if d.get("Name")]
            out += names
            total = root.find(f".//{NC_NS}TotalItems")
            if len(names) < 100 or total is None or len(out) >= int(total.text or 0):
                return out
            page += 1

    @staticmethod
    def _sld_tld(zone: str) -> dict[str, str]:
        sld, _, tld = zone.partition(".")
        return {"SLD": sld, "TLD": tld}

    def _hosts(self, zone: str) -> tuple[list[dict], str]:
        root = self._call("namecheap.domains.dns.getHosts", self._sld_tld(zone), "leer los registros")
        result = root.find(f".//{NC_NS}DomainDNSGetHostsResult")
        if result is not None and result.get("IsUsingOurDNS", "true").lower() != "true":
            raise DomainError(f"{zone} no usa los DNS de Namecheap (BasicDNS): cámbialo en Namecheap o usa ese proveedor")
        hosts = [
            {k: h.get(k) or "" for k in ("Name", "Type", "Address", "MXPref", "TTL")}
            for h in root.iter(NC_NS + "host")
        ]
        return hosts, (result.get("EmailType") or "") if result is not None else ""

    def _set_hosts(self, zone: str, hosts: list[dict], email_type: str) -> None:
        params = self._sld_tld(zone)
        if email_type:
            # Left out, setHosts would reset the mail settings.
            params["EmailType"] = email_type
        for i, h in enumerate(hosts, 1):
            params[f"HostName{i}"] = h["Name"]
            params[f"RecordType{i}"] = h["Type"]
            params[f"Address{i}"] = h["Address"]
            params[f"TTL{i}"] = h.get("TTL") or "1800"
            if h["Type"] in ("MX", "MXE"):
                params[f"MXPref{i}"] = h.get("MXPref") or "10"
        self._call("namecheap.domains.dns.setHosts", params, "guardar los registros")

    def _replace(self, zone: str, name: str, drop: tuple[str, ...], add: dict | None) -> None:
        name = name or "@"
        hosts, email_type = self._hosts(zone)
        kept = [h for h in hosts if not (h["Name"] == name and h["Type"] in drop)]
        if add is None and len(kept) == len(hosts):
            return
        if add is not None:
            kept.append({"Name": name, **add})
        self._set_hosts(zone, kept, email_type)

    def records_at(self, zone: str, name: str) -> list[tuple[str, str]]:
        hosts, _email = self._hosts(zone)
        return [(h["Type"], h["Address"]) for h in hosts if h["Name"] == (name or "@")]

    def set_record(self, zone: str, name: str, rtype: str, value: str, ttl: int = 300) -> None:
        self._replace(zone, name, (rtype,), {"Type": rtype, "Address": value, "TTL": str(max(60, ttl))})

    def delete_record(self, zone: str, name: str, rtype: str) -> None:
        self._replace(zone, name, (rtype,), None)

    def forward(self, zone: str, name: str, url: str) -> None:
        # URL = unmasked 302 redirect.
        self._replace(zone, name, self.redirect_types, {"Type": "URL", "Address": url, "TTL": "300"})

    def unforward(self, zone: str, name: str) -> None:
        self._replace(zone, name, self.redirect_types, None)

    def redirect(self, zone: str, name: str, url: str) -> None:
        # One setHosts instead of four.
        self._replace(zone, name, FORWARD_TYPES + self.redirect_types, {"Type": "URL", "Address": url, "TTL": "300"})

    def point(self, zone: str, name: str, ip: str) -> None:
        rtype = "AAAA" if ":" in ip else "A"
        self._replace(zone, name, ("CNAME", rtype) + self.redirect_types, {"Type": rtype, "Address": ip, "TTL": "300"})


def provider_for(cfg: DomainConfig) -> Provider:
    c = cfg.creds
    if cfg.provider == "namecom":
        return NameCom(c.get("user", ""), c.get("token", ""))
    if cfg.provider == "godaddy":
        return GoDaddy(c.get("key", ""), c.get("secret", ""), c.get("customer", ""))
    if cfg.provider == "namecheap":
        return Namecheap(c.get("user", ""), c.get("key", ""))
    raise DomainError(f"proveedor desconocido: {cfg.provider}")


# --- where this runs --------------------------------------------------------------------

IP_SERVICES = ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://checkip.amazonaws.com")


def public_ipv4(service: str | None = None) -> str | None:
    for url in (service,) if service else IP_SERVICES:
        try:
            status, raw = _http("GET", url)
        except DomainError:
            continue
        text = raw.decode("ascii", "replace").strip()
        try:
            if status == 200 and ipaddress.ip_address(text).version == 4:
                return text
        except ValueError:
            continue
    return None


def local_addresses() -> set[str]:
    ip = shutil.which("ip")
    if not ip:
        return set()
    try:
        out = subprocess.run([ip, "-j", "addr"], capture_output=True, text=True, timeout=5, check=False).stdout
        data = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()
    return {a.get("local") for iface in data for a in iface.get("addr_info") or [] if a.get("local")}


def has_own_public_ip(public: str | None) -> bool:
    """A server (VPS): its public address sits on one of its own interfaces.

    A PC at home only has a private one; the router (or the ISP's CGNAT)
    holds the public address, so nobody outside can reach it directly.
    """
    return bool(public) and public in local_addresses()


PORT_CHECK = "https://ifconfig.co/port/{port}"
# Where the direct mode listens when 443 is not ours to open (a normal user
# may not bind below 1024): the router forwards its 443 here.
FALLBACK_PORT = 8443


def local_https_port() -> int:
    """443 when this user may open it (root, a server), else FALLBACK_PORT."""
    for port in (443, FALLBACK_PORT):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return FALLBACK_PORT


def port_open_outside(port: int, public: str | None) -> bool:
    """Can the internet open ``port`` on our public IP? (ifconfig.co tries it.)"""
    try:
        status, raw = _http("GET", PORT_CHECK.format(port=port), {"Accept": "application/json"})
        data = json.loads(raw)
    except (DomainError, ValueError):
        return False
    if status != 200 or not isinstance(data, dict):
        return False
    return bool(data.get("reachable")) and (not public or data.get("ip") == public)


def lan_route() -> tuple[str, str]:
    """(this PC's LAN address, the router's), for the port forwarding hint."""
    ip = shutil.which("ip")
    if not ip:
        return "", ""
    try:
        out = subprocess.run([ip, "-j", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=5, check=False).stdout
        route = (json.loads(out or "[]") or [{}])[0]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return "", ""
    return route.get("prefsrc", ""), route.get("gateway", "")


@dataclass
class Reach:
    """Whether the direct mode can work here, and on which ports."""

    ok: bool
    why: str
    local_port: int
    # The port people connect to (443 unless only the fallback is forwarded).
    public_port: int = 443


@dataclass
class Edge:
    """A reverse proxy already on this machine (Coolify's Traefik and the like)
    that takes 80/443: Remote Control hands it a route instead of the ports."""

    name: str  # its container
    dynamic_dir: Path  # where it reads routes from, on this machine
    https: str  # its entry point on 443
    http: str  # and on 80
    resolver: str  # its Let's Encrypt certificate resolver
    gateway: str  # this machine's address on its network (how it reaches us)
    inside: str = "/traefik/dynamic"  # that folder as the proxy sees it
    v3: bool = True  # Traefik 3 rule syntax (HostRegexp takes a regular expression)


def _flag(args: list[str], prefix: str) -> str:
    for arg in args:
        if arg.lower().startswith(prefix.lower()):
            return arg.split("=", 1)[1] if "=" in arg else ""
    return ""


def detect_traefik() -> Edge | None:
    """A running Traefik container with a file provider, both entry points and
    an ACME resolver (how Coolify sets it up); None when there is none."""
    docker = shutil.which("docker")
    if not docker:
        return None
    try:
        out = subprocess.run([docker, "ps", "--format", "{{.Names}}\t{{.Image}}"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = next(((n, image) for line in out.splitlines() for n, _, image in [line.partition("\t")] if "traefik" in (n + image).lower() or n == "coolify-proxy"), None)
    if not found:
        return None
    name, image = found
    try:
        info = json.loads(subprocess.run([docker, "inspect", name], capture_output=True, text=True, timeout=10, check=False).stdout)[0]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    args = [str(a) for a in (info.get("Args") or [])] + [str(a) for a in ((info.get("Config") or {}).get("Cmd") or [])]
    inside = _flag(args, "--providers.file.directory=").rstrip("/")
    entry = {}
    for arg in args:
        found = re.match(r"--entrypoints\.([^.=]+)\.address=:?(\d+)$", arg, re.I)
        if found:
            entry[found.group(2)] = found.group(1)
    resolver = next((m.group(1) for a in args if (m := re.match(r"--certificatesresolvers\.([^.=]+)\.acme", a, re.I))), "")
    dynamic_dir = None
    for mount in info.get("Mounts") or []:
        dest = str(mount.get("Destination") or "").rstrip("/")
        if inside and dest and (inside == dest or inside.startswith(dest + "/")):
            dynamic_dir = Path(str(mount.get("Source"))) / inside[len(dest):].lstrip("/")
    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    gateway = next((str(n.get("Gateway")) for n in networks.values() if n.get("Gateway")), "")
    if not (dynamic_dir and dynamic_dir.is_dir() and "443" in entry and "80" in entry and resolver and gateway):
        return None
    tag = image.rpartition(":")[2] if ":" in image.rpartition("/")[2] else ""
    return Edge(name, dynamic_dir, entry["443"], entry["80"], resolver, gateway, inside, not re.match(r"v?[12]\.", tag))


def port_holder(port: int) -> str:
    """The program listening on ``port`` here ("" if none, or not visible)."""
    ss = shutil.which("ss")
    if not ss:
        return ""
    try:
        out = subprocess.run([ss, "-ltnpH", f"sport = :{port}"], capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    if not out.strip():
        return ""
    found = re.search(r'users:\(\("([^"]+)"', out)
    return found.group(1) if found else "otro programa"


def _ours_on(port: int) -> bool:
    """Our router (or a direct-mode proxy) already serving that port."""
    if port_holder(port) not in ("python3", "python", "remote-control"):
        return False
    from . import router

    return router.service_active()


def direct_reach(public: str | None) -> Reach:
    """A server (own public IP) can when 443 is free or already ours; a PC
    only when its router forwards 443 (or 8443) to it, checked from outside
    while we listen on our port."""
    local = local_https_port()
    own_ip = has_own_public_ip(public)
    edge = detect_traefik()
    if edge:
        # A proxy here owns 80/443 (and their certificates): it gets a route.
        # Its public IP may live on another machine that forwards 80/443 to
        # this one (a VPS used as a gateway, rathole, NAT): then the internet
        # must reach 443 on that IP.
        if own_ip:
            return Reach(True, f"Lo sirve el Traefik del servidor ({edge.name})", local, 443)
        if public and port_open_outside(443, public):
            return Reach(True, f"Lo sirve el Traefik del servidor ({edge.name}), detrás de {public}", local, 443)
        return Reach(False, f"El Traefik del servidor ({edge.name}) no se alcanza desde internet en {public or 'la IP pública'}:443", local)
    if own_ip:
        # Another web server (Traefik, nginx, Caddy…) on 443 would answer
        # instead of ours, with its own certificate.
        if local == 443 or _ours_on(443):
            return Reach(True, f"Servidor con IP pública ({public})", 443, 443)
        # 443 taken: our own port (8443) works if the firewall lets it in;
        # the check below sees whether the internet reaches us there.
        busy = port_holder(443) or "otro programa"
    else:
        busy = ""
    if not public:
        return Reach(False, "No hay conexión para comprobar el puerto", local)
    hits = []
    listener = None
    try:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", local))
        listener.listen(8)
        listener.settimeout(0.5)

        def accept() -> None:
            while listener is not None and listener.fileno() >= 0:
                try:
                    conn, _addr = listener.accept()
                except OSError:
                    continue
                hits.append(1)
                conn.close()

        threading.Thread(target=accept, daemon=True).start()
    except OSError:
        # Taken: our own direct mode is already serving it.
        listener = None
    try:
        for outside in (443, FALLBACK_PORT):
            hits.clear()
            # Open, and (when we listen) it was us who answered, not another box.
            if port_open_outside(outside, public) and (listener is None or hits):
                where = "443" if outside == 443 else f"{outside} (la URL lleva :{outside})"
                if busy:
                    return Reach(True, f"El 443 lo usa {busy}: tu dominio va por el {where}", local, outside)
                return Reach(True, f"El puerto {where} de tu router llega a esta PC", local, outside)
    finally:
        if listener is not None:
            listener.close()
    if busy:
        return Reach(False, f"El puerto 443 lo usa {busy}; abre el {local} en el firewall del servidor o libera el 443", local)
    pc, router = lan_route()
    target = f"{pc}:{local}" if pc else f"esta PC, puerto {local}"
    return Reach(False, f"Abre el puerto 443 de tu router{f' ({router})' if router else ''} hacia {target}", local)


def account_domains(provider: str, creds: dict) -> list[str]:
    """The domains this key can manage, for the picker."""
    if provider not in PROVIDERS:
        raise DomainError("elige un proveedor")
    for key, label, _secret, required in FIELDS[provider]:
        if required and not creds.get(key, "").strip():
            raise DomainError(f"falta {label}")
    cfg = DomainConfig(provider=provider, creds={k: v.strip() for k, v in creds.items()})
    zones = sorted({z.lower() for z in provider_for(cfg).domains()})
    if not zones:
        raise DomainError(f"esta cuenta de {PROVIDERS[provider]} no tiene dominios activos")
    return zones


def check(cfg: DomainConfig) -> str:
    """Try the key and the name; what would happen on start, in words."""
    problems = cfg.problems()
    if problems:
        raise DomainError("; ".join(problems))
    prov = provider_for(cfg)
    zones = [z.lower() for z in prov.domains()]
    if cfg.zone and cfg.zone.lower() not in zones:
        raise DomainError(f"{cfg.zone} no está en esta cuenta")
    if not cfg.host:
        # Just the account (multi: each container picks its name, router.py).
        if not cfg.zone:
            raise DomainError("elige un dominio")
        return f"Clave OK ({cfg.zone})."
    zone, _name = split_host(cfg.host, zones)
    cfg.zone = zone
    return f"Clave OK ({zone})."


# --- on start (tunnel.py) and while running (proxy.py) ----------------------------------


def challenge_name(name: str) -> str:
    return f"_acme-challenge.{name}" if name else "_acme-challenge"


def serve_direct(cfg: DomainConfig, public: str | None, log=lambda _msg: None) -> tuple[Path, Path]:
    """Point the name at this machine and make sure it has a certificate."""
    from . import acme

    if not public:
        raise DomainError("no pude saber la IP pública de esta máquina")
    prov = provider_for(cfg)
    zone, name = locate(cfg, prov)
    log(f"{cfg.host} → {public}")
    prov.point(zone, name, public)
    # *.host too: each local environment gets its own name (devlocal.py).
    prov.point(zone, f"*.{name}" if name else "*", public)
    directory = acme.STAGING if os.environ.get("RC_ACME_STAGING") else acme.DIRECTORY
    return acme.ensure_cert(
        cfg.host,
        zone,
        lambda value: prov.set_record(zone, challenge_name(name), "TXT", value, ttl=300),
        lambda: prov.delete_record(zone, challenge_name(name), "TXT"),
        email=cfg.email,
        directory=directory,
        log=log,
        wildcard=True,
    )



# --- `remote-control domain` (a server over SSH has no window) -----------------------

CLI_USAGE = """uso: remote-control domain [show | check | off | set OPCIONES]

  set --provider namecom|godaddy|namecheap [--host rc.midominio.com]
      [--email tu@correo]
      Sin --host muestra los dominios de la cuenta para elegir uno.
      name.com:  --user USUARIO   (pide el token)
      GoDaddy:   (pide la key y el secret)
      Namecheap: --user USUARIO   (pide la API key)
  Las claves se piden sin eco para que no queden en el historial.
  Solo se guarda si esta máquina es alcanzable desde internet: un servidor
  con IP pública, o una PC con el puerto 443 del router abierto hacia ella.
  Se aplica al encender el túnel (remote-control start).
"""


def _masked(value: str) -> str:
    return value[:3] + "…" if len(value) > 6 else "…"


def cli(argv: list[str]) -> int:
    import argparse
    import getpass
    import sys

    cmd = argv[0] if argv else "show"
    if cmd in ("-h", "--help", "help"):
        print(CLI_USAGE)
        return 0
    if cmd == "off":
        clear_config()
        print("Dominio propio quitado: vuelve la URL de trycloudflare.com.")
        return 0
    if cmd == "show":
        cfg = load_config()
        if cfg is None:
            print("Sin dominio propio (URL de trycloudflare.com).")
            return 0
        print(f"Proveedor: {PROVIDERS.get(cfg.provider, cfg.provider)}\nDominio:   {cfg.host}")
        for key, label, secret, _req in FIELDS.get(cfg.provider, []):
            if cfg.creds.get(key):
                print(f"{label}: {_masked(cfg.creds[key]) if secret else cfg.creds[key]}")
        return 0
    if cmd == "check":
        cfg = load_config()
        if cfg is None:
            print("Sin dominio propio.", file=sys.stderr)
            return 1
        try:
            print(check(cfg))
        except DomainError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        reach = direct_reach(public_ipv4())
        print(("✓ " if reach.ok else "No disponible: ") + reach.why)
        return 0 if reach.ok else 1
    if cmd != "set":
        print(CLI_USAGE, file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="remote-control domain set")
    parser.add_argument("--provider", required=True, choices=list(PROVIDERS))
    parser.add_argument("--host", default="")
    parser.add_argument("--email", default="")
    for key in sorted({k for fields in FIELDS.values() for k, _l, _s, _r in fields}):
        parser.add_argument(f"--{key}", default="")
    args = parser.parse_args(argv[1:])
    creds = {}
    for key, label, secret, required in FIELDS[args.provider]:
        value = getattr(args, key)
        if not value and secret and (required or sys.stdin.isatty()):
            value = getpass.getpass(f"{label}: ")
        if value:
            creds[key] = value
    host, zone = args.host, ""
    if not host:
        try:
            zones = account_domains(args.provider, creds)
        except DomainError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for i, item in enumerate(zones, 1):
            print(f"  {i}. {item}")
        pick = input(f"Dominio [1-{len(zones)}]: ").strip() if len(zones) > 1 else "1"
        if not pick.isdigit() or not 1 <= int(pick) <= len(zones):
            print("error: elige un número de la lista", file=sys.stderr)
            return 2
        zone = zones[int(pick) - 1]
        sub = input(f"Subdominio (@ = {zone} mismo) [rc]: ").strip() or "rc"
        host = compose_host("" if sub == "@" else sub, zone)
    cfg = DomainConfig(provider=args.provider, host=host, zone=zone, creds=creds, mode="direct", email=args.email)
    try:
        message = check(cfg)
    except DomainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    reach = direct_reach(public_ipv4())
    if not reach.ok:
        print(f"error: esta máquina no es alcanzable desde internet. {reach.why}.", file=sys.stderr)
        return 1
    save_config(cfg)
    print(message)
    print("Guardado. Se usa la próxima vez que enciendas el túnel.")
    return 0
