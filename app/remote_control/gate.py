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
<meta name="theme-color" content="#011627">
<title>Remote Control</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #011627;
    --card: 11 41 66;
    --text: #d6deeb;
    --muted: #9fb4c8;
    --line: 214 222 235;
    --blue: #82aaff;
    --blue-rgb: 130 170 255;
    --teal: #7fdbca;
    --on: #21c7a8;
    --on-rgb: 33 199 168;
    --red: #ef5350;
    --shade: 1 22 39;
  }
  @media (prefers-color-scheme: light) {
    :root {
      color-scheme: light;
      --bg: #eef1f5;
      --card: 255 255 255;
      --text: #403f53;
      --muted: #69708a;
      --line: 64 63 83;
      --blue: #2f7fd0;
      --blue-rgb: 47 127 208;
      --teal: #2aa298;
      --on: #2aa298;
      --on-rgb: 42 162 152;
      --red: #de3d3b;
      --shade: 64 63 83;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; min-height: 100%; background: var(--bg); color: var(--text); }
  body {
    min-height: 100vh;
    min-height: 100dvh;
    display: grid;
    place-items: center;
    padding: 24px 16px;
    overflow: hidden;
    font: 15px/1.4 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  /* Soft glows behind the glass card */
  body::before, body::after {
    content: "";
    position: fixed;
    z-index: 0;
    width: 60vmax;
    height: 60vmax;
    border-radius: 50%;
    filter: blur(80px);
    opacity: 0.32;
    pointer-events: none;
  }
  body::before { top: -24vmax; left: -18vmax; background: radial-gradient(circle, var(--on), transparent 65%); }
  body::after { right: -22vmax; bottom: -26vmax; background: radial-gradient(circle, var(--blue), transparent 65%); }
  main {
    position: relative;
    z-index: 1;
    width: 100%;
    max-width: 380px;
    padding: 32px 28px 28px;
    border: 1px solid rgb(var(--line) / 0.1);
    border-radius: 22px;
    background: rgb(var(--card) / 0.62);
    -webkit-backdrop-filter: blur(24px) saturate(1.5);
    backdrop-filter: blur(24px) saturate(1.5);
    box-shadow:
      0 1px 0 rgb(255 255 255 / 0.06) inset,
      0 30px 70px -20px rgb(var(--shade) / 0.7);
    animation: rise 420ms cubic-bezier(0.2, 0.9, 0.2, 1) both;
  }
  @keyframes rise { from { opacity: 0; transform: translateY(12px) scale(0.98); } }
  .mark {
    display: grid;
    place-items: center;
    width: 52px;
    height: 52px;
    margin-bottom: 20px;
    border-radius: 15px;
    background: linear-gradient(140deg, var(--on), var(--blue));
    color: #011627;
    box-shadow: 0 10px 24px -8px rgb(var(--on-rgb) / 0.7);
  }
  .mark svg { width: 26px; height: 26px; }
  h1 { margin: 0 0 6px; font-size: 22px; font-weight: 700; letter-spacing: -0.01em; }
  .lead { margin: 0 0 24px; color: var(--muted); font-size: 14px; }
  label { display: block; margin-bottom: 8px; color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: 0.02em; }
  .field {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 46px;
    padding: 0 6px 0 14px;
    border: 1px solid rgb(var(--line) / 0.12);
    border-radius: 12px;
    background: rgb(var(--shade) / 0.35);
    color: var(--muted);
    transition: border-color 140ms ease, box-shadow 140ms ease;
  }
  @media (prefers-color-scheme: light) { .field { background: rgb(255 255 255 / 0.8); } }
  .field:focus-within { border-color: rgb(var(--blue-rgb) / 0.6); box-shadow: 0 0 0 4px rgb(var(--blue-rgb) / 0.16); }
  .field.bad { border-color: var(--red); box-shadow: 0 0 0 4px rgb(239 83 80 / 0.14); animation: shake 320ms ease; }
  @keyframes shake { 20%, 60% { transform: translateX(-5px); } 40%, 80% { transform: translateX(5px); } }
  .field > svg { flex: 0 0 auto; width: 17px; height: 17px; }
  input {
    flex: 1 1 auto;
    min-width: 0;
    height: 100%;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 15px;
    outline: none;
  }
  .eye {
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    padding: 0;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }
  .eye:hover { background: rgb(var(--line) / 0.08); color: var(--text); }
  .eye svg { width: 18px; height: 18px; }
  .eye .off { display: none; }
  .eye[aria-pressed="true"] .on { display: none; }
  .eye[aria-pressed="true"] .off { display: block; }
  .err { min-height: 1.3em; margin: 10px 2px 0; color: var(--red); font-size: 13px; }
  .go {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    width: 100%;
    height: 46px;
    margin-top: 10px;
    border: 0;
    border-radius: 12px;
    background: linear-gradient(135deg, var(--on), color-mix(in srgb, var(--on) 70%, var(--blue)));
    color: #011627;
    font: inherit;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 10px 24px -10px rgb(var(--on-rgb) / 0.8);
    transition: transform 120ms ease, filter 120ms ease, box-shadow 120ms ease;
  }
  .go:hover { filter: brightness(1.06); box-shadow: 0 14px 28px -10px rgb(var(--on-rgb) / 0.9); }
  .go:active { transform: translateY(1px); }
  .go:focus-visible, .eye:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
  .go svg { width: 17px; height: 17px; transition: transform 140ms ease; }
  .go:hover svg { transform: translateX(2px); }
  .go[disabled] { cursor: progress; filter: saturate(0.7); }
  .go[disabled] svg { display: none; }
  .go[disabled]::after {
    content: "";
    width: 16px;
    height: 16px;
    border: 2px solid rgb(1 22 39 / 0.35);
    border-top-color: #011627;
    border-radius: 50%;
    animation: spin 700ms linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .foot { margin: 20px 0 0; color: var(--muted); font-size: 12px; text-align: center; opacity: 0.75; }
  @media (prefers-color-scheme: light) {
    .go, .mark { color: #fff; }
    .go[disabled]::after { border-color: rgb(255 255 255 / 0.4); border-top-color: #fff; }
  }
  @media (prefers-reduced-motion: reduce) { main, .field.bad { animation: none; } }
</style>
</head>
<body>
<main>
  <div class="mark" aria-hidden="true">
    <svg viewBox="0 0 24 24"><path d="M5 7l5 5-5 5M12.5 17H19" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
  </div>
  <h1>Remote Control</h1>
  <p class="lead">Escribe la contraseña para usar la terminal.</p>
  <form id="gate">
    <label for="password">Contraseña</label>
    <div class="field" id="field">
      <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="7" width="10" height="7" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
      <input id="password" type="password" autocomplete="current-password" autofocus required>
      <button class="eye" id="eye" type="button" aria-label="Mostrar contraseña" aria-pressed="false">
        <svg class="on" viewBox="0 0 16 16" aria-hidden="true"><path d="M1.6 8s2.4-4.6 6.4-4.6S14.4 8 14.4 8 12 12.6 8 12.6 1.6 8 1.6 8z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>
        <svg class="off" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 2.5l11 11M6.6 4.6A6 6 0 0 1 8 3.4c4 0 6.4 4.6 6.4 4.6a11 11 0 0 1-1.9 2.4M10.2 11.9A5.6 5.6 0 0 1 8 12.6C4 12.6 1.6 8 1.6 8a11 11 0 0 1 2.3-2.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </div>
    <p class="err" id="err" role="alert"></p>
    <button class="go" id="go" type="submit">
      <span>Entrar</span>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9.5M8.8 4.2L12.6 8l-3.8 3.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
  </form>
  <p class="foot">Se recuerda 30 días en este dispositivo</p>
</main>
<script>
  var form = document.getElementById("gate");
  var err = document.getElementById("err");
  var field = document.getElementById("field");
  var input = document.getElementById("password");
  var go = document.getElementById("go");
  var eye = document.getElementById("eye");
  eye.addEventListener("click", function () {
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    eye.setAttribute("aria-pressed", show ? "true" : "false");
    eye.setAttribute("aria-label", show ? "Ocultar contraseña" : "Mostrar contraseña");
    input.focus();
  });
  input.addEventListener("input", function () {
    field.classList.remove("bad");
    err.textContent = "";
  });
  function fail(text) {
    go.disabled = false;
    err.textContent = text;
    field.classList.remove("bad");
    void field.offsetWidth;
    field.classList.add("bad");
    input.select();
  }
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (go.disabled) return;
    err.textContent = "";
    go.disabled = true;
    fetch("/rc-login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({password: input.value})
    }).then(function (res) {
      if (res.ok) { location.replace(location.pathname + location.search); return; }
      fail("Contraseña incorrecta");
    }).catch(function () {
      fail("No hay conexión con el servidor");
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


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_password(length: int = 16) -> str:
    """Random alphanumeric password with upper, lower and digits (always "strong").

    Look-alike characters (0/O, 1/l/I) are left out so it can be typed from a phone.
    """
    while True:
        text = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if any(c.islower() for c in text) and any(c.isupper() for c in text) and any(c.isdigit() for c in text):
            return text


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


def clear_cookie(host: str) -> str:
    """Logout: the same cookie, already expired. The browser drops it."""
    parts = [f"{COOKIE_NAME}=", "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=0"]
    if not _loopback_host(host):
        parts.insert(2, "Secure")
    return "; ".join(parts)


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
