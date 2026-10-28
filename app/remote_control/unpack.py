"""Extracting archives from the file explorer (files.py): zip, rar, 7z, tar.*,
single .gz/.bz2/.xz/.zst files and whatever else 7-Zip opens (iso, cab…).

Everything lands in a hidden folder next to the archive first and is moved
into place at the end, so a failure leaves nothing half done and nothing is
ever overwritten: an archive with one folder at its top gives that folder
(«proyecto.zip» → «proyecto/»), anything else a folder named after it.

tar goes through Python's tarfile with its "data" filter (no paths leaving
the folder, no devices, no links out). The rest goes to 7-Zip when it is
installed (it also opens encrypted zips and rar5) and else to zipfile or
unrar. It runs in the background; the page polls its progress.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import zipfile
from pathlib import Path

from .files import FsError, _existing, free_name

JOB_TTL_S = 600
PASSWORD_MAX = 512

TAR_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2", ".tar.xz", ".txz", ".tar.zst", ".tzst", ".tar.lzma", ".tlz",
)
SINGLE = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open, ".lzma": lzma.open, ".zst": None}
SEVEN_ONLY = (".rar", ".7z", ".iso", ".cab", ".arj", ".lzh", ".lha", ".cpio", ".wim", ".dmg", ".vhd", ".vhdx", ".msi", ".chm")
ZIPLIKE = (".zip", ".jar", ".apk", ".xpi", ".epub", ".cbz")
SEVEN_BINS = ("7zz", "7z", "7za")


def kind_of(name: str) -> str:
    """"tar", "zip", "seven", "single" or "" for a file name."""
    low = name.lower()
    if low.endswith(TAR_SUFFIXES):
        return "tar"
    if low.endswith(ZIPLIKE):
        return "zip"
    # Split archives open from their first part.
    if re.search(r"\.(zip|7z|rar)\.0*1$", low) or low.endswith(SEVEN_ONLY) or low.endswith((".cbr", ".cb7")):
        return "seven"
    if any(low.endswith(ext) for ext in SINGLE):
        return "single"
    return ""


def stem_of(name: str) -> str:
    low = name.lower()
    for suffix in sorted(TAR_SUFFIXES + ZIPLIKE + SEVEN_ONLY + tuple(SINGLE) + (".cbr", ".cb7"), key=len, reverse=True):
        if low.endswith(suffix):
            return re.sub(r"\.part0*1$", "", name[: -len(suffix)], flags=re.I) or name
    return re.sub(r"(\.part0*1)?\.(rar|zip|7z)(\.0*1)?$", "", name, flags=re.I) or name


def seven_zip() -> str:
    return next((found for found in map(shutil.which, SEVEN_BINS) if found), "")


def tools() -> dict:
    """What the page can offer (rar and 7z need an outside tool)."""
    return {"seven": bool(seven_zip()), "unrar": bool(shutil.which("unrar"))}


class NeedsPassword(Exception):
    pass


# --- the formats ---------------------------------------------------------------------------


def _tar(src: Path, dest: Path, job: dict) -> None:
    total = max(src.stat().st_size, 1)
    with src.open("rb") as raw:
        low = src.name.lower()
        proc = None
        if low.endswith((".tar.zst", ".tzst")):
            # tarfile reads zstd only from Python 3.14 on.
            exe = shutil.which("zstd") or shutil.which("unzstd")
            if not exe:
                raise FsError("Falta zstd para abrir .tar.zst")
            proc = subprocess.Popen([exe, "-dc"], stdin=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            archive = tarfile.open(fileobj=proc.stdout, mode="r|")
        else:
            archive = tarfile.open(fileobj=raw, mode="r:*")
        try:
            with archive:
                for member in archive:
                    _stop_if_cancelled(job)
                    try:
                        archive.extract(member, dest, filter="data")
                    except tarfile.FilterError:
                        job["skipped"] += 1
                        continue
                    job["files"] += 1
                    job["progress"] = min(raw.tell() / total, 0.99)
        except (tarfile.TarError, EOFError, lzma.LZMAError, OSError) as exc:
            if isinstance(exc, PermissionError):
                raise
            raise FsError("El archivo está dañado o no es un tar: " + str(exc)) from exc
        finally:
            if proc:
                proc.kill()
                proc.wait()


def _zip(src: Path, dest: Path, job: dict, password: str) -> None:
    try:
        archive = zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise FsError("El archivo está dañado o no es un zip") from exc
    with archive:
        members = archive.infolist()
        if any(m.flag_bits & 1 for m in members) and not password:
            raise NeedsPassword()
        pwd = password.encode("utf-8") if password else None
        total = max(sum(m.file_size for m in members), 1)
        done = 0
        root = dest.resolve()
        for member in members:
            _stop_if_cancelled(job)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                # A link stays only when it points inside the folder.
                target = archive.read(member, pwd=pwd).decode("utf-8", "replace")
                where = (dest / member.filename).parent
                if os.path.isabs(target) or not (where / target).resolve().is_relative_to(root):
                    job["skipped"] += 1
                    continue
                link = Path(archive.extract(member, dest, pwd=pwd))
                link.unlink()
                os.symlink(target, link)
                continue
            try:
                out = archive.extract(member, dest, pwd=pwd)
            except RuntimeError as exc:
                if "password" in str(exc).lower():
                    raise NeedsPassword() from exc
                raise FsError("No pude extraerlo: " + str(exc)) from exc
            except NotImplementedError as exc:
                raise FsError("Ese zip usa un método que solo abre 7-Zip (instálalo: 7zip)") from exc
            except (zipfile.BadZipFile, EOFError) as exc:
                raise FsError("El archivo está dañado") from exc
            if mode & 0o111 and not member.is_dir():
                os.chmod(out, (mode & 0o777) | 0o600)
            job["files"] += 1
            done += member.file_size
            job["progress"] = min(done / total, 0.99)


def _single(src: Path, dest: Path, job: dict) -> None:
    low = src.name.lower()
    out = dest / stem_of(src.name)
    total = max(src.stat().st_size, 1)
    try:
        if low.endswith(".zst"):
            exe = shutil.which("zstd") or shutil.which("unzstd")
            if not exe:
                raise FsError("Falta zstd para abrir .zst")
            with out.open("wb") as fh:
                done = subprocess.run([exe, "-dc", str(src)], stdout=fh, stderr=subprocess.PIPE, check=False)
            if done.returncode:
                raise FsError("El archivo está dañado")
        else:
            opener = next(fn for ext, fn in SINGLE.items() if fn and low.endswith(ext))
            with src.open("rb") as raw, opener(raw) as stream, out.open("wb") as fh:
                while chunk := stream.read(1 << 20):
                    _stop_if_cancelled(job)
                    fh.write(chunk)
                    job["progress"] = min(raw.tell() / total, 0.99)
    except (OSError, EOFError, lzma.LZMAError) as exc:
        if isinstance(exc, PermissionError):
            raise
        raise FsError("El archivo está dañado") from exc
    job["files"] = 1


PERCENT_RE = re.compile(rb"(\d{1,3})%")
SEVEN_PASSWORD = ("wrong password", "can not open encrypted archive", "enter password", "encrypted")


def _seven(exe: str, src: Path, dest: Path, job: dict, password: str) -> None:
    # -p always: without it 7-Zip would wait for a password on its stdin.
    cmd = [exe, "x", "-y", "-bso0", "-bsp1", "-bse1", f"-o{dest}", f"-p{password}", "--", str(src)]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    job["proc"] = proc
    out = b""
    assert proc.stdout is not None
    while chunk := proc.stdout.read1(4096):
        out = (out + chunk)[-8192:]
        found = PERCENT_RE.findall(chunk)
        if found:
            job["progress"] = min(int(found[-1]) / 100, 0.99)
    proc.stdout.close()
    code = proc.wait()
    _stop_if_cancelled(job)
    # 1 is a warning (a link pointing out of the folder left out, say).
    if code == 0 or code == 1 and any(dest.iterdir()):
        job["files"] = sum(len(files) for _, _, files in os.walk(dest))
        return
    text = out.decode("utf-8", "replace").lower()
    if any(w in text for w in SEVEN_PASSWORD):
        raise NeedsPassword()
    if "unsupported method" in text and src.name.lower().endswith(".rar") and shutil.which("unrar"):
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir()
        _unrar(src, dest, job, password)
        return
    last = next((line.strip() for line in reversed(text.splitlines()) if line.strip().startswith("error")), "")
    raise FsError("No pude extraerlo" + (": " + last[6:].strip(" :") if last else " (¿archivo dañado?)"))


def _unrar(src: Path, dest: Path, job: dict, password: str) -> None:
    done = subprocess.run(
        ["unrar", "x", "-o-", "-y", f"-p{password}" if password else "-p-", "--", str(src), str(dest) + "/"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=6 * 3600,
        check=False,
    )
    if done.returncode == 0:
        job["files"] = sum(len(files) for _, _, files in os.walk(dest))
        return
    text = (done.stdout + done.stderr).decode("utf-8", "replace").lower()
    if "password" in text or done.returncode == 11:
        raise NeedsPassword()
    raise FsError("No pude extraerlo (¿archivo dañado?)")


# --- jobs ------------------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class _Cancelled(Exception):
    pass


def _stop_if_cancelled(job: dict) -> None:
    if job.get("cancel"):
        raise _Cancelled()


def _sweep() -> None:
    now = time.time()
    with _lock:
        for token, job in list(_jobs.items()):
            if job["state"] != "working" and now - job["made"] > JOB_TTL_S:
                _jobs.pop(token, None)


def start(raw_path: str, password: str = "") -> dict:
    _sweep()
    src = _existing(raw_path)
    if not src.is_file():
        raise FsError("No es un archivo")
    kind = kind_of(src.name)
    if not kind:
        raise FsError("No sé abrir ese tipo de archivo")
    seven = seven_zip()
    if kind == "seven" and not seven and not (src.name.lower().endswith(".rar") and shutil.which("unrar")):
        raise FsError("Para abrir ." + src.name.rsplit(".", 1)[-1].lower() + " hace falta 7-Zip (paquete 7zip)")
    folder = src.parent
    if not os.access(folder, os.W_OK):
        raise FsError("No tienes permiso para escribir en " + str(folder), "403 Forbidden")
    token = secrets.token_hex(12)
    job = {
        "state": "working",
        "name": src.name,
        "progress": 0.0,
        "files": 0,
        "skipped": 0,
        "error": "",
        "path": "",
        "made": time.time(),
    }
    with _lock:
        _jobs[token] = job
    args = (job, src, kind, seven, str(password or "")[:PASSWORD_MAX])
    threading.Thread(target=_work, args=args, daemon=True).start()
    return {"ok": True, "token": token, "name": src.name}


def _place(work: Path, src: Path) -> Path:
    """Moves what came out next to the archive, under a name that is free."""
    inside = list(work.iterdir())
    if len(inside) == 1 and not inside[0].is_symlink():
        target = free_name(src.parent, inside[0].name)
        os.rename(inside[0], target)
        work.rmdir()
        return target
    target = free_name(src.parent, stem_of(src.name))
    os.rename(work, target)
    return target


def _work(job: dict, src: Path, kind: str, seven: str, password: str) -> None:
    work = src.parent / f".{stem_of(src.name)[:80]}.rc-extract-{secrets.token_hex(4)}"
    try:
        work.mkdir(mode=0o755)
        if kind == "tar":
            _tar(src, work, job)
        elif kind == "single":
            _single(src, work, job)
        elif kind == "zip" and not seven:
            _zip(src, work, job, password)
        elif seven:
            _seven(seven, src, work, job, password)
        else:
            _unrar(src, work, job, password)
        if not any(work.iterdir()):
            raise FsError("El archivo está vacío")
        job["path"] = str(_place(work, src))
        job["progress"] = 1.0
        job["state"] = "done"
    except NeedsPassword:
        job["state"] = "password"
        job["error"] = "Contraseña incorrecta" if password else "Este archivo tiene contraseña"
    except _Cancelled:
        job["state"] = "cancelled"
    except FsError as exc:
        job["state"], job["error"] = "error", str(exc)
    except PermissionError:
        job["state"], job["error"] = "error", "Sin permiso para escribir ahí"
    except OSError as exc:
        job["state"], job["error"] = "error", "No pude extraerlo: " + str(exc.strerror or exc)
    finally:
        job.pop("proc", None)
        if job["state"] != "done":
            shutil.rmtree(work, ignore_errors=True)
        job["made"] = time.time()


def status(token: str) -> dict:
    with _lock:
        job = _jobs.get(str(token or ""))
    if not job:
        return {"ok": False, "error": "Esa extracción ya no está"}
    keys = ("state", "name", "progress", "files", "skipped", "error", "path")
    return {"ok": True, **{k: job[k] for k in keys}}


def cancel(token: str) -> dict:
    with _lock:
        job = _jobs.get(str(token or ""))
    if job and job["state"] == "working":
        job["cancel"] = True
        proc = job.get("proc")
        if proc:
            proc.terminate()
    return {"ok": True}
