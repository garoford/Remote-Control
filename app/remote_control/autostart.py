"""Start the tunnel when the user logs in ("Iniciar con el sistema").

An XDG autostart entry runs `remote-control start` inside the desktop
session, so tab shells get its display, clipboard and agent sockets like a
tunnel switched on from the window. The tunnel comes up headless; the window
and `remote-control cli` show it as on, with its URL.
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from pathlib import Path

from remote_control import APP_ID
from remote_control.gate import generate_password, read_password, without_spaces, write_password

ENTRY_NAME = f"{APP_ID}-autostart.desktop"
# Network may still be coming up right after login.
START_ATTEMPTS = 6
RETRY_S = 15


def entry_path(home: Path | None = None) -> Path:
    if home is None and os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "autostart" / ENTRY_NAME
    return (home or Path.home()) / ".config" / "autostart" / ENTRY_NAME


def start_command(home: Path | None = None) -> str:
    """The installed wrapper, else this very package (a dev checkout)."""
    wrapper = (home or Path.home()) / ".local" / "bin" / "remote-control"
    if wrapper.is_file():
        return f"{shlex.quote(str(wrapper))} start"
    root = Path(__file__).resolve().parent.parent
    return f"env PYTHONPATH={shlex.quote(str(root))} {shlex.quote(sys.executable)} -m remote_control start"


def render(home: Path | None = None) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=Remote Control",
            "Comment=Enciende el túnel de Remote Control al iniciar sesión",
            f"Exec={start_command(home)}",
            f"Icon={APP_ID}",
            "Terminal=false",
            "NoDisplay=true",
            "X-GNOME-Autostart-enabled=true",
            "X-GNOME-Autostart-Delay=5",
            "",
        ]
    )


def is_enabled(home: Path | None = None) -> bool:
    path = entry_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text


def set_enabled(on: bool, home: Path | None = None) -> None:
    path = entry_path(home)
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(render(home), encoding="utf-8")
    os.replace(tmp, path)


def headless_start(attempts: int = START_ATTEMPTS, retry_s: float = RETRY_S) -> int:
    """`remote-control start`: switch the tunnel on without a window.

    A tunnel already on is left alone (its URL and connections stay). With no
    password saved one is generated first, as the window does: a tunnel
    started unattended is never open to anyone with the URL.
    """
    from remote_control.tunnel import TunnelService

    tunnel = TunnelService()
    status = tunnel.status()
    if status.running and status.url:
        print(status.url)
        return 0
    password = without_spaces(read_password())
    if not password:
        password = generate_password()
        write_password(password)
    error = ""
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(retry_s)
        try:
            print(tunnel.start(password))
            return 0
        except Exception as exc:
            error = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            print(f"intento {attempt + 1}: {error}", file=sys.stderr)
    print(f"error: no se pudo encender el túnel: {error}", file=sys.stderr)
    return 1
