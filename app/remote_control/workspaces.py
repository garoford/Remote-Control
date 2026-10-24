"""Workspaces: named groups of tunnel tabs that survive tunnel and PC restarts.

State lives on disk (the tunnel URL changes on every start, so the browser
cannot keep it). A snapshot records each tab's folder, screen text and, for
Claude Code / Cursor Agent / Antigravity, the conversation id so a restore reopens that
exact conversation in that tab.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .history import TAB_RE, TMUX_SOCKET, _tmux, close_session, closed_tabs, list_sessions

WS_RE = re.compile(r"^w[a-f0-9]{8}$")
GROUP_RE = re.compile(r"^(g[a-f0-9]{8}|gpc|gphone)$")
# The two groups that always exist. Phone is the one "Auto" fills from herdr.
BASE_GROUPS = (("gpc", "PC"), ("gphone", "Phone"))
LABEL_MAX = 60
# Folded groups are kept per device (phone and PC each remember their own).
DEVICE_RE = re.compile(r"^[a-z0-9-]{1,40}$")
DEVICES_MAX = 20
RESUME_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
NAME_MAX = 40
THEMES = ("system", "light", "dark")
MIC_MIN, MIC_MAX = 28, 160
SCREEN_LINES = 3000

_lock = threading.RLock()


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "remote-control"


def _state_file(base: Path) -> Path:
    return base / "workspaces.json"


def _screen_file(base: Path, tab: str) -> Path:
    return base / "screens" / f"{tab}.ans"


def _new_ws_id() -> str:
    return "w" + secrets.token_hex(4)


def _new_group_id() -> str:
    return "g" + secrets.token_hex(4)


def _empty_state() -> dict:
    return {"version": 1, "active": "", "theme": "system", "groups": _base_groups(), "workspaces": [], "tabs": {}}


def _base_groups() -> list[dict]:
    return [{"id": gid, "name": name, "fixed": True} for gid, name in BASE_GROUPS]


def _ensure_groups(state: dict) -> None:
    """PC and Phone always exist and come first; every workspace has a group."""
    own = [g for g in state.get("groups") or [] if g["id"] not in dict(BASE_GROUPS)]
    state["groups"] = _base_groups() + own
    known = {g["id"] for g in state["groups"]}
    for ws in state["workspaces"]:
        if ws.get("group") not in known:
            ws["group"] = "gpc"


def _find_group(state: dict, gid: str) -> dict | None:
    for group in state["groups"]:
        if group["id"] == gid:
            return group
    return None


def load_state(base: Path | None = None) -> dict:
    base = base or state_dir()
    try:
        data = json.loads(_state_file(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    state = _empty_state()
    for ws in data.get("workspaces") or []:
        if not isinstance(ws, dict) or not WS_RE.match(str(ws.get("id", ""))):
            continue
        tabs = [t for t in ws.get("tabs") or [] if isinstance(t, str) and TAB_RE.match(t)]
        state["workspaces"].append(
            {
                "id": ws["id"],
                "name": _clean_name(ws.get("name")) or "Workspace",
                "tabs": tabs,
                "lastTab": ws.get("lastTab") if ws.get("lastTab") in tabs else "",
                "created": int(ws.get("created") or 0),
                "group": str(ws.get("group") or ""),
            }
        )
    for group in data.get("groups") or []:
        if isinstance(group, dict) and GROUP_RE.match(str(group.get("id", ""))):
            state["groups"].append({"id": group["id"], "name": _clean_name(group.get("name")) or "Grupo", "fixed": False})
    _ensure_groups(state)
    tabs = data.get("tabs")
    if isinstance(tabs, dict):
        state["tabs"] = {k: v for k, v in tabs.items() if TAB_RE.match(k) and isinstance(v, dict)}
    if data.get("theme") in THEMES:
        state["theme"] = data["theme"]
    themes = data.get("themes")
    if isinstance(themes, dict):
        state["themes"] = {
            dev: t
            for dev, t in list(themes.items())[-DEVICES_MAX:]
            if isinstance(dev, str) and DEVICE_RE.match(dev) and t in THEMES
        }
    mic = _clean_pos(data.get("micPos"))
    if mic:
        state["micPos"] = mic
    folded = data.get("folded")
    if isinstance(folded, dict):
        state["folded"] = {
            dev: [g for g in gids if isinstance(g, str) and GROUP_RE.match(g)]
            for dev, gids in list(folded.items())[-DEVICES_MAX:]
            if isinstance(dev, str) and DEVICE_RE.match(dev) and isinstance(gids, list)
        }
    files_pos = _clean_pos(data.get("filesPos"))
    if files_pos:
        state["filesPos"] = files_pos
    picks = data.get("profilePick")
    if isinstance(picks, dict):
        state["profilePick"] = {
            dev: pid
            for dev, pid in list(picks.items())[-DEVICES_MAX:]
            if isinstance(dev, str) and DEVICE_RE.match(dev) and isinstance(pid, str) and (pid == "" or PROFILE_RE.match(pid))
        }
    fs_prefs = data.get("fsPrefs")
    if isinstance(fs_prefs, dict):
        state["fsPrefs"] = {
            dev: clean
            for dev, raw in list(fs_prefs.items())[-DEVICES_MAX:]
            if isinstance(dev, str) and DEVICE_RE.match(dev) and (clean := _clean_fs_prefs(raw))
        }
    file_pos = _clean_pos(data.get("filePos"))
    if file_pos:
        state["filePos"] = file_pos
    active = data.get("active")
    if any(ws["id"] == active for ws in state["workspaces"]):
        state["active"] = active
    return state


def save_state(state: dict, base: Path | None = None) -> None:
    base = base or state_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = _state_file(base)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _clean_pos(raw) -> dict | None:
    """{x, y} as fractions of the free screen space, both in [0, 1], plus an
    optional button size in px."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y = float(raw.get("x")), float(raw.get("y"))
    except (TypeError, ValueError):
        return None
    if not (0 <= x <= 1 and 0 <= y <= 1):
        return None
    pos = {"x": round(x, 4), "y": round(y, 4)}
    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if MIC_MIN <= size <= MIC_MAX:
        pos["size"] = size
    return pos


FS_SORTS = ("name", "size", "mtime", "type")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _clean_fs_prefs(raw) -> dict | None:
    """File explorer view settings of one device."""
    if not isinstance(raw, dict):
        return None
    tabs = [t for t in raw.get("tabs") or [] if isinstance(t, str) and 0 < len(t) <= 4096][:12]
    try:
        active = int(raw.get("active") or 0)
    except (TypeError, ValueError):
        active = 0
    try:
        zoom = int(raw.get("zoom") or 0)
    except (TypeError, ValueError):
        zoom = 0
    return {
        "view": "list" if raw.get("view") == "list" else "grid",
        "hidden": bool(raw.get("hidden")),
        "sort": raw.get("sort") if raw.get("sort") in FS_SORTS else "name",
        "desc": bool(raw.get("desc")),
        "sidebar": raw.get("sidebar") is not False,
        "zoom": max(0, min(4, zoom)),
        "tabs": tabs,
        "active": max(0, min(len(tabs) - 1, active)) if tabs else 0,
    }


def _clean_name(raw) -> str:
    name = " ".join(str(raw or "").split())
    return name[:NAME_MAX]


def _find(state: dict, ws_id: str) -> dict | None:
    for ws in state["workspaces"]:
        if ws["id"] == ws_id:
            return ws
    return None


def _owner(state: dict, tab: str) -> dict | None:
    for ws in state["workspaces"]:
        if tab in ws["tabs"]:
            return ws
    return None


def _ensure_one(state: dict) -> dict:
    if not state["workspaces"]:
        ws = {"id": _new_ws_id(), "name": "General", "tabs": [], "lastTab": "", "created": int(time.time()), "group": "gpc"}
        state["workspaces"].append(ws)
    if not _find(state, state["active"]):
        state["active"] = state["workspaces"][0]["id"]
    return _find(state, state["active"])


def reconcile(state: dict, live_ids: list[str]) -> dict:
    """Put unknown live tabs in the active workspace; drop tabs that are gone.

    An empty live list is treated as "tmux not up yet" and prunes nothing, so a
    restart never wipes the saved layout.
    """
    active = _ensure_one(state)
    live = [t for t in live_ids if TAB_RE.match(t)]
    for tab in live:
        if not _owner(state, tab):
            active["tabs"].append(tab)
    if live:
        alive = set(live)
        for ws in state["workspaces"]:
            ws["tabs"] = [t for t in ws["tabs"] if t in alive]
            if ws["lastTab"] not in ws["tabs"]:
                ws["lastTab"] = ws["tabs"][-1] if ws["tabs"] else ""
        state["tabs"] = {k: v for k, v in state["tabs"].items() if k in alive}
    return state


def apply_op(state: dict, op: dict, socket: str = TMUX_SOCKET) -> dict:
    """One change from the sidebar. Returns {"ok": bool, ...}."""
    kind = str(op.get("op") or "")
    _ensure_one(state)
    if kind == "micPos":
        pos = _clean_pos(op.get("pos"))
        if not pos:
            return {"ok": False}
        state["micPos"] = pos
        return {"ok": True}
    if kind == "filesPos":
        pos = _clean_pos(op.get("pos"))
        if not pos:
            return {"ok": False}
        state["filesPos"] = pos
        return {"ok": True}
    if kind == "profilePick":
        device = str(op.get("device") or "")
        pid = str(op.get("profile") or "")
        if not DEVICE_RE.match(device) or not (pid == "" or PROFILE_RE.match(pid)):
            return {"ok": False}
        picks = state.setdefault("profilePick", {})
        picks.pop(device, None)
        picks[device] = pid
        while len(picks) > DEVICES_MAX:
            picks.pop(next(iter(picks)))
        return {"ok": True}
    if kind == "fsPrefs":
        device = str(op.get("device") or "")
        prefs = _clean_fs_prefs(op.get("prefs"))
        if not DEVICE_RE.match(device) or not prefs:
            return {"ok": False}
        store = state.setdefault("fsPrefs", {})
        store.pop(device, None)
        store[device] = prefs
        while len(store) > DEVICES_MAX:
            store.pop(next(iter(store)))
        return {"ok": True}
    if kind == "filePos":
        pos = _clean_pos(op.get("pos"))
        if not pos:
            return {"ok": False}
        state["filePos"] = pos
        return {"ok": True}
    if kind == "theme":
        if op.get("theme") not in THEMES:
            return {"ok": False}
        device = str(op.get("device") or "")
        if not DEVICE_RE.match(device):
            state["theme"] = op["theme"]
            return {"ok": True}
        # Each device keeps its own: a laptop can stay light while a phone is dark.
        themes = state.setdefault("themes", {})
        themes.pop(device, None)
        themes[device] = op["theme"]
        while len(themes) > DEVICES_MAX:
            themes.pop(next(iter(themes)))
        return {"ok": True}
    if kind == "fold":
        device = str(op.get("device") or "")
        gids = op.get("groups")
        if not DEVICE_RE.match(device) or not isinstance(gids, list):
            return {"ok": False}
        folded = state.setdefault("folded", {})
        folded.pop(device, None)
        folded[device] = [g for g in gids if isinstance(g, str) and _find_group(state, g)]
        while len(folded) > DEVICES_MAX:
            folded.pop(next(iter(folded)))
        return {"ok": True}
    if kind == "groupCreate":
        group = {"id": _new_group_id(), "name": _clean_name(op.get("name")) or "Grupo", "fixed": False}
        state["groups"].append(group)
        return {"ok": True, "group": group["id"]}
    if kind in ("groupRename", "groupDelete"):
        group = _find_group(state, str(op.get("group") or ""))
        if not group or group["fixed"]:
            return {"ok": False}
        if kind == "groupRename":
            name = _clean_name(op.get("name"))
            if not name:
                return {"ok": False}
            group["name"] = name
            return {"ok": True}
        # Its workspaces go to PC; nothing is closed.
        for ws in state["workspaces"]:
            if ws["group"] == group["id"]:
                ws["group"] = "gpc"
        state["groups"] = [g for g in state["groups"] if g is not group]
        return {"ok": True}
    if kind == "groupClear":
        return _group_clear(state, str(op.get("group") or ""), socket, keep=str(op.get("keep") or ""))
    if kind == "herdrPlan":
        from .herdr import herdr_plan

        plan = herdr_plan()
        if plan.get("error"):
            return {"ok": False, "error": plan["error"]}
        phone = [t for ws in state["workspaces"] if ws["group"] == "gphone" for t in ws["tabs"]]
        return {"ok": True, "plan": plan["workspaces"], "closing": len(phone)}
    if kind == "herdrApply":
        return _herdr_apply(state, socket, keep=str(op.get("keep") or ""))
    if kind == "create":
        name = _clean_name(op.get("name")) or f"Workspace {len(state['workspaces']) + 1}"
        current = _find(state, state["active"])
        group = str(op.get("group") or "") or (current["group"] if current else "gpc")
        if not _find_group(state, group):
            group = "gpc"
        ws = {"id": _new_ws_id(), "name": name, "tabs": [], "lastTab": "", "created": int(time.time()), "group": group}
        state["workspaces"].append(ws)
        state["active"] = ws["id"]
        return {"ok": True, "id": ws["id"]}
    ws = _find(state, str(op.get("id") or ""))
    if kind == "move":
        group = _find_group(state, str(op.get("group") or ""))
        if not ws or not group:
            return {"ok": False}
        ws["group"] = group["id"]
        return {"ok": True}
    if kind == "order":
        # Tab strip drag: the new order of this workspace's tabs.
        wanted = op.get("tabs")
        if not ws or not isinstance(wanted, list):
            return {"ok": False}
        mine = set(ws["tabs"])
        ordered = []
        for tab in wanted:
            if isinstance(tab, str) and tab in mine and tab not in ordered:
                ordered.append(tab)
        ws["tabs"] = ordered + [t for t in ws["tabs"] if t not in ordered]
        return {"ok": True}
    if kind == "rename":
        name = _clean_name(op.get("name"))
        if not ws or not name:
            return {"ok": False}
        ws["name"] = name
        return {"ok": True}
    if kind == "activate":
        if not ws:
            return {"ok": False}
        state["active"] = ws["id"]
        return {"ok": True, "lastTab": ws["lastTab"]}
    if kind == "delete":
        if not ws:
            return {"ok": False}
        for tab in ws["tabs"]:
            close_session(tab, socket=socket)
            state["tabs"].pop(tab, None)
        state["workspaces"] = [w for w in state["workspaces"] if w is not ws]
        if state["active"] == ws["id"]:
            state["active"] = ""
        _ensure_one(state)
        return {"ok": True, "active": state["active"]}
    tab = str(op.get("tab") or "")
    if kind in ("assign", "open"):
        if not TAB_RE.match(tab):
            return {"ok": False}
        target = ws or _owner(state, tab) or _find(state, state["active"])
        current = _owner(state, tab)
        if current is not target:
            if current:
                current["tabs"].remove(tab)
            target["tabs"].append(tab)
        target["lastTab"] = tab
        state["active"] = target["id"]
        return {"ok": True, "id": target["id"]}
    if kind == "forget":
        owner = _owner(state, tab)
        if owner:
            owner["tabs"].remove(tab)
            if owner["lastTab"] == tab:
                owner["lastTab"] = owner["tabs"][-1] if owner["tabs"] else ""
        state["tabs"].pop(tab, None)
        return {"ok": True}
    return {"ok": False}


def _group_clear(state: dict, gid: str, socket: str, keep: str = "") -> dict:
    """Close every tab of a group and delete its workspaces; the group stays.

    ``keep`` is the tab on screen: the page closes it after moving away.
    """
    group = _find_group(state, gid)
    if not group:
        return {"ok": False}
    for ws in [w for w in state["workspaces"] if w["group"] == gid]:
        for tab in ws["tabs"]:
            if tab == keep:
                continue
            close_session(tab, socket=socket)
            state["tabs"].pop(tab, None)
        state["workspaces"].remove(ws)
    if not _find(state, state["active"]):
        state["active"] = state["workspaces"][0]["id"] if state["workspaces"] else ""
    _ensure_one(state)
    return {"ok": True, "active": state["active"]}


def _herdr_apply(state: dict, socket: str, keep: str = "") -> dict:
    """Replace everything in Phone with herdr's workspaces, one tab per pane.

    ``keep`` is the tab the phone is showing: the page closes it itself after
    moving to the new tabs, so its reconnect does not bring it back.
    """
    from .herdr import herdr_plan, tab_launch_context

    plan = herdr_plan()
    if plan.get("error"):
        return {"ok": False, "error": plan["error"]}
    conf, shell, env = tab_launch_context()
    conf_args = ["-f", str(conf)] if conf.is_file() else []
    env_args = tab_env_args(conf, shell)
    for ws in [w for w in state["workspaces"] if w["group"] == "gphone"]:
        for tab in ws["tabs"]:
            if tab == keep:
                continue
            close_session(tab, socket=socket)
            state["tabs"].pop(tab, None)
        state["workspaces"].remove(ws)
    first_ws, first_tab = "", ""
    now = int(time.time())
    for item in plan["workspaces"]:
        ws = {"id": _new_ws_id(), "name": _clean_name(item["name"]) or "herdr", "tabs": [], "lastTab": "", "created": now, "group": "gphone"}
        for pane in item["panes"]:
            tab = "rc" + secrets.token_hex(12)
            made = subprocess.run(
                ["tmux", "-L", socket, *conf_args, "new-session", *env_args, "-d", "-s", tab, "-c", pane["cwd"], "--", shell, "-il"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                env=env,
            )
            if made.returncode != 0:
                continue
            if pane["command"]:
                _tmux(socket, "send-keys", "-t", f"={tab}:", "-l", pane["command"])
                _tmux(socket, "send-keys", "-t", f"={tab}:", "Enter")
            ws["tabs"].append(tab)
            state["tabs"][tab] = {"cwd": pane["cwd"], "cli": "", "resume": "", "argv": [], "label": pane["label"][:LABEL_MAX], "updated": now}
        if not ws["tabs"]:
            continue
        ws["lastTab"] = ws["tabs"][0]
        state["workspaces"].append(ws)
        if not first_ws:
            first_ws, first_tab = ws["id"], ws["tabs"][0]
    if not first_ws:
        _ensure_one(state)
        return {"ok": False, "error": "No pude abrir las pestañas"}
    state["active"] = first_ws
    return {"ok": True, "id": first_ws, "firstTab": first_tab}


def public_view(state: dict, sessions: list[dict]) -> dict:
    return {
        "active": state["active"],
        "theme": state.get("theme", "system"),
        "themes": state.get("themes") or {},
        "micPos": state.get("micPos"),
        "filePos": state.get("filePos"),
        "filesPos": state.get("filesPos"),
        "fsPrefs": state.get("fsPrefs") or {},
        "profilePick": state.get("profilePick") or {},
        "groups": [dict(g) for g in state["groups"]],
        "folded": state.get("folded") or {},
        "workspaces": [
            {"id": ws["id"], "name": ws["name"], "tabs": list(ws["tabs"]), "lastTab": ws["lastTab"], "group": ws["group"]}
            for ws in state["workspaces"]
        ],
        "labels": {tab: info["label"] for tab, info in state["tabs"].items() if info.get("label")},
        "sessions": sessions,
        "closed": closed_tabs(),
    }


def workspaces_payload(socket: str = TMUX_SOCKET, base: Path | None = None) -> dict:
    with _lock:
        sessions = list_sessions(socket=socket)
        state = reconcile(load_state(base), [s["id"] for s in sessions])
        save_state(state, base)
        return public_view(state, sessions)


def change_workspaces(op: dict, socket: str = TMUX_SOCKET, base: Path | None = None) -> dict:
    with _lock:
        sessions = list_sessions(socket=socket)
        state = reconcile(load_state(base), [s["id"] for s in sessions])
        result = apply_op(state, op, socket=socket)
        if result.get("ok") and op.get("op") in ("delete", "herdrApply", "groupClear"):
            sessions = list_sessions(socket=socket)
        save_state(state, base)
        return {**result, **public_view(state, sessions)}


# --- which conversation is running in a pane --------------------------------


def _profile_home(profile: str) -> Path | None:
    if not profile:
        return None
    from .profiles import home_of

    return home_of(profile)


def cli_profile(pane_pid: int) -> str:
    """The Remote Control profile a CLI under this pane runs with ("" = primary)."""
    queue = list(_children(pane_pid))
    seen = 0
    while queue and seen < 32:
        pid = queue.pop(0)
        seen += 1
        try:
            env = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            env = b""
        for item in env.split(b"\0"):
            if item.startswith(b"RC_PROFILE="):
                value = item[len(b"RC_PROFILE=") :].decode("ascii", "replace")
                if PROFILE_RE.match(value):
                    return value
        queue.extend(_children(pid))
    return ""


def _children(pid: int) -> list[int]:
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text()
    except OSError:
        return []
    return [int(p) for p in raw.split() if p.isdigit()]


def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _claude_session(pid: int, home: Path) -> str:
    try:
        data = json.loads((home / ".claude" / "sessions" / f"{pid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    sid = str(data.get("sessionId") or "") if isinstance(data, dict) else ""
    return sid if RESUME_RE.match(sid) else ""


def _cursor_chat(pid: int, home: Path) -> str:
    chats = str(home / ".cursor" / "chats") + "/"
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return ""
    for fd in fds:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if target.startswith(chats) and target.endswith("/store.db"):
            parts = target[len(chats) :].split("/")
            if len(parts) == 3 and RESUME_RE.match(parts[1]):
                return parts[1]
    return ""


AGY_CONV_RE = re.compile(r"(?:Created|Streaming) conversation ([0-9a-f-]{36})")


def _agy_conversation(pid: int, home: Path) -> str:
    """Antigravity CLI logs the conversation it works on to its stdout file."""
    root = home / ".gemini" / "antigravity-cli"
    try:
        log = os.readlink(f"/proc/{pid}/fd/1")
    except OSError:
        return ""
    if not log.startswith(str(root / "log") + "/"):
        return ""
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 512 * 1024))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    found = AGY_CONV_RE.findall(tail)
    if found and (root / "conversations" / f"{found[-1]}.db").is_file():
        return found[-1]
    return ""


def detect_cli(pane_pid: int, home: Path | None = None) -> tuple[str, str]:
    """("claude"|"agent"|"agy", conversation id or "") for the CLI under a pane's shell."""
    home = home or Path.home()
    queue = list(_children(pane_pid))
    seen = 0
    fallback = ""
    while queue and seen < 32:
        pid = queue.pop(0)
        seen += 1
        sid = _claude_session(pid, home)
        if sid:
            return "claude", sid
        argv = _cmdline(pid)
        name = os.path.basename(argv[0]) if argv else ""
        if name == "agy":
            return "agy", _agy_conversation(pid, home)
        if "cursor-agent" in " ".join(argv) or name == "agent":
            chat = _cursor_chat(pid, home)
            if chat:
                return "agent", chat
            fallback = fallback or "agent"
        queue.extend(_children(pid))
    return fallback, ""


# Interactive CLI tools reopened after a restart. A program not listed here
# still counts when it took over the screen (alternate screen, like any TUI).
# Plain commands (rm, git push, npm run dev) are never rerun.
AUTO_RESUME = frozenset(
    {
        "vim", "nvim", "vi", "view", "nano", "micro", "hx", "helix", "emacs", "kak",
        "btop", "htop", "top", "atop", "nvtop", "glances", "bpytop",
        "less", "more", "most", "man", "watch", "tail",
        "mc", "ranger", "lf", "yazi", "nnn", "vifm", "broot",
        "lazygit", "lazydocker", "tig", "gitui", "k9s",
        "ssh", "mosh", "tmux", "psql", "mysql", "sqlite3", "redis-cli",
        "gemini", "codex", "aider", "opencode", "crush", "goose", "qwen", "amp", "copilot",
    }
)
SHELLS = frozenset({"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh", "nu"})


def foreground_argv(pane_pid: int) -> list[str]:
    """argv of the job in the foreground of a pane's terminal, [] at a prompt."""
    try:
        stat = Path(f"/proc/{pane_pid}/stat").read_text()
    except OSError:
        return []
    fields = stat[stat.rfind(")") + 2 :].split()
    # After the command name: state ppid pgrp session tty_nr tpgid ...
    if len(fields) < 6 or not fields[5].lstrip("-").isdigit():
        return []
    tpgid = int(fields[5])
    if tpgid <= 0 or tpgid == pane_pid:
        return []
    argv = _cmdline(tpgid)
    if not argv or os.path.basename(argv[0]).lstrip("-") in SHELLS:
        return []
    if any("\n" in part or "\r" in part for part in argv):
        return []
    return argv


def is_cli_tool(argv: list[str], alternate: bool) -> bool:
    return bool(argv) and (alternate or os.path.basename(argv[0]) in AUTO_RESUME)


def rerun_line(argv: list[str]) -> str:
    if not argv:
        return ""
    name = os.path.basename(argv[0])
    # Prefer the bare name when PATH finds the same binary; it reads better.
    head = name if shutil.which(name) == argv[0] else argv[0]
    return shlex.join([head, *argv[1:]])


# --- snapshot / restore -------------------------------------------------------


def _pane_info(tab: str, socket: str) -> tuple[int, str, bool] | None:
    result = _tmux(
        socket,
        "display-message",
        "-p",
        "-t",
        f"={tab}:",
        "#{pane_pid}\t#{pane_current_path}\t#{alternate_on}",
        text=True,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.rstrip("\n").split("\t")
    if len(parts) < 3 or not parts[0].isdigit() or not parts[1]:
        return None
    return int(parts[0]), parts[1], parts[2] == "1"


# A tab that had a CLI keeps it on record until it has sat at a prompt this
# long: a shutdown killing the CLI first, or a restored CLI still starting,
# must not wipe what to reopen.
IDLE_FORGET_S = 30


def snapshot(socket: str = TMUX_SOCKET, base: Path | None = None) -> None:
    base = base or state_dir()
    sessions = list_sessions(socket=socket)
    if not sessions:
        return
    meta: dict[str, dict] = {}
    for item in sessions:
        tab = item["id"]
        info = _pane_info(tab, socket)
        if info is None:
            continue
        pane_pid, cwd, alternate = info
        profile = cli_profile(pane_pid)
        cli, resume = detect_cli(pane_pid, home=_profile_home(profile))
        argv = [] if cli else foreground_argv(pane_pid)
        if not is_cli_tool(argv, alternate):
            argv = []
        meta[tab] = {
            "cwd": cwd,
            "cli": cli,
            "resume": resume,
            "argv": argv,
            "updated": int(time.time()),
        }
        if profile and (cli or argv):
            meta[tab]["profile"] = profile
        shot = _tmux(socket, "capture-pane", "-p", "-e", "-J", "-S", f"-{SCREEN_LINES}", "-t", f"={tab}:", text=True)
        if shot.returncode == 0:
            path = _screen_file(base, tab)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(shot.stdout.rstrip("\n") + "\n", encoding="utf-8")
            os.replace(tmp, path)
    with _lock:
        state = reconcile(load_state(base), [s["id"] for s in sessions])
        now = int(time.time())
        for tab, info in meta.items():
            prev = state["tabs"].get(tab) or {}
            if not info["cli"] and not info["argv"] and (prev.get("cli") or prev.get("argv")):
                idle = int(prev.get("idle") or now)
                if now - idle < IDLE_FORGET_S:
                    info.update(cli=prev.get("cli", ""), resume=prev.get("resume", ""), argv=prev.get("argv", []), idle=idle)
            if prev.get("label"):
                info["label"] = prev["label"]
            state["tabs"][tab] = info
        save_state(state, base)


RESUME_FLAG = {"claude": "--resume", "agent": "--resume", "agy": "--conversation"}


def _claude_transcript(sid: str, home: Path) -> bool:
    # Claude only writes the conversation after the first message; resuming
    # an empty one fails with "No conversation found".
    return any((home / ".claude" / "projects").glob(f"*/{sid}.jsonl"))


def resume_command(cli: str, resume: str, home: Path | None = None) -> str:
    if cli not in RESUME_FLAG:
        return ""
    home = home or Path.home()
    if not RESUME_RE.match(resume or ""):
        return cli
    if cli == "claude" and not _claude_transcript(resume, home):
        return cli
    return f"{cli} {RESUME_FLAG[cli]} {resume}"


def _rerun(info: dict) -> str:
    profile = str(info.get("profile") or "")
    home = _profile_home(profile)
    # The shell wrapper (web/profiles.zsh) honours an explicit RC_PROFILE.
    prefix = f"RC_PROFILE={profile} " if home else ""
    line = resume_command(str(info.get("cli") or ""), str(info.get("resume") or ""), home=home)
    if line:
        return prefix + line
    argv = info.get("argv")
    if isinstance(argv, list) and all(isinstance(a, str) for a in argv):
        line = rerun_line(argv)
        return prefix + line if line else ""
    return ""


def restore_plan(state: dict, base: Path, shell: str) -> list[dict]:
    """tmux commands that bring every saved tab back, in workspace order."""
    plan = []
    home = str(Path.home())
    for ws in state["workspaces"]:
        for tab in ws["tabs"]:
            info = state["tabs"].get(tab) or {}
            cwd = str(info.get("cwd") or home)
            if not os.path.isdir(cwd):
                cwd = home
            screen = _screen_file(base, tab)
            script = 'if [ -f "$1" ]; then cat "$1"; printf "\\033[0m"; fi; exec "$2" -il'
            plan.append(
                {
                    "tab": tab,
                    "new": ["new-session", "-d", "-s", tab, "-c", cwd, "--", "sh", "-c", script, "rc-restore", str(screen), shell],
                    "type": _rerun(info),
                }
            )
    return plan


def tab_env_args(conf: Path, shell: str) -> list[str]:
    """tmux new-session -e flags that make a zsh tab load web/zdot (CLI wrappers)."""
    run_dir = conf.parent
    zdot = run_dir / "zdot"
    keep = run_dir / "rc_keephistory.py"
    if os.path.basename(shell) != "zsh" or not (zdot / ".zshrc").is_file() or not keep.is_file():
        return []
    return ["-e", f"ZDOTDIR={zdot}", "-e", f"RC_KEEPHISTORY={keep}"]


def restore(
    socket: str,
    conf: Path,
    shell: str,
    base: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Recreate saved tabs when tmux has none (after a PC restart). Returns count."""
    base = base or state_dir()
    with _lock:
        if list_sessions(socket=socket):
            return 0
        state = load_state(base)
        plan = restore_plan(state, base, shell)
    made = 0
    conf_args = ["-f", str(conf)] if conf.is_file() else []
    env_args = tab_env_args(conf, shell)
    for step in plan:
        new = step["new"][:1] + env_args + step["new"][1:]
        result = subprocess.run(
            ["tmux", "-L", socket, *conf_args, *new],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            continue
        made += 1
        if step["type"]:
            _tmux(socket, "send-keys", "-t", f"={step['tab']}:", "-l", step["type"])
            _tmux(socket, "send-keys", "-t", f"={step['tab']}:", "Enter")
    return made

