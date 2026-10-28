"""Profiles: separate CLI accounts for the people sharing one Remote Control.

There are no passwords and no extra Linux users. The primary user keeps their
HOME as always. Each extra profile is a folder under
~/.local/share/remote-control/profiles/<id>; a new AI CLI (claude, agent,
codex…) started while that profile is active runs with HOME pointing there,
so it logs in and keeps its tokens apart. Nothing that already runs changes,
and herdr always stays the primary user's.

Which profile is active is decided by the device that typed last: the page
pings /rc-profile-ping and the choice is written to ``profile-active``, which
the shell wrapper (web/profiles.zsh) reads when a CLI starts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LABEL_MAX = 40
PROFILES_MAX = 50
SUBDIRS = (".config", ".local/share", ".local/state", ".cache")

_lock = threading.Lock()
_last_active: str | None = None


def base_dir() -> Path:
    return Path.home() / ".local" / "share" / "remote-control"


def profiles_dir() -> Path:
    return base_dir() / "profiles"


def _labels_file() -> Path:
    return profiles_dir() / "profiles.json"


def active_file() -> Path:
    return base_dir() / "profile-active"


def primary_name() -> str:
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, ImportError):
        return os.environ.get("USER") or "principal"


def _read_labels() -> dict[str, str]:
    try:
        data = json.loads(_labels_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v)[:LABEL_MAX] for k, v in data.items() if isinstance(k, str) and ID_RE.match(k)} if isinstance(data, dict) else {}


def _write_labels(labels: dict[str, str]) -> None:
    path = _labels_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def home_of(pid: str) -> Path | None:
    """The folder of an extra profile, or None for the primary / unknown ids."""
    if not pid or not ID_RE.match(pid):
        return None
    path = profiles_dir() / pid
    return path if path.is_dir() else None


def list_profiles() -> list[dict]:
    labels = _read_labels()
    out = []
    root = profiles_dir()
    if root.is_dir():
        for item in sorted(root.iterdir()):
            if item.is_dir() and ID_RE.match(item.name):
                out.append({"id": item.name, "label": labels.get(item.name) or item.name})
    return out


def _slug(label: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-_")
    return text[:32] or "perfil"


def create(label_raw: str) -> dict:
    label = " ".join(str(label_raw or "").split())[:LABEL_MAX]
    if not label:
        return {"ok": False, "error": "Escribe un nombre"}
    with _lock:
        existing = list_profiles()
        if len(existing) >= PROFILES_MAX:
            return {"ok": False, "error": "Hay demasiados perfiles"}
        if label.casefold() in (primary_name().casefold(), primary_label().casefold()):
            return {"ok": False, "error": "Ese es el usuario principal"}
        if any(p["label"].casefold() == label.casefold() for p in existing):
            return {"ok": False, "error": "Ya existe un perfil con ese nombre"}
        base = _slug(label)
        pid = base
        n = 2
        while (profiles_dir() / pid).exists():
            pid = f"{base[:28]}-{n}"
            n += 1
        home = profiles_dir() / pid
        profiles_dir().mkdir(parents=True, exist_ok=True)
        os.chmod(profiles_dir(), 0o700)
        home.mkdir(mode=0o700)
        for sub in SUBDIRS:
            (home / sub).mkdir(parents=True, exist_ok=True)
        labels = _read_labels()
        labels[pid] = label
        _write_labels(labels)
    return {"ok": True, "id": pid}


def _primary_label_file() -> Path:
    return base_dir() / "primary-label"


def primary_label() -> str:
    """What the primary user is called on the page (its Linux name by default)."""
    try:
        label = " ".join(_primary_label_file().read_text(encoding="utf-8").split())[:LABEL_MAX]
    except OSError:
        label = ""
    return label or primary_name()


def rename(pid: str, label_raw: str) -> dict:
    """Only the name shown changes: the id and its folder (with the logins
    in it) stay. "" is the primary user."""
    label = " ".join(str(label_raw or "").split())[:LABEL_MAX]
    if not label:
        return {"ok": False, "error": "Escribe un nombre"}
    with _lock:
        others = [p["label"] for p in list_profiles() if p["id"] != pid]
        if pid:
            others.append(primary_label())
        if any(o.casefold() == label.casefold() for o in others):
            return {"ok": False, "error": "Ya existe un perfil con ese nombre"}
        if not pid:
            path = _primary_label_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(label + "\n", encoding="utf-8")
            return {"ok": True}
        if home_of(pid) is None:
            return {"ok": False, "error": "Ese perfil no existe"}
        labels = _read_labels()
        labels[pid] = label
        _write_labels(labels)
    return {"ok": True}


def delete(pid: str) -> dict:
    home = home_of(pid)
    if home is None:
        return {"ok": False, "error": "Ese perfil no existe"}
    root = profiles_dir().resolve()
    with _lock:
        target = home.resolve()
        if target.parent != root:
            return {"ok": False, "error": "Ruta no válida"}
        shutil.rmtree(target)
        labels = _read_labels()
        labels.pop(pid, None)
        _write_labels(labels)
        current = read_active()
        if current == pid:
            _write_active("")
    return {"ok": True}


def read_active() -> str:
    try:
        line = active_file().read_text(encoding="utf-8")
    except OSError:
        return ""
    return line.split("\t", 1)[0].strip()


def _write_active(pid: str) -> None:
    global _last_active
    path = active_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    home = home_of(pid)
    text = f"{pid}\t{home}\n" if home else "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    _last_active = pid if home else ""


def set_active(pid: str) -> dict:
    """The device that typed last made this choice; unknown ids mean the primary."""
    pid = str(pid or "")
    if not home_of(pid):
        pid = ""
    with _lock:
        if _last_active != pid or read_active() != pid:
            _write_active(pid)
    return {"ok": True, "active": pid}


def public() -> dict:
    return {"ok": True, "primary": primary_label(), "profiles": list_profiles(), "active": read_active()}
