"""Reserve a paste path, then write the uploaded bytes onto it."""

from __future__ import annotations

import os
import re
from pathlib import Path

MAX_PASTE_BYTES = 12 * 1024 * 1024

ALLOWED_EXT = frozenset(
    {
        "webp",
        "jpg",
        "jpeg",
        "png",
        "gif",
        "svg",
        "txt",
        "md",
        "json",
        "csv",
        "pdf",
        "zip",
        "gz",
        "xz",
        "tar",
        "7z",
        "mp4",
        "webm",
        "mp3",
        "wav",
        "bin",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
    }
)

PASTE_NAME_RE = re.compile(r"^paste-[a-f0-9]{8}\.([a-z0-9]{1,8})$")

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


def parse_paste_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise PasteError("bad name")
    match = PASTE_NAME_RE.fullmatch(raw)
    if match is None:
        raise PasteError("bad name")
    ext = match.group(1)
    if ext == "jpeg":
        raise PasteError("bad name")
    if ext not in ALLOWED_EXT:
        raise PasteError("bad name")
    return raw


def reserve_paste_file(name: str, *, home: Path | None = None) -> Path:
    safe = parse_paste_name(name)
    dest = paste_dir(home)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / safe
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise PasteError("exists") from exc
    os.close(fd)
    return path


def write_paste_file(name: str, body: bytes, *, home: Path | None = None) -> Path:
    if not body:
        raise PasteError("empty")
    if len(body) > MAX_PASTE_BYTES:
        raise PasteError("too large")
    safe = parse_paste_name(name)
    dest = paste_dir(home)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / safe
    path.write_bytes(body)
    return path
