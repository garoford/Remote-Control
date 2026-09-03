"""Save a clipboard image from the web terminal onto this machine."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

MAX_IMAGE_BYTES = 12 * 1024 * 1024

_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class PasteError(ValueError):
    pass


def sniff_ext(body: bytes, content_type: str = "") -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if body[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    return _TYPES.get(ctype)


def paste_dir(home: Path | None = None) -> Path:
    home = home or Path.home()
    pictures = home / "Pictures"
    if pictures.is_dir():
        return pictures / "Remote Control"
    downloads = home / "Downloads"
    if downloads.is_dir():
        return downloads / "Remote Control"
    return home / ".cache" / "cf-quick-tunnel" / "pastes"


def save_clipboard_image(
    body: bytes,
    content_type: str = "",
    *,
    home: Path | None = None,
    now: datetime | None = None,
) -> Path:
    if not body:
        raise PasteError("empty")
    if len(body) > MAX_IMAGE_BYTES:
        raise PasteError("too large")
    ext = sniff_ext(body, content_type)
    if ext is None:
        raise PasteError("not an image")
    dest = paste_dir(home)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = dest / f"paste-{stamp}{ext}"
    n = 1
    while path.exists():
        n += 1
        path = dest / f"paste-{stamp}-{n}{ext}"
    path.write_bytes(body)
    return path
