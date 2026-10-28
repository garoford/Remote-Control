"""File explorer backend: list, preview, search, upload, zip and edit the disk.

The explorer in the page works like Nautilus over the whole disk, limited only
by what this user can read and write. Every route sits behind the tunnel's
password gate like the rest of /rc-.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

LIST_MAX = 20000
SEARCH_MAX = 500
SEARCH_BUDGET_S = 4.0
RECENT_MAX = 50
TEXT_PREVIEW_BYTES = 512 * 1024
THUMB_SIZE = 256
UPLOAD_CHUNK_MAX = 16 * 1024 * 1024
ZIP_TTL_S = 600
NAME_MAX = 255

# Already-compressed formats go into a zip as they are; deflating them only costs time.
STORED_EXT = frozenset(
    {
        "jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "mp4", "mkv", "webm", "mov", "avi",
        "mp3", "m4a", "ogg", "opus", "flac", "aac", "zip", "gz", "tgz", "bz2", "xz", "zst", "7z",
        "rar", "pdf", "docx", "xlsx", "pptx", "odt", "ods", "apk", "jar", "woff2",
    }
)
THUMB_EXT = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "avif", "heic", "ico"})

mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("text/plain", ".log")


class FsError(Exception):
    """A failure the page shows as is (Spanish, user-facing)."""

    def __init__(self, message: str, status: str = "400 Bad Request") -> None:
        super().__init__(message)
        self.status = status


def _home() -> Path:
    return Path.home()


def resolve(raw: str) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise FsError("Falta la ruta")
    if "\0" in text:
        raise FsError("Ruta no válida")
    if text.startswith("file://"):
        text = unquote(urlparse(text).path)
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        path = _home() / path
    return Path(os.path.normpath(str(path)))


def _existing(raw: str) -> Path:
    path = resolve(raw)
    if not os.path.lexists(path):
        raise FsError("No existe: " + str(path), "404 Not Found")
    return path


def _protected(path: Path) -> bool:
    """/ , the home folder and anything above it can never be moved or deleted."""
    home = _home()
    return path == Path("/") or path == home or home.is_relative_to(path)


def _clean_name(raw: str) -> str:
    name = str(raw or "").strip()
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise FsError("Nombre no válido")
    if len(name.encode("utf-8")) > NAME_MAX:
        raise FsError("El nombre es demasiado largo")
    return name


def free_name(folder: Path, name: str) -> Path:
    """name, or "name (1).ext", "name (2).ext"… whichever does not exist yet."""
    target = folder / name
    if not os.path.lexists(target):
        return target
    stem, ext = name, ""
    if "." in name.lstrip("."):
        dot = name.rfind(".")
        stem, ext = name[:dot], name[dot:]
        if stem.endswith(".tar"):
            stem, ext = stem[:-4], ".tar" + ext
    match = re.match(r"^(.*) \((\d+)\)$", stem)
    if match:
        stem = match.group(1)
    n = 1
    while True:
        target = folder / f"{stem} ({n}){ext}"
        if not os.path.lexists(target):
            return target
        n += 1


def _mime(path: Path, is_dir: bool) -> str:
    if is_dir:
        return "inode/directory"
    guess, _ = mimetypes.guess_type(path.name)
    return guess or "application/octet-stream"


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _entry(entry: os.DirEntry) -> dict | None:
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    link = stat.S_ISLNK(st.st_mode)
    target_dir = False
    size = st.st_size
    mtime = st.st_mtime
    broken = False
    if link:
        try:
            full = os.stat(entry.path)
            target_dir = stat.S_ISDIR(full.st_mode)
            size = full.st_size
            mtime = full.st_mtime
        except OSError:
            broken = True
    is_dir = stat.S_ISDIR(st.st_mode) or target_dir
    item = {
        "name": entry.name,
        "kind": "dir" if is_dir else "file",
        "link": link,
        "broken": broken,
        "size": 0 if is_dir else size,
        "mtime": int(mtime),
        "mime": _mime(Path(entry.name), is_dir),
        "hidden": entry.name.startswith("."),
    }
    if not is_dir and _ext(entry.name) in THUMB_EXT:
        item["thumb"] = True
    return item


def list_dir(raw: str) -> dict:
    path = _existing(raw)
    if not path.is_dir():
        raise FsError("No es una carpeta: " + str(path))
    entries = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                item = _entry(entry)
                if item:
                    entries.append(item)
                if len(entries) >= LIST_MAX:
                    break
    except PermissionError:
        raise FsError("No tienes permiso para abrir " + str(path), "403 Forbidden")
    except OSError as exc:
        raise FsError(f"No pude abrir {path}: {exc.strerror or exc}")
    return {
        "path": str(path),
        "parent": str(path.parent) if path != path.parent else "",
        "writable": os.access(path, os.W_OK),
        "entries": entries,
        "truncated": len(entries) >= LIST_MAX,
    }


# --- sidebar --------------------------------------------------------------------

XDG_KEYS = (
    ("DESKTOP", "Escritorio", "desktop"),
    ("DOCUMENTS", "Documentos", "documents"),
    ("DOWNLOAD", "Descargas", "downloads"),
    ("MUSIC", "Música", "music"),
    ("PICTURES", "Imágenes", "pictures"),
    ("VIDEOS", "Vídeos", "videos"),
)


def _user_dirs() -> dict[str, str]:
    home = str(_home())
    out: dict[str, str] = {}
    try:
        text = (_home() / ".config" / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        match = re.match(r'^XDG_([A-Z]+)_DIR="(.*)"\s*$', line.strip())
        if match:
            out[match.group(1)] = match.group(2).replace("$HOME", home)
    return out


def _usage(path: str) -> dict | None:
    try:
        du = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": du.total, "free": du.free}


def places() -> dict:
    home = str(_home())
    dirs = _user_dirs()
    main = [{"name": "Recientes", "path": "recent://", "icon": "recent"}]
    main.append({"name": "Carpeta personal", "path": home, "icon": "home"})
    for key, label, icon in XDG_KEYS:
        path = dirs.get(key) or os.path.join(home, label)
        if os.path.isdir(path) and path != home:
            main.append({"name": label, "path": path, "icon": icon})
    main.append({"name": "Papelera", "path": "trash://", "icon": "trash"})
    marks = [
        {"name": label or os.path.basename(path) or path, "path": path, "icon": "folder", "pinned": True}
        for path, label in _bookmarks()
        if os.path.isdir(path)
    ]
    other = [{"name": "Equipo", "path": "/", "icon": "drive", "usage": _usage("/")}]
    seen = {"/"}
    try:
        mounts = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        mounts = []
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1].encode("latin1").decode("unicode_escape")
        if point in seen or not point.startswith(("/run/media/", "/mnt/", "/media/")):
            continue
        seen.add(point)
        other.append({"name": os.path.basename(point) or point, "path": point, "icon": "drive", "usage": _usage(point)})
    return {"home": home, "main": main, "bookmarks": marks, "other": other}


# Recents: what was opened, previewed, downloaded, uploaded, created or
# extracted here, plus GNOME's own list when there is one (on a server or a
# container nothing writes it, so the explorer keeps its own).
RECENT_KEEP = 200
_recent_lock = threading.Lock()


def _recent_file() -> Path:
    return _home() / ".local" / "share" / "remote-control" / "recent.json"


def _own_recent() -> dict[str, float]:
    try:
        data = json.loads(_recent_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, (int, float))} if isinstance(data, dict) else {}


def note_recent(path: str | Path) -> None:
    """Never fails: a recent not kept is no reason to fail what was asked."""
    path = str(path)
    try:
        with _recent_lock:
            seen = _own_recent()
            seen[path] = time.time()
            kept = dict(sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:RECENT_KEEP])
            target = _recent_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(kept), encoding="utf-8")
            tmp.replace(target)
    except OSError:
        pass


def _gnome_recent() -> dict[str, float]:
    try:
        text = (_home() / ".local" / "share" / "recently-used.xbel").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found: dict[str, float] = {}
    for match in re.finditer(r'<bookmark href="([^"]+)"[^>]*?modified="([^"]+)"', text):
        uri, modified = match.group(1), match.group(2)
        if not uri.startswith("file://"):
            continue
        try:
            when = calendar.timegm(time.strptime(modified[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        path = unquote(urlparse(uri).path)
        found[path] = max(found.get(path, 0.0), float(when))
    return found


# Folders pinned to the sidebar: GTK's bookmarks, the ones Nautilus shows too.

def _bookmarks_file() -> Path:
    return _home() / ".config" / "gtk-3.0" / "bookmarks"


def _bookmarks() -> list[tuple[str, str]]:
    """[(path, label)] in their order; other kinds (sftp://…) are kept by _pin, not listed."""
    try:
        lines = _bookmarks_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    marks = []
    for line in lines:
        uri, _, label = line.strip().partition(" ")
        if uri.startswith("file://"):
            marks.append((unquote(urlparse(uri).path), label))
    return marks


def _pin(paths: list[Path], on: bool, before: str = "") -> None:
    """Pins (at the end, or before that pinned path) or unpins these folders."""
    target = _bookmarks_file()
    try:
        lines = [l for l in target.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        lines = []
    wanted = {str(p) for p in paths}

    def path_of(line: str) -> str:
        uri = line.strip().partition(" ")[0]
        return unquote(urlparse(uri).path) if uri.startswith("file://") else ""

    have = {path_of(l) for l in lines}
    if not on:
        lines = [l for l in lines if path_of(l) not in wanted]
    elif before:
        # Moved there, the pinned ones as well.
        moved = [l for l in lines if path_of(l) in wanted]
        lines = [l for l in lines if path_of(l) not in wanted]
        new = moved + ["file://" + quote(str(p)) for p in paths if str(p) not in have]
        at = next((i for i, l in enumerate(lines) if path_of(l) == before), len(lines))
        lines[at:at] = new
    else:
        lines += ["file://" + quote(str(p)) for p in paths if str(p) not in have]
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise _err(exc, "fijar en la barra lateral")


def recent() -> dict:
    found = _gnome_recent()
    for path, when in _own_recent().items():
        found[path] = max(found.get(path, 0.0), when)
    entries = []
    for path, _ in sorted(found.items(), key=lambda kv: kv[1], reverse=True):
        if len(entries) >= RECENT_MAX:
            break
        if not os.path.exists(path):
            continue
        item = _stat_item(Path(path))
        if item:
            entries.append(item)
    return {"path": "recent://", "parent": "", "writable": False, "entries": entries}


def _stat_item(path: Path) -> dict | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    is_dir = stat.S_ISDIR(st.st_mode)
    item = {
        "name": path.name or str(path),
        "path": str(path),
        "kind": "dir" if is_dir else "file",
        "link": path.is_symlink(),
        "broken": False,
        "size": 0 if is_dir else st.st_size,
        "mtime": int(st.st_mtime),
        "mime": _mime(path, is_dir),
        "hidden": path.name.startswith("."),
    }
    if not is_dir and _ext(path.name) in THUMB_EXT:
        item["thumb"] = True
    return item


# --- trash -----------------------------------------------------------------------
# The freedesktop.org trash, by hand: gio lists trash:/// only with gvfs running
# (containers and servers have none), and moved things into it anyway, so they
# were there and the page never showed them. Same layout as Nautilus: files/ and
# info/<name>.trashinfo in ~/.local/share/Trash, and .Trash-<uid> at the top of
# another disk (a USB, /mnt/…). A trashed item's address is
# trash:///<its path in files/, quoted>.

TRASH_URI = "trash:///"
# A disk of these gets a trash of its own; others (/, /tmp, /boot…) have none.
TRASH_MOUNTS = ("/run/media/", "/media/", "/mnt/")
NO_TRASH_MOUNTS = ("/proc", "/sys", "/dev", "/run/credentials", "/run/user")


class NoTrash(FsError):
    pass


def _home_trash() -> Path:
    return _home() / ".local" / "share" / "Trash"


def _mount_points() -> list[str]:
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return []
    points = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            points.append(parts[1].encode("latin1").decode("unicode_escape"))
    return points


def _top_trash(top: Path, create: bool) -> Path | None:
    uid = os.getuid()
    shared = top / ".Trash"
    try:
        st = os.lstat(shared)
        if stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX and (shared / str(uid)).is_dir():
            return shared / str(uid)
    except OSError:
        pass
    own = top / f".Trash-{uid}"
    if own.is_dir() and not own.is_symlink():
        return own
    if create:
        try:
            own.mkdir(mode=0o700)
            return own
        except OSError:
            return None
    return None


def _trash_dirs() -> list[Path]:
    dirs = [_home_trash()]
    for point in _mount_points():
        if point != "/" and not point.startswith(NO_TRASH_MOUNTS):
            found = _top_trash(Path(point), False)
            if found:
                dirs.append(found)
    return dirs


def _trash_for(path: Path) -> Path:
    """Where this item goes: the home trash on the home's disk, else its disk's own."""
    home_trash = _home_trash()
    try:
        home_trash.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.lstat(path).st_dev == os.stat(home_trash).st_dev:
            return home_trash
    except OSError as exc:
        raise _err(exc, "mover a la papelera " + path.name)
    dev = os.lstat(path).st_dev
    top = path.parent
    while top.parent != top and os.stat(top.parent).st_dev == dev:
        top = top.parent
    if str(top).startswith(TRASH_MOUNTS) or top.is_relative_to(_home()):
        found = _top_trash(top, True)
        if found:
            return found
    raise NoTrash("Aquí no hay papelera")


def _trash_one(path: Path) -> str:
    trash = _trash_for(path)
    files, info = trash / "files", trash / "info"
    files.mkdir(parents=True, exist_ok=True, mode=0o700)
    info.mkdir(parents=True, exist_ok=True, mode=0o700)
    # On another disk the path is relative to it, as the spec asks.
    orig = str(path) if trash == _home_trash() else os.path.relpath(path, trash.parent)
    stem, ext = os.path.splitext(path.name) if path.is_file() else (path.name, "")
    for n in range(1, 10000):
        name = path.name if n == 1 else f"{stem}.{n}{ext}"
        try:
            fd = os.open(info / f"{name}.trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError as exc:
            raise _err(exc, "mover a la papelera " + path.name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"[Trash Info]\nPath={quote(orig)}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        try:
            os.rename(path, files / name)
        except OSError as exc:
            (info / f"{name}.trashinfo").unlink(missing_ok=True)
            raise _err(exc, "mover a la papelera " + path.name)
        return TRASH_URI + quote(str(files / name), safe="")
    raise FsError("La papelera tiene demasiados con ese nombre")


def _trashed(uri: str) -> tuple[Path, Path, Path]:
    """(the item in files/, its .trashinfo, its trash) of a trash:/// address."""
    if not isinstance(uri, str) or not uri.startswith(TRASH_URI):
        raise FsError("No está en la papelera")
    item = Path(os.path.normpath(unquote(uri[len(TRASH_URI):])))
    for trash in _trash_dirs():
        if item.parent == trash / "files" and item.name not in ("", ".", ".."):
            return item, trash / "info" / f"{item.name}.trashinfo", trash
    raise FsError("No está en la papelera")


def _read_info(info: Path, trash: Path) -> tuple[str, int]:
    """(its original path, when it was trashed)."""
    orig, when = "", 0
    try:
        lines = info.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return orig, when
    for line in lines:
        key, _, value = line.partition("=")
        if key == "Path":
            orig = unquote(value.strip())
            if orig and not orig.startswith("/"):
                orig = os.path.normpath(str(trash.parent / orig))
        elif key == "DeletionDate":
            try:
                when = int(time.mktime(time.strptime(value.strip(), "%Y-%m-%dT%H:%M:%S")))
            except ValueError:
                pass
    return orig, when


def trash_list() -> dict:
    entries = []
    for trash in _trash_dirs():
        try:
            names = os.listdir(trash / "files")
        except OSError:
            continue
        for name in names:
            item = trash / "files" / name
            orig, when = _read_info(trash / "info" / f"{name}.trashinfo", trash)
            try:
                st = os.lstat(item)
            except OSError:
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            entries.append(
                {
                    "name": os.path.basename(orig) or name,
                    "path": TRASH_URI + quote(str(item), safe=""),
                    "kind": "dir" if is_dir else "file",
                    "link": stat.S_ISLNK(st.st_mode),
                    "broken": False,
                    "size": 0 if is_dir else st.st_size,
                    "mtime": when or int(st.st_mtime),
                    "mime": _mime(item, is_dir),
                    "hidden": False,
                    "orig": orig,
                }
            )
    return {"path": "trash://", "parent": "", "writable": False, "entries": entries}


def _trash_restore(uri: str) -> str:
    item, info, trash = _trashed(uri)
    orig, _ = _read_info(info, trash)
    if not orig:
        raise FsError("No sé de dónde vino " + item.name)
    target = Path(orig)
    if os.path.lexists(target):
        raise FsError(f"Ya existe «{target.name}» en {target.parent}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(item, target)
    except OSError as exc:
        raise _err(exc, "restaurar " + target.name)
    info.unlink(missing_ok=True)
    return str(target)


def _remove_tree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _trash_delete(uri: str) -> None:
    item, info, _ = _trashed(uri)
    try:
        _remove_tree(item)
    except OSError as exc:
        raise _err(exc, "eliminar " + item.name)
    info.unlink(missing_ok=True)


def _trash_empty() -> None:
    for trash in _trash_dirs():
        for sub in ("files", "info"):
            try:
                children = list((trash / sub).iterdir())
            except OSError:
                continue
            for child in children:
                try:
                    _remove_tree(child)
                except OSError as exc:
                    raise _err(exc, "vaciar la papelera")


# --- search ----------------------------------------------------------------------


def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in norm if not unicodedata.combining(c))


def search(raw: str, query: str, hidden: bool = False) -> dict:
    root = _existing(raw)
    words = _fold(str(query or "")).split()
    if not words:
        return {"path": str(root), "entries": [], "truncated": False}
    started = time.monotonic()
    found = []
    truncated = False
    for folder, dirs, files in os.walk(root, followlinks=False):
        if not hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files = [f for f in files if not f.startswith(".")]
        for name in dirs + files:
            low = _fold(name)
            if all(w in low for w in words):
                item = _stat_item(Path(folder) / name)
                if item:
                    item["where"] = folder
                    found.append(item)
        if len(found) >= SEARCH_MAX or time.monotonic() - started > SEARCH_BUDGET_S:
            truncated = True
            break
    return {"path": str(root), "entries": found[:SEARCH_MAX], "truncated": truncated}


# --- thumbnails (freedesktop cache, shared with Nautilus) -------------------------


def thumb(raw: str) -> Path | None:
    path = _existing(raw)
    if not path.is_file():
        return None
    uri = path.as_uri()
    digest = hashlib.md5(uri.encode("utf-8")).hexdigest()
    cache = _home() / ".cache" / "thumbnails"
    mtime = int(path.stat().st_mtime)
    for size in ("large", "normal", "x-large"):
        cand = cache / size / f"{digest}.png"
        if cand.is_file() and cand.stat().st_mtime >= mtime:
            return cand
    if _ext(path.name) not in THUMB_EXT:
        return None
    try:
        from PIL import Image, ImageOps, PngImagePlugin
    except ImportError:
        return None
    out = cache / "large" / f"{digest}.png"
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((THUMB_SIZE, THUMB_SIZE))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            meta = PngImagePlugin.PngInfo()
            meta.add_text("Thumb::URI", uri)
            meta.add_text("Thumb::MTime", str(mtime))
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(f".{digest}.{secrets.token_hex(3)}.png")
            img.save(tmp, "PNG", pnginfo=meta)
            os.replace(tmp, out)
    except Exception:
        return None
    return out


# --- reading files ------------------------------------------------------------------


def file_for_read(raw: str) -> tuple[Path, int, str]:
    path = _existing(raw)
    if path.is_dir():
        raise FsError("Es una carpeta")
    try:
        size = path.stat().st_size
        with open(path, "rb"):
            pass
    except PermissionError:
        raise FsError("No tienes permiso para leer " + path.name, "403 Forbidden")
    return path, size, _mime(path, False)


def text_preview(raw: str) -> dict:
    path, size, mime = file_for_read(raw)
    with open(path, "rb") as fh:
        data = fh.read(TEXT_PREVIEW_BYTES)
    if b"\0" in data[:8192]:
        return {"binary": True, "size": size}
    return {"binary": False, "size": size, "text": data.decode("utf-8", "replace"), "truncated": size > len(data)}


# --- edits ---------------------------------------------------------------------------


def _folder(raw: str) -> Path:
    path = _existing(raw)
    if not path.is_dir():
        raise FsError("No es una carpeta: " + str(path))
    return path


def _err(exc: OSError, what: str) -> FsError:
    if isinstance(exc, PermissionError):
        return FsError("No tienes permiso para " + what, "403 Forbidden")
    return FsError(f"No pude {what}: {exc.strerror or exc}")


def _copy_one(src: Path, dest_dir: Path) -> Path:
    target = free_name(dest_dir, src.name)
    if src.is_dir() and not src.is_symlink():
        if dest_dir == src or dest_dir.is_relative_to(src):
            raise FsError("No se puede copiar una carpeta dentro de sí misma")
        shutil.copytree(src, target, symlinks=True)
    else:
        shutil.copy2(src, target, follow_symlinks=False)
    return target


def _move_one(src: Path, dest_dir: Path) -> Path:
    if _protected(src):
        raise FsError("Esa carpeta no se puede mover")
    if src.parent == dest_dir:
        return src
    if src.is_dir() and (dest_dir == src or dest_dir.is_relative_to(src)):
        raise FsError("No se puede mover una carpeta dentro de sí misma")
    target = free_name(dest_dir, src.name)
    shutil.move(str(src), str(target))
    return target


def apply_op(op: dict) -> dict:
    kind = str(op.get("op") or "")
    if kind == "mkdirs":
        # An empty folder inside a folder dropped from the PC.
        return {"ok": True, "path": str(_subfolder(_folder(op.get("dir")), str(op.get("sub") or "")))}
    if kind in ("mkdir", "newfile"):
        folder = _folder(op.get("dir"))
        target = free_name(folder, _clean_name(op.get("name") or ("Carpeta nueva" if kind == "mkdir" else "Archivo nuevo")))
        try:
            if kind == "mkdir":
                target.mkdir()
            else:
                target.touch(exist_ok=False)
        except OSError as exc:
            raise _err(exc, "crear " + target.name)
        if kind == "newfile":
            note_recent(target)
        return {"ok": True, "path": str(target)}
    if kind == "rename":
        src = _existing(op.get("path"))
        if _protected(src):
            raise FsError("Esa carpeta no se puede renombrar")
        name = _clean_name(op.get("name"))
        if name == src.name:
            return {"ok": True, "path": str(src)}
        target = src.parent / name
        if os.path.lexists(target) and name.casefold() != src.name.casefold():
            raise FsError(f"Ya existe «{name}» en esta carpeta")
        try:
            os.rename(src, target)
        except OSError as exc:
            raise _err(exc, "renombrar " + src.name)
        return {"ok": True, "path": str(target)}
    if kind in ("copy", "move"):
        dest = _folder(op.get("dest"))
        paths = [_existing(p) for p in (op.get("paths") or [])][:5000]
        if not paths:
            raise FsError("No hay nada que " + ("copiar" if kind == "copy" else "mover"))
        done = []
        for src in paths:
            try:
                done.append(str(_copy_one(src, dest) if kind == "copy" else _move_one(src, dest)))
            except OSError as exc:
                raise _err(exc, ("copiar " if kind == "copy" else "mover ") + src.name)
        return {"ok": True, "paths": done}
    if kind == "trash":
        paths = [_existing(p) for p in (op.get("paths") or [])][:5000]
        if not paths:
            raise FsError("No hay nada que mover a la papelera")
        for src in paths:
            if _protected(src):
                raise FsError("Esa carpeta no se puede mover a la papelera")
        try:
            for src in paths:
                _trash_for(src)
        except NoTrash:
            # /tmp and other system disks have no trash: the page offers
            # deleting for good instead, like Nautilus.
            return {"ok": False, "noTrash": True, "error": "Aquí no hay papelera"}
        return {"ok": True, "trashed": [_trash_one(src) for src in paths]}
    if kind == "pin":
        raw = [p for p in (op.get("paths") or []) if isinstance(p, str)][:100]
        on = bool(op.get("on", True))
        # Unpinning one that no longer exists is fine; pinning asks for a folder.
        paths = [_folder(p) if on else resolve(p) for p in raw]
        if not paths:
            raise FsError("No hay carpetas que fijar")
        _pin(paths, on, str(op.get("before") or ""))
        return {"ok": True}
    if kind == "delete":
        paths = [_existing(p) for p in (op.get("paths") or [])][:5000]
        for src in paths:
            if _protected(src):
                raise FsError("Esa carpeta no se puede eliminar")
        for src in paths:
            try:
                if src.is_dir() and not src.is_symlink():
                    shutil.rmtree(src)
                else:
                    src.unlink()
            except OSError as exc:
                raise _err(exc, "eliminar " + src.name)
        return {"ok": True}
    if kind == "restore":
        uris = [u for u in (op.get("uris") or []) if isinstance(u, str)]
        return {"ok": True, "paths": [_trash_restore(uri) for uri in uris]}
    if kind == "deleteForever":
        for uri in [u for u in (op.get("uris") or []) if isinstance(u, str)]:
            _trash_delete(uri)
        return {"ok": True}
    if kind == "emptyTrash":
        _trash_empty()
        return {"ok": True}
    if kind == "props":
        path = _existing(op.get("path"))
        st = os.lstat(path)
        size, count = 0, 0
        if path.is_dir() and not path.is_symlink():
            started = time.monotonic()
            for folder, dirs, files in os.walk(path):
                count += len(dirs) + len(files)
                for name in files:
                    try:
                        size += os.lstat(os.path.join(folder, name)).st_size
                    except OSError:
                        pass
                if time.monotonic() - started > 3:
                    break
        else:
            size = st.st_size
        try:
            import grp
            import pwd

            owner = pwd.getpwuid(st.st_uid).pw_name
            group = grp.getgrgid(st.st_gid).gr_name
        except (KeyError, ImportError):
            owner, group = str(st.st_uid), str(st.st_gid)
        return {
            "ok": True,
            "path": str(path),
            "name": path.name or str(path),
            "kind": "dir" if path.is_dir() else "file",
            "mime": _mime(path, path.is_dir()),
            "size": size,
            "items": count,
            "mtime": int(st.st_mtime),
            "atime": int(st.st_atime),
            "mode": stat.filemode(st.st_mode),
            "owner": owner,
            "group": group,
            "link": os.readlink(path) if path.is_symlink() else "",
            "usage": _usage(str(path if path.is_dir() else path.parent)),
        }
    raise FsError("Operación desconocida")


# --- uploads in chunks -------------------------------------------------------------------


def _subfolder(folder: Path, sub: str) -> Path:
    """folder/a/b for "a/b" (a folder dropped from the PC keeps its tree),
    made if missing; every part a plain name, so nothing leaves folder."""
    for part in [p for p in str(sub or "").split("/") if p]:
        folder = folder / _clean_name(part)
        try:
            folder.mkdir(exist_ok=True)
        except OSError as exc:
            raise _err(exc, "crear " + part)
        if not folder.is_dir() or folder.is_symlink():
            raise FsError("No es una carpeta: " + str(folder))
    return folder


def upload_chunk(dir_raw: str, name_raw: str, offset: int, total: int, body: bytes, upload_id: str, sub: str = "") -> dict:
    folder = _subfolder(_folder(dir_raw), sub)
    name = _clean_name(name_raw)
    if not re.match(r"^[a-z0-9]{8,32}$", upload_id or ""):
        raise FsError("Subida no válida")
    if len(body) > UPLOAD_CHUNK_MAX or offset < 0 or total < 0 or offset + len(body) > total:
        raise FsError("Trozo no válido")
    part = folder / f".{upload_id}.rcpart"
    try:
        if offset == 0:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        else:
            if not part.is_file() or part.stat().st_size != offset:
                raise FsError("La subida se cortó; vuelve a intentarlo")
            fd = os.open(part, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
    except OSError as exc:
        raise _err(exc, "subir " + name)
    if offset + len(body) < total:
        return {"ok": True, "done": False, "received": offset + len(body)}
    target = free_name(folder, name)
    os.replace(part, target)
    note_recent(target)
    return {"ok": True, "done": True, "path": str(target), "name": target.name}


# --- zip downloads ------------------------------------------------------------------------

# Zipping a big folder can take longer than the tunnel waits for one response,
# so it runs in the background and the page polls its progress.
_zips: dict[str, dict] = {}
_zip_lock = threading.Lock()


def _zip_dir() -> Path:
    base = Path(tempfile.gettempdir()) / f"rc-zip-{os.getuid()}"
    base.mkdir(mode=0o700, exist_ok=True)
    return base


def _sweep_zips() -> None:
    now = time.time()
    with _zip_lock:
        for token, job in list(_zips.items()):
            if job["state"] != "working" and now - job["made"] > ZIP_TTL_S:
                job["path"].unlink(missing_ok=True)
                _zips.pop(token, None)


def _clean_label(raw: str) -> str:
    return re.sub(r"[/\\\0]", "", str(raw or "")).strip()[:80]


def start_zip(raw_paths: list, label: str = "") -> dict:
    _sweep_zips()
    paths = [_existing(p) for p in raw_paths if isinstance(p, str)][:5000]
    if not paths:
        raise FsError("No hay nada que descargar")
    base = Path(os.path.commonpath([str(p.parent) for p in paths]))
    if len(paths) == 1:
        name = (paths[0].name or "archivos") + ".zip"
    else:
        folder = _clean_label(label) or base.name or "archivos"
        name = f"{folder} ({len(paths)} elementos).zip"
    token = secrets.token_hex(16)
    job = {
        "state": "working",
        "name": name,
        "path": _zip_dir() / f"{token}.zip",
        "files": 0,
        "bytes": 0,
        "error": "",
        "made": time.time(),
    }
    with _zip_lock:
        _zips[token] = job
    threading.Thread(target=_zip_worker, args=(job, paths, base), daemon=True).start()
    return {"ok": True, "token": token, "name": name}


def _zip_worker(job: dict, paths: list[Path], base: Path) -> None:
    def add(zf: zipfile.ZipFile, path: Path, arcname: str) -> None:
        method = zipfile.ZIP_STORED if _ext(path.name) in STORED_EXT else zipfile.ZIP_DEFLATED
        try:
            zf.write(path, arcname, compress_type=method, compresslevel=6 if method == zipfile.ZIP_DEFLATED else None)
        except (PermissionError, FileNotFoundError):
            return
        job["files"] += 1
        try:
            job["bytes"] += path.stat().st_size
        except OSError:
            pass

    try:
        with zipfile.ZipFile(job["path"], "w", allowZip64=True) as zf:
            for src in paths:
                if src.is_dir() and not src.is_symlink():
                    for folder, dirs, files in os.walk(src):
                        rel_dir = os.path.relpath(folder, base)
                        if not files and not dirs:
                            zf.writestr(rel_dir.rstrip("/") + "/", b"")
                        for fname in files:
                            full = Path(folder) / fname
                            if full.is_symlink() or not full.is_file():
                                continue
                            add(zf, full, os.path.join(rel_dir, fname))
                elif src.is_file():
                    add(zf, src, os.path.relpath(src, base))
        job["state"] = "done"
    except OSError as exc:
        job["path"].unlink(missing_ok=True)
        job["state"] = "error"
        job["error"] = "No pude comprimir: " + str(exc.strerror or exc)
    job["made"] = time.time()


def zip_status(token: str) -> dict:
    with _zip_lock:
        job = _zips.get(str(token or ""))
    if not job:
        return {"ok": False, "error": "La descarga ya no está disponible"}
    out = {"ok": True, "state": job["state"], "name": job["name"], "files": job["files"], "bytes": job["bytes"], "error": job["error"]}
    if job["state"] == "done":
        out["size"] = job["path"].stat().st_size
    return out


def take_zip(token: str) -> tuple[Path, str] | None:
    with _zip_lock:
        job = _zips.get(str(token or ""))
        if not job or job["state"] != "done":
            return None
        _zips.pop(str(token), None)
    if not job["path"].is_file():
        return None
    return job["path"], job["name"]


def content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "'").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name.encode('utf-8', 'replace'))}"
