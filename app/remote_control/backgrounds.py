"""Terminal background images, shared by every profile.

Files live in ~/.local/share/remote-control/backgrounds, named by a hash of
their bytes, so the same picture uploaded twice is kept once and the browser
can cache it forever. The type comes from the bytes, never from the name.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

MAX_BYTES = 15 * 1024 * 1024
MAX_FILES = 60
NAME_RE = re.compile(r"^[a-f0-9]{16}\.(png|jpg|webp|gif)$")
MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}


class BackgroundError(Exception):
    pass


def folder() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "remote-control" / "backgrounds"


def sniff(body: bytes) -> str:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if body.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def path_of(name: str) -> Path | None:
    if not NAME_RE.match(name or ""):
        return None
    path = folder() / name
    return path if path.is_file() else None


def list_images() -> list[str]:
    base = folder()
    if not base.is_dir():
        return []
    items = []
    for item in base.iterdir():
        if NAME_RE.match(item.name):
            try:
                items.append((item.stat().st_mtime, item.name))
            except OSError:
                continue
    return [name for _, name in sorted(items, reverse=True)]


def save(body: bytes) -> str:
    if not body:
        raise BackgroundError("La imagen está vacía")
    if len(body) > MAX_BYTES:
        raise BackgroundError("La imagen pasa de 15 MB")
    ext = sniff(body)
    if not ext:
        raise BackgroundError("Solo PNG, JPG, WebP o GIF")
    if len(list_images()) >= MAX_FILES:
        raise BackgroundError("Hay demasiadas imágenes; borra alguna")
    name = hashlib.sha256(body).hexdigest()[:16] + "." + ext
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    path = base / name
    if not path.exists():
        tmp = base / ("." + name + ".part")
        tmp.write_bytes(body)
        os.replace(tmp, path)
    else:
        path.touch()
    return name


def delete(name: str) -> bool:
    path = path_of(name)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True
