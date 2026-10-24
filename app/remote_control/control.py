"""What the desktop window and the terminal UI (remote-control cli) share.

The QR code of the public URL, the local URL, and whether we run inside the
tunnel itself (then switching it off would cut our own connection).
"""

from __future__ import annotations

import os

from .history import TMUX_SOCKET, list_sessions


def inside_tunnel() -> bool:
    """True when this terminal may be the web terminal itself.

    A tab of the tunnel (or anything started from one) is certain. A herdr pane
    is hosted by the herdr server, not by a tab, so it counts when a herdr client
    runs in some tunnel tab: it might be on screen through the web.
    """
    if os.environ.get("RC_KEEPHISTORY"):
        return True
    if f"/{TMUX_SOCKET}," in os.environ.get("TMUX", ""):
        return True
    if os.environ.get("HERDR_ENV"):
        try:
            return any(row["command"] == "herdr" for row in list_sessions())
        except Exception:
            return True
    return False


def local_url(port: int) -> str:
    return f"http://localhost:{port}/"


def _qr(url: str):
    try:
        import segno
    except ImportError:
        return None
    return segno.make(url, error="m", micro=False)


def qr_matrix(url: str, border: int = 2) -> list[list[bool]] | None:
    """Rows of dark (True) / light modules, quiet zone included; None without segno."""
    code = _qr(url)
    if code is None:
        return None
    return [[bool(cell) for cell in row] for row in code.matrix_iter(scale=1, border=border)]


def qr_png(url: str, scale: int = 6) -> bytes | None:
    code = _qr(url)
    if code is None:
        return None
    import io

    out = io.BytesIO()
    code.save(out, kind="png", scale=scale, border=2, dark="#011627", light="#ffffff")
    return out.getvalue()
