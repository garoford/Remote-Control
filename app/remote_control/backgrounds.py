"""Terminal backgrounds (images and looping videos), shared by every profile.

Files live in ~/.local/share/remote-control/backgrounds, named by a hash of
their bytes, so the same picture uploaded twice is kept once and the browser
can cache it forever. The type comes from the bytes, never from the name.

Videos, uploaded or picked from CATALOG (downloaded the first time someone
picks one), go through ffmpeg: <id>.mp4 (up to 1080p, no sound), a lighter
<id>.light.mp4 for phones and <id>.poster.jpg. <id>.json says whether it is
still being processed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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


# --- looping videos ---------------------------------------------------------------

VIDEO_MAX_BYTES = 200 * 1024 * 1024
VIDEO_MAX_FILES = 30
VIDEO_MAX_SECONDS = 600
CHUNK_MAX = 9 * 1024 * 1024
VIDEO_ID_RE = re.compile(r"^[a-f0-9]{16}$")
TOKEN_RE = re.compile(r"^[a-z0-9]{8,32}$")
# <id>.mp4 / <id>.webm, <id>.light.mp4 / .light.webm, <id>.poster.jpg
VIDEO_FILE_RE = re.compile(r"^([a-f0-9]{16})(\.light)?\.(mp4|webm)$")
VIDEO_MIME = {"mp4": "video/mp4", "webm": "video/webm", "jpg": "image/jpeg"}
# Long side in pixels and bitrate of each version.
FULL = (1920, "4M", "6M")
LIGHT = (1280, "1200k", "1800k")
# Tried in this order; an encoder ffmpeg lists can still fail (NVENC with no GPU).
ENCODERS = (
    ("libx264", "mp4", ["-preset", "veryfast"]),
    ("libopenh264", "mp4", []),
    ("h264_nvenc", "mp4", []),
    ("libvpx-vp9", "webm", ["-deadline", "realtime", "-cpu-used", "8"]),
)



def _mixkit(cid: str, title: str, group: str, vid: int, res: int) -> tuple:
    base = f"https://assets.mixkit.co/videos/{vid}/{vid}"
    return (cid, title, group, f"{base}-{res}.mp4", f"{base}-360.mp4")


def _pixabay(cid: str, title: str, path: str) -> tuple:
    base = f"https://cdn.pixabay.com/video/{path}"
    return (cid, title, "anime", f"{base}_large.mp4", f"{base}_tiny.jpg")


# (id, title, group, video URL, thumbnail source: a small video or a still).
# Free to use: Mixkit Free License, Pixabay Content License. Nothing is
# stored in the repo: the server downloads a clip the first time someone
# picks it.
CATALOG = (
    _mixkit("lluvia-ventana", "Lluvia en la ventana", "lofi", 28085, 720),
    _mixkit("ventana-luces", "Ventana lluviosa", "lofi", 2846, 1080),
    _mixkit("gotas-vidrio", "Gotas en el vidrio", "lofi", 21144, 720),
    _mixkit("gato-ventana", "Gato en la ventana", "lofi", 33951, 720),
    _mixkit("calle-noche", "Calle de noche", "lofi", 6754, 720),
    _mixkit("calle-mojada", "Calle mojada", "lofi", 4331, 1080),
    _pixabay("anime-tren", "Tren de noche", "2025/10/23/311602"),
    _pixabay("anime-gato-libro", "Gato y libro", "2025/10/27/312345"),
    _pixabay("anime-audifonos", "Chica con gato", "2025/04/13/271841"),
    _pixabay("anime-estudio", "Estudiando", "2025/05/03/276498"),
    _pixabay("anime-atardecer", "Atardecer en la ciudad", "2025/11/08/314688"),
    _pixabay("anime-ventana", "Gato en la ventana", "2024/12/11/246243"),
    _pixabay("anime-paraguas", "Lluvia con paraguas", "2025/10/23/311563"),
    _pixabay("anime-lectura", "Leyendo", "2025/08/17/297863"),
    _pixabay("anime-aldea", "Aldea de noche", "2022/01/14/104372-666197751"),
    _pixabay("anime-gato-lampara", "Gato dormido", "2026/04/11/345856"),
    _pixabay("anime-sofa", "Siesta en el sofá", "2024/12/11/246241"),
    _mixkit("olas", "Olas", "naturaleza", 17972, 720),
    _mixkit("atardecer", "Atardecer", "naturaleza", 4205, 1080),
    _mixkit("montanas", "Montañas", "naturaleza", 41545, 1080),
    _mixkit("fogata", "Fogata", "naturaleza", 1243, 1080),
    _mixkit("rio-bosque", "Río en el bosque", "naturaleza", 51585, 1080),
    _mixkit("nubes", "Sobre las nubes", "naturaleza", 2408, 1080),
    _mixkit("tormenta", "Tormenta", "naturaleza", 47948, 720),
    _mixkit("mar", "Mar turquesa", "naturaleza", 42498, 1080),
)
# "Cargar más" hands out the anime clips in blocks from this vetted bank
# (Pixabay blocks servers from its search; its CDN serves the files fine).
# Landscape, no letterbox, no franchise characters.
ANIME_POOL = (
    _pixabay("anime-90408", "Tren al atardecer", "2021/10/01/90408-626004752"),
    _pixabay("anime-91562", "Lago al atardecer", "2021/10/10/91562-629172467"),
    _pixabay("anime-138370", "Danza en el estanque", "2022/11/09/138370-769235235"),
    _pixabay("anime-143782", "Pausa de estudio", "2022/12/21/143782-784138267"),
    _pixabay("anime-179909", "Cuarto de artista", "2023/09/10/179909-862981824"),
    _pixabay("anime-202600", "Paraguas en la lluvia", "2024/03/01/202600-918484146"),
    _pixabay("anime-204056", "Música y apuntes", "2024/03/13/204056-923134012"),
    _pixabay("anime-205733", "Lluvia de otoño", "2024/03/26/205733-927672950"),
    _pixabay("anime-214500", "Estudio nocturno", "2024/05/30/214500"),
    _pixabay("anime-215407", "Lámpara y lluvia", "2024/06/05/215407"),
    _pixabay("anime-229513", "Noche estrellada", "2024/09/03/229513"),
    _pixabay("anime-265641", "Linterna", "2025/03/18/265641"),
    _pixabay("anime-267772", "Audífonos", "2025/03/26/267772"),
    _pixabay("anime-270470", "Bajo un paraguas", "2025/04/07/270470"),
    _pixabay("anime-270562", "Programando", "2025/04/07/270562"),
    _pixabay("anime-285203", "Muro de grafiti", "2025/06/11/285203"),
    _pixabay("anime-285225", "Calle del Fuji", "2025/06/11/285225"),
    _pixabay("anime-285240", "Cielo nocturno", "2025/06/11/285240"),
    _pixabay("anime-285242", "Paseo al atardecer", "2025/06/11/285242"),
    _pixabay("anime-285245", "Contemplación", "2025/06/11/285245"),
    _pixabay("anime-285246", "Vista estrellada", "2025/06/11/285246"),
    _pixabay("anime-270983", "Cabaña acogedora", "2025/04/09/270983"),
    _pixabay("anime-309388", "Tarde con un gato", "2025/10/12/309388"),
    _pixabay("anime-248092", "Juegos retro", "2024/12/21/248092"),
    _pixabay("anime-265816", "Estudio con laptop", "2025/03/18/265816"),
    _pixabay("anime-265815", "Audífonos de noche", "2025/03/18/265815"),
    _pixabay("anime-323002", "Mirada al atardecer", "2025/12/19/323002"),
    _pixabay("anime-243342", "Café nevado", "2024/11/26/243342"),
    _pixabay("anime-379720", "Laptop y gato", "2026/09/29/379720"),
    _pixabay("anime-379718", "Estudio bajo la lluvia", "2026/09/29/379718"),
    _pixabay("anime-376049", "Tormenta en la ventana", "2026/09/13/376049"),
    _pixabay("anime-376046", "Trabajo en pareja", "2026/09/13/376046"),
    _pixabay("anime-204238", "Habitación soleada", "2024/03/15/204238-923909572"),
    _pixabay("anime-270507", "Soñando despierto", "2025/04/07/270507"),
    _pixabay("anime-104373", "Luna brillante", "2022/01/14/104373-666197752"),
    _pixabay("anime-247183", "Navidad lofi", "2024/12/17/247183"),
    _pixabay("anime-296693", "Guitarra en la nieve", "2025/08/11/296693"),
    _pixabay("anime-359835", "Siesta con gato", "2026/06/21/359835"),
    _pixabay("anime-246242", "Gato junto al fuego", "2024/12/11/246242"),
    _pixabay("anime-376431", "Balcón de noche", "2026/09/15/376431"),
    _pixabay("anime-379717", "Estudio con gato", "2026/09/29/379717"),
    _pixabay("anime-376047", "Atardecer en el escritorio", "2026/09/13/376047"),
    _pixabay("anime-376044", "Noche de trabajo lluviosa", "2026/09/13/376044"),
    _pixabay("anime-330219", "Restaurante al atardecer", "2026/01/25/330219"),
    _pixabay("anime-376051", "Restaurante del lago", "2026/09/13/376051"),
    _pixabay("anime-331618", "Café de noche", "2026/02/01/331618"),
)
# "Cargar más" for the rest searches Mixkit, tag after tag, page after page.
MIXKIT_TAGS = {
    "lofi": ("rain-window", "night-city", "rain", "city-lights", "coffee-shop", "neon-lights"),
    "naturaleza": ("nature", "forest", "ocean", "clouds", "waterfall", "mountain", "beach", "sunset", "snow", "river"),
}
MORE_BLOCK = 9
# Pages read per "Cargar más" at most: a tag full of clips we already have
# should not keep the request going for long.
MORE_MAX_PAGES = 6
MIXKIT_PAGES_PER_TAG = 5
USER_AGENT = "Mozilla/5.0 Remote-Control"
THUMB_MAX_BYTES = 20 * 1024 * 1024
# JPEG wants full-range color; some clips (Pixabay's) are tagged limited range.
STILL_FILTER = "out_range=full,format=yuvj420p"

_jobs: set[str] = set()
_downloads: dict[str, float] = {}
_jobs_lock = threading.Lock()
# One ffmpeg at a time: the tunnel's machine is also someone's computer.
_encode_lock = threading.Lock()
_thumb_lock = threading.Lock()


def _catalog_item(cid: str) -> tuple | None:
    return next((item for item in _all_clips() if item[0] == cid), None)


# --- more clips on demand ("Cargar más") -------------------------------------------
# discovered.json: {"items": [[id, title, group, url, thumb], ...],
#                   "cursor": {group: [tag index, page]}, "done": [group, ...]}
_discover_lock = threading.Lock()


def _discovered() -> dict:
    try:
        data = json.loads((folder() / "discovered.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    items = [tuple(i) for i in data.get("items") or [] if isinstance(i, list) and len(i) == 5 and all(isinstance(x, str) for x in i)]
    cursor = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}
    done = [g for g in data.get("done") or [] if isinstance(g, str)]
    return {"items": items, "cursor": cursor, "done": done}


def _save_discovered(data: dict) -> None:
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / ".discovered.json.tmp"
    tmp.write_text(json.dumps({"items": [list(i) for i in data["items"]], "cursor": data["cursor"], "done": data["done"]}), encoding="utf-8")
    os.replace(tmp, base / "discovered.json")


def _all_clips() -> list[tuple]:
    return list(CATALOG) + _discovered()["items"]


def more_available() -> dict[str, bool]:
    done = set(_discovered()["done"])
    groups = {item[2] for item in CATALOG}
    return {g: g not in done for g in sorted(groups)}


def mixkit_cards(html: str) -> list[tuple[str, str, bool]]:
    """(id, title, landscape) of each clip on a Mixkit listing page."""
    cards = []
    for chunk in html.split('data-item-grid--video-player-item-id-value="')[1:]:
        vid = chunk.split('"', 1)[0]
        if not vid.isdigit():
            continue
        # Masonry pages have an overlay title; grid pages only the link and the alt text.
        title = (
            re.search(r'overlay-video-title">\s*(.*?)\s*<', chunk, re.S)
            or re.search(r'overlay-link">\s*(.*?)\s*</a>', chunk, re.S)
            or re.search(r'alt="([^"]+?)\.?"', chunk)
        )
        size = re.search(r'width="(\d+)" height="(\d+)"', chunk)
        landscape = not size or int(size.group(1)) > int(size.group(2)) * 1.3
        cards.append((vid, html_unescape(title.group(1)) if title else f"Clip {vid}", landscape))
    return cards


def html_unescape(text: str) -> str:
    import html

    return " ".join(html.unescape(text).split())[:60]


def _mixkit_video_url(vid: str) -> str:
    """The best of 1080p / 720p that exists for a Mixkit clip, or ""."""
    for res in (1080, 720):
        url = f"https://assets.mixkit.co/videos/{vid}/{vid}-{res}.mp4"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                size = int(resp.headers.get("Content-Length") or 0)
            if 0 < size <= VIDEO_MAX_BYTES:
                return url
        except (OSError, ValueError):
            continue
    return ""


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read(4 * 1024 * 1024).decode("utf-8", "replace")


def discover_more(group: str) -> dict:
    """Add the next MORE_BLOCK clips of a group to the catalog."""
    if group not in more_available():
        raise BackgroundError("Esa categoría no existe")
    with _discover_lock:
        data = _discovered()
        known = {item[3] for item in list(CATALOG) + data["items"]} | {item[0] for item in list(CATALOG) + data["items"]}
        added: list[tuple] = []
        if group == "anime":
            for item in ANIME_POOL:
                if len(added) >= MORE_BLOCK:
                    break
                if item[0] not in known and item[3] not in known:
                    added.append(item)
            left = [i for i in ANIME_POOL if i[0] not in known and i not in added]
            if not left and group not in data["done"]:
                data["done"].append(group)
        else:
            tags = MIXKIT_TAGS.get(group, ())
            tag_i, page = (data["cursor"].get(group) or [0, 1])[:2]
            pages = 0
            while len(added) < MORE_BLOCK and tag_i < len(tags) and pages < MORE_MAX_PAGES:
                pages += 1
                try:
                    cards = mixkit_cards(_fetch_text(f"https://mixkit.co/free-stock-video/{tags[tag_i]}/?page={page}"))
                except OSError:
                    cards = []
                fresh = [c for c in cards if c[2] and f"mk-{c[0]}" not in known]
                while fresh and len(added) < MORE_BLOCK:
                    batch, fresh = fresh[: MORE_BLOCK - len(added) + 3], fresh[MORE_BLOCK - len(added) + 3 :]
                    # Which resolution each clip has: HEAD requests, side by side.
                    with ThreadPoolExecutor(max_workers=8) as pool:
                        urls = list(pool.map(_mixkit_video_url, [c[0] for c in batch]))
                    for (vid, title, _), url in zip(batch, urls):
                        if len(added) >= MORE_BLOCK:
                            break
                        if not url or url in known:
                            continue
                        item = (f"mk-{vid}", title, group, url, f"https://assets.mixkit.co/videos/{vid}/{vid}-thumb-360-0.jpg")
                        added.append(item)
                        known.update((item[0], url))
                if not cards or (len(added) < MORE_BLOCK and page >= MIXKIT_PAGES_PER_TAG):
                    tag_i, page = tag_i + 1, 1
                elif len(added) < MORE_BLOCK:
                    page += 1
            data["cursor"][group] = [tag_i, page]
            if tag_i >= len(tags) and group not in data["done"]:
                data["done"].append(group)
        data["items"].extend(added)
        _save_discovered(data)
    return {"ok": True, "added": [item[0] for item in added]}


def sniff_video(head: bytes) -> str:
    """"mp4" (also .mov), "webm" or ""."""
    if head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return ""


def _state_path(vid: str) -> Path:
    return folder() / f"{vid}.json"


def _write_state(vid: str, **state) -> None:
    path = _state_path(vid)
    tmp = path.with_name(f".{vid}.json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def video_state(vid: str) -> dict:
    try:
        state = json.loads(_state_path(vid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    if state.get("state") == "processing":
        with _jobs_lock:
            alive = vid in _jobs
        if not alive:
            # The server restarted halfway through.
            state = {"state": "error", "error": "Se interrumpió; vuelve a subirlo"}
    return state


def video_file(vid: str, which: str) -> Path | None:
    """The file behind /rc-bg/v/<id>/<full|light|poster>."""
    if not VIDEO_ID_RE.match(vid or ""):
        return None
    base = folder()
    if which == "poster":
        path = base / f"{vid}.poster.jpg"
        return path if path.is_file() else _complete_poster(vid)
    if which == "light":
        for ext in ("mp4", "webm"):
            path = base / f"{vid}.light.{ext}"
            if path.is_file():
                return path
        _complete_light(vid)
        which = "full"
    if which == "full":
        for ext in ("mp4", "webm"):
            path = base / f"{vid}.{ext}"
            if path.is_file():
                return path
    return None


def list_videos() -> list[dict]:
    """Uploaded and downloaded videos, newest first: {id, state, progress, error, source}."""
    base = folder()
    if not base.is_dir():
        return []
    found = []
    for item in base.iterdir():
        name = item.name
        if not (name.endswith(".json") and VIDEO_ID_RE.match(name[:-5])):
            continue
        vid = name[:-5]
        state = video_state(vid)
        try:
            mtime = item.stat().st_mtime
        except OSError:
            continue
        found.append(
            (
                mtime,
                {
                    "id": vid,
                    "state": state.get("state") or "error",
                    "progress": float(state.get("progress") or 0),
                    "error": str(state.get("error") or ""),
                    "source": str(state.get("source") or "upload"),
                },
            )
        )
    return [entry for _, entry in sorted(found, key=lambda x: x[0], reverse=True)]


def _catalog_map() -> dict[str, str]:
    try:
        data = json.loads((folder() / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and VIDEO_ID_RE.match(v)} if isinstance(data, dict) else {}


def _save_catalog_map(mapping: dict[str, str]) -> None:
    path = folder() / "catalog.json"
    tmp = path.with_name(".catalog.json.tmp")
    tmp.write_text(json.dumps(mapping), encoding="utf-8")
    os.replace(tmp, path)


def catalog() -> list[dict]:
    """The preset clips: {id, title, group, video, state, progress}."""
    mapping = _catalog_map()
    out = []
    for cid, title, group, _url, _thumb in _all_clips():
        entry = {"id": cid, "title": title, "group": group, "video": "", "state": "", "progress": 0.0}
        with _jobs_lock:
            downloading = _downloads.get(cid)
        if downloading is not None:
            entry.update(state="downloading", progress=downloading)
        elif cid in mapping:
            state = video_state(mapping[cid])
            if state.get("state") in ("ready", "processing"):
                entry.update(video=mapping[cid], state=state["state"], progress=float(state.get("progress") or 0))
        out.append(entry)
    return out


def _video_count() -> int:
    return sum(1 for v in list_videos() if v["state"] != "error")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _ingest(src: Path, source: str) -> str:
    """Take a finished upload or download: dedupe by hash, then encode in the background."""
    with open(src, "rb") as fh:
        kind = sniff_video(fh.read(64))
    if not kind:
        src.unlink(missing_ok=True)
        raise BackgroundError("Solo videos MP4, MOV o WebM")
    vid = _hash_file(src)
    state = video_state(vid)
    if state.get("state") in ("ready", "processing") and (state.get("state") == "processing" or video_file(vid, "full")):
        src.unlink(missing_ok=True)
        return vid
    if _video_count() >= VIDEO_MAX_FILES:
        src.unlink(missing_ok=True)
        raise BackgroundError("Hay demasiados videos; borra alguno")
    work = folder() / f".src-{vid}"
    os.replace(src, work)
    with _jobs_lock:
        _jobs.add(vid)
    _write_state(vid, state="processing", progress=0, source=source)
    threading.Thread(target=_process, args=(work, vid, source), daemon=True).start()
    return vid


def upload_chunk(token: str, offset: int, total: int, body: bytes) -> dict:
    """One piece of an uploaded video (pieces come in order, like the file explorer's)."""
    if not TOKEN_RE.match(token or ""):
        raise BackgroundError("Subida no válida")
    if total <= 0 or total > VIDEO_MAX_BYTES:
        raise BackgroundError("El video pasa de 200 MB")
    if len(body) > CHUNK_MAX or offset < 0 or offset + len(body) > total:
        raise BackgroundError("Trozo no válido")
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    part = base / f".up-{token}.part"
    if offset == 0:
        if not sniff_video(body[:64]):
            raise BackgroundError("Solo videos MP4, MOV o WebM")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    else:
        if not part.is_file() or part.stat().st_size != offset:
            raise BackgroundError("La subida se cortó; vuelve a intentarlo")
        fd = os.open(part, os.O_WRONLY | os.O_APPEND)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    if offset + len(body) < total:
        return {"ok": True, "done": False, "received": offset + len(body)}
    return {"ok": True, "done": True, "video": _ingest(part, "upload")}


def fetch_catalog(cid: str) -> dict:
    """Pick a preset clip: ready at once when cached, else download it in the background."""
    item = _catalog_item(cid)
    if item is None:
        raise BackgroundError("Ese fondo no existe")
    vid = _catalog_map().get(cid, "")
    if vid:
        state = video_state(vid)
        if state.get("state") == "processing" or (state.get("state") == "ready" and video_file(vid, "full")):
            return {"ok": True, "video": vid, "state": state["state"]}
    with _jobs_lock:
        if cid in _downloads:
            return {"ok": True, "video": "", "state": "downloading"}
        _downloads[cid] = 0.0
    threading.Thread(target=_download, args=(item,), daemon=True).start()
    return {"ok": True, "video": "", "state": "downloading"}


def _download(item: tuple) -> None:
    cid = item[0]
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    part = base / f".dl-{cid}.part"
    try:
        req = urllib.request.Request(item[3], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp, open(part, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                got += len(block)
                if got > VIDEO_MAX_BYTES:
                    raise BackgroundError("El video pasa de 200 MB")
                fh.write(block)
                if total:
                    with _jobs_lock:
                        _downloads[cid] = min(1.0, got / total)
        vid = _ingest(part, cid)
        mapping = _catalog_map()
        mapping[cid] = vid
        _save_catalog_map(mapping)
    except (OSError, ValueError, BackgroundError):
        part.unlink(missing_ok=True)
    finally:
        with _jobs_lock:
            _downloads.pop(cid, None)


def catalog_thumb(cid: str) -> Path | None:
    """A still of a preset clip, made once from its small preview or still.

    The source is downloaded first: some CDNs (Pixabay's) break ffmpeg's
    seeking over HTTP.
    """
    item = _catalog_item(cid)
    if item is None:
        return None
    path = folder() / f"thumb-{cid}.jpg"
    if path.is_file():
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        # Servers and containers may have no ffmpeg: a still, scaled by Pillow.
        return _still_thumb(cid, item[4], path)
    with _thumb_lock:
        if path.is_file():
            return path
        folder().mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".thumb-{cid}.jpg")
        src = path.with_name(f".thumb-{cid}.src")
        seek = [] if item[4].endswith(".jpg") else ["-ss", "2"]
        cmd = [ffmpeg, "-v", "error", "-y", *seek, "-i", str(src), "-frames:v", "1", "-vf", f"scale=320:-2:{STILL_FILTER}", "-q:v", "5", "-f", "image2", str(tmp)]
        try:
            _fetch(item[4], src, THUMB_MAX_BYTES)
            subprocess.run(cmd, capture_output=True, timeout=30, check=True)
            os.replace(tmp, path)
        except (OSError, ValueError, subprocess.SubprocessError, BackgroundError):
            tmp.unlink(missing_ok=True)
            return None
        finally:
            src.unlink(missing_ok=True)
    return path


MIXKIT_CLIP = re.compile(r"^(https://assets\.mixkit\.co/videos/(\d+)/)\2-\d+\.mp4$")


def still_source(url: str) -> str | None:
    """An image for that preview: itself, or Mixkit's still of the same clip."""
    if url.lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png", ".webp")):
        return url
    found = MIXKIT_CLIP.match(url)
    return f"{found.group(1)}{found.group(2)}-thumb-360-0.jpg" if found else None


def _still_thumb(cid: str, url: str, path: Path) -> Path | None:
    source = still_source(url)
    if source is None:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    with _thumb_lock:
        if path.is_file():
            return path
        folder().mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".thumb-{cid}.jpg")
        src = path.with_name(f".thumb-{cid}.src")
        try:
            _fetch(source, src, THUMB_MAX_BYTES)
            with Image.open(src) as img:
                img = img.convert("RGB")
                img.thumbnail((320, 320 * img.height // max(1, img.width)))
                img.save(tmp, "JPEG", quality=78)
            os.replace(tmp, path)
        except (OSError, ValueError, BackgroundError):
            tmp.unlink(missing_ok=True)
            return None
        finally:
            src.unlink(missing_ok=True)
    return path


def _fetch(url: str, dest: Path, limit: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    got = 0
    with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as fh:
        while block := resp.read(1 << 20):
            got += len(block)
            if got > limit:
                raise BackgroundError("Archivo demasiado grande")
            fh.write(block)


_encoders_seen: list[str] | None = None


def encoders(ffmpeg: str) -> list[tuple]:
    """ENCODERS this ffmpeg has, in order of preference."""
    global _encoders_seen
    if _encoders_seen is None:
        try:
            out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _encoders_seen = re.findall(r"^\s*V\S*\s+(\S+)", out, re.M)
    return [enc for enc in ENCODERS if enc[0] in _encoders_seen]


def _probe_duration(src: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(src)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        info = json.loads(out.stdout or "{}")
    except ValueError:
        info = {}
    if not info.get("streams"):
        raise BackgroundError("Ese archivo no tiene video")
    try:
        return float((info.get("format") or {}).get("duration") or 0)
    except ValueError:
        return 0.0


def scale_filter(long_side: int) -> str:
    """Fit the long side in long_side pixels, never upscale, even sizes."""
    return (
        f"scale='if(gte(iw,ih),min({long_side},iw),-2)':'if(gte(iw,ih),-2,min({long_side},ih))',"
        "format=yuv420p"
    )


def encode_cmd(ffmpeg: str, src: Path, dest: Path, enc: tuple, size: tuple) -> list[str]:
    long_side, rate, maxrate = size
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src), "-an", "-sn", "-dn", "-vf", scale_filter(long_side)]
    cmd += ["-c:v", enc[0], *enc[2], "-b:v", rate, "-maxrate", maxrate, "-bufsize", maxrate]
    if enc[1] == "mp4":
        cmd += ["-movflags", "+faststart"]
    cmd += ["-progress", "pipe:1", "-nostats", "-f", enc[1], str(dest)]
    return cmd


def _run_encode(cmd: list[str], vid: str, source: str, duration: float, lo: float, hi: float) -> bool:
    last = 0.0
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as proc:
        for line in proc.stdout or []:
            if not line.startswith("out_time_us=") or duration <= 0:
                continue
            try:
                done = int(line.split("=", 1)[1]) / 1e6 / duration
            except ValueError:
                continue
            now = time.monotonic()
            if now - last > 0.5:
                last = now
                _write_state(vid, state="processing", progress=round(lo + (hi - lo) * max(0.0, min(1.0, done)), 3), source=source)
    return proc.returncode == 0


def _process(work: Path, vid: str, source: str) -> None:
    base = folder()
    made: list[Path] = []
    try:
        with _encode_lock:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                # No ffmpeg: a browser-ready file is kept as it is.
                with open(work, "rb") as fh:
                    head = fh.read(64)
                kind = sniff_video(head)
                if kind == "mp4" and head[8:12] == b"qt  ":
                    raise BackgroundError("Para videos MOV hace falta ffmpeg")
                os.replace(work, base / f"{vid}.{kind}")
                _write_state(vid, state="ready", progress=1, source=source)
                return
            duration = _probe_duration(work)
            if duration > VIDEO_MAX_SECONDS:
                raise BackgroundError("El video dura más de 10 minutos")
            for enc in encoders(ffmpeg):
                full = base / f".{vid}.full.{enc[1]}"
                light = base / f".{vid}.light.{enc[1]}"
                made = [full, light]
                if _run_encode(encode_cmd(ffmpeg, work, full, enc, FULL), vid, source, duration, 0.0, 0.65) and _run_encode(
                    encode_cmd(ffmpeg, work, light, enc, LIGHT), vid, source, duration, 0.65, 0.95
                ):
                    break
                for path in made:
                    path.unlink(missing_ok=True)
            else:
                raise BackgroundError("ffmpeg no pudo convertir el video")
            poster = base / f".{vid}.poster.jpg"
            made.append(poster)
            seek = str(min(1.0, duration / 2)) if duration else "0"
            subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-ss", seek, "-i", str(full), "-frames:v", "1", "-vf", f"scale=640:-2:{STILL_FILTER}", "-q:v", "4", "-f", "image2", str(poster)],
                capture_output=True,
                timeout=60,
                check=True,
            )
            os.replace(full, base / f"{vid}.{enc[1]}")
            os.replace(light, base / f"{vid}.light.{enc[1]}")
            os.replace(poster, base / f"{vid}.poster.jpg")
            _write_state(vid, state="ready", progress=1, source=source)
    except (OSError, ValueError, subprocess.SubprocessError, BackgroundError) as exc:
        for path in made:
            path.unlink(missing_ok=True)
        message = str(exc) if isinstance(exc, BackgroundError) else "No se pudo procesar el video"
        _write_state(vid, state="error", progress=0, error=message, source=source)
    finally:
        work.unlink(missing_ok=True)
        with _jobs_lock:
            _jobs.discard(vid)


# A video kept as it came (added while this machine had no ffmpeg) gets what
# it lacks the first time it is asked for: its still now, its light version
# for phones in the background (the full one is served meanwhile).
_completing: set[str] = set()


def _full_file(vid: str) -> Path | None:
    for ext in ("mp4", "webm"):
        path = folder() / f"{vid}.{ext}"
        if path.is_file():
            return path
    return None


def _complete_poster(vid: str) -> Path | None:
    full, ffmpeg = _full_file(vid), shutil.which("ffmpeg")
    if full is None or not ffmpeg:
        return None
    path = folder() / f"{vid}.poster.jpg"
    with _thumb_lock:
        if path.is_file():
            return path
        tmp = folder() / f".{vid}.poster.jpg"
        try:
            subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-ss", "1", "-i", str(full), "-frames:v", "1", "-vf", f"scale=640:-2:{STILL_FILTER}", "-q:v", "4", "-f", "image2", str(tmp)],
                capture_output=True,
                timeout=60,
                check=True,
            )
            os.replace(tmp, path)
        except (OSError, subprocess.SubprocessError):
            tmp.unlink(missing_ok=True)
            return None
    return path


def _complete_light(vid: str) -> None:
    full, ffmpeg = _full_file(vid), shutil.which("ffmpeg")
    if full is None or not ffmpeg:
        return
    with _jobs_lock:
        if vid in _completing or vid in _jobs:
            return
        _completing.add(vid)

    def work() -> None:
        try:
            with _encode_lock:
                for enc in encoders(ffmpeg):
                    tmp = folder() / f".{vid}.light.{enc[1]}"
                    try:
                        done = subprocess.run(encode_cmd(ffmpeg, full, tmp, enc, LIGHT), capture_output=True, timeout=3600, check=False)
                    except (OSError, subprocess.SubprocessError):
                        done = None
                    if done is not None and done.returncode == 0 and tmp.is_file():
                        os.replace(tmp, folder() / f"{vid}.light.{enc[1]}")
                        return
                    tmp.unlink(missing_ok=True)
        finally:
            with _jobs_lock:
                _completing.discard(vid)

    threading.Thread(target=work, daemon=True).start()


def delete_video(vid: str) -> bool:
    if not VIDEO_ID_RE.match(vid or ""):
        return False
    with _jobs_lock:
        if vid in _jobs:
            return False
    base = folder()
    gone = False
    for name in (f"{vid}.mp4", f"{vid}.webm", f"{vid}.light.mp4", f"{vid}.light.webm", f"{vid}.poster.jpg", f"{vid}.json"):
        try:
            (base / name).unlink()
            gone = True
        except FileNotFoundError:
            pass
        except OSError:
            return False
    mapping = _catalog_map()
    if vid in mapping.values():
        _save_catalog_map({k: v for k, v in mapping.items() if v != vid})
    return gone
