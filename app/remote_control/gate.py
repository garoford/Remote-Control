"""Optional tunnel password.

The preference lives in ~/.config/remote-control/gate (mode 0600). Each start
writes a separate runtime file with a scrypt hash and a signing key. The
browser cookie is an HMAC of that key, so the password never leaves the host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

COOKIE_NAME = "rc_gate"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_LEN = 32
_TOKEN_MSG = b"rc-gate-v1"

LOGIN_PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Remote Control</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: #011627; color: #d6deeb; }
  body { font: 15px/1.4 ui-sans-serif, system-ui, sans-serif; }
  main {
    min-height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    max-width: 360px;
    margin: 0 auto;
    padding: 24px;
  }
  h1 { margin: 0 0 8px; font-size: 22px; font-weight: 700; }
  .lead { margin: 0 0 20px; color: #7B88A1; font-size: 13px; }
  label { display: block; margin-bottom: 6px; font-size: 12px; font-weight: 600; }
  input {
    width: 100%;
    padding: 10px 12px;
    color: #d6deeb;
    background: #011627;
    border: 1px solid rgba(130, 170, 255, 0.28);
    border-radius: 10px;
    font: inherit;
  }
  input:focus { outline: 2px solid #82AAFF; outline-offset: 1px; }
  button {
    width: 100%;
    min-height: 40px;
    margin-top: 16px;
    color: #011627;
    background: #21C7A8;
    border: 0;
    border-radius: 10px;
    font: inherit;
    font-weight: 700;
    cursor: pointer;
  }
  button:focus-visible { outline: 2px solid #82AAFF; outline-offset: 2px; }
  .err { min-height: 1.2em; margin: 8px 0 0; color: #EF5350; font-size: 13px; }
</style>
</head>
<body>
<main>
  <h1>Remote Control</h1>
  <p class="lead">Escribe la contraseña para usar la terminal.</p>
  <form id="gate">
    <label for="password">Contraseña</label>
    <input id="password" type="password" autocomplete="current-password" autofocus required>
    <p class="err" id="err" role="alert"></p>
    <button type="submit">Entrar</button>
  </form>
</main>
<script>
  var form = document.getElementById("gate");
  var err = document.getElementById("err");
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    err.textContent = "";
    fetch("/rc-login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({password: document.getElementById("password").value})
    }).then(function (res) {
      if (res.ok) { location.replace("/"); return; }
      err.textContent = "Contraseña incorrecta";
    }).catch(function () {
      err.textContent = "Contraseña incorrecta";
    });
  });
</script>
</body>
</html>
""".encode("utf-8")


class GateUsage(Exception):
    pass


def preference_path(home: Path | None = None) -> Path:
    if home is not None:
        return home / ".config" / "remote-control" / "gate"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "remote-control" / "gate"
    return Path.home() / ".config" / "remote-control" / "gate"


def without_spaces(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace())


def password_strength(password: str) -> str:
    """none, weak, ok, or strong. Empty stays open; weak does not block start."""
    if not password:
        return "none"
    if len(password) < 8:
        return "weak"
    classes = 0
    if any(ch.islower() for ch in password):
        classes += 1
    if any(ch.isupper() for ch in password):
        classes += 1
    if any(ch.isdigit() for ch in password):
        classes += 1
    if any(not ch.isalnum() for ch in password):
        classes += 1
    if len(password) >= 10 and classes >= 3:
        return "strong"
    return "ok"


STRENGTH_LABEL = {
    "none": "Sin contraseña",
    "weak": "Débil",
    "ok": "Regular",
    "strong": "Fuerte",
}


def read_password(home: Path | None = None) -> str:
    path = preference_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    if text.endswith("\n"):
        text = text[:-1]
    return text


def write_password(password: str, home: Path | None = None) -> None:
    if any(ch.isspace() for ch in password):
        raise GateUsage("la contraseña no puede llevar espacios")
    path = preference_path(home)
    if not password:
        path.unlink(missing_ok=True)
        return
    _write_private(path, password + "\n")


def consume_gate_args(argv: list[str]) -> tuple[list[str], str | None, bool]:
    """Strip gate flags. Returns (gtk argv, password or None, clear)."""
    if not argv:
        argv = ["remote-control"]
    rest = [argv[0]]
    password: str | None = None
    clear = False
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--clear-password":
            clear = True
            index += 1
            continue
        if arg == "--password":
            if index + 1 >= len(argv):
                raise GateUsage("falta el valor de --password")
            password = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--password="):
            password = arg.split("=", 1)[1]
            index += 1
            continue
        rest.append(arg)
        index += 1
    if password is not None and clear:
        raise GateUsage("--password y --clear-password no van juntos")
    if password is not None and any(ch.isspace() for ch in password):
        raise GateUsage("la contraseña no puede llevar espacios")
    if password == "":
        raise GateUsage("--password necesita una contraseña")
    return rest, password, clear


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_LEN,
    )


@dataclass(frozen=True)
class Gate:
    salt: bytes
    password_hash: bytes
    key: bytes

    def token(self) -> str:
        return hmac.new(self.key, _TOKEN_MSG, hashlib.sha256).hexdigest()

    def check(self, password: str) -> bool:
        digest = _scrypt(password, self.salt)
        return hmac.compare_digest(digest, self.password_hash)

    def cookie_ok(self, header: str) -> bool:
        got = ""
        for part in header.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name == COOKIE_NAME:
                got = value
                break
        expected = self.token()
        if len(got) != len(expected):
            return False
        return hmac.compare_digest(got, expected)


def write_runtime_gate(path: Path, password: str) -> None:
    salt = secrets.token_bytes(16)
    key = secrets.token_bytes(32)
    payload = {
        "hash": _scrypt(password, salt).hex(),
        "salt": salt.hex(),
        "key": key.hex(),
    }
    _write_private(path, json.dumps(payload))


def load_gate(path: Path) -> Gate:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Gate(
        salt=bytes.fromhex(data["salt"]),
        password_hash=bytes.fromhex(data["hash"]),
        key=bytes.fromhex(data["key"]),
    )


def _loopback_host(host: str) -> bool:
    name = host.split(",")[0].strip().lower()
    if name.startswith("["):
        name = name[1:].split("]", 1)[0]
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    return name in {"localhost", "127.0.0.1", "::1"}


def set_cookie(token: str, host: str) -> str:
    parts = [
        f"{COOKIE_NAME}={token}",
        "HttpOnly",
        "SameSite=Lax",
        "Path=/",
        f"Max-Age={COOKIE_MAX_AGE}",
    ]
    # The public tunnel is HTTPS. Loopback (the local Abrir fallback) is HTTP,
    # and a browser would drop a Secure cookie there.
    if not _loopback_host(host):
        parts.insert(2, "Secure")
    return "; ".join(parts)
