"""tmux pane history + fingerprint reconcile for the tty cache."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

TAB_RE = re.compile(r"^rc[a-z0-9]{10,32}$")
MAX_RETURN_LINES = 20000
TMUX_SOCKET = "cf-remote"
CLOSED_KEEP_S = 7 * 24 * 3600
BUSY_S = 8


def closed_dir() -> Path:
    """Closed tab ids. tab_session.sh will not bring one of these back."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "cf-quick-tunnel" / "closed"


def closed_tabs() -> list[str]:
    folder = closed_dir()
    if not folder.is_dir():
        return []
    cutoff = time.time() - CLOSED_KEEP_S
    out = []
    for item in folder.iterdir():
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
            elif TAB_RE.match(item.name):
                out.append(item.name)
        except OSError:
            continue
    return out


def _mark_closed(tab: str) -> None:
    try:
        folder = closed_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / tab).touch()
    except OSError:
        pass


def normalize_line(line: str) -> str:
    return line.rstrip("\n\r").rstrip()


def find_suffix(lines: list[str], fingerprint: list[str]) -> tuple[str, list[str]]:
    """Return ('suffix', new_lines) or ('full', last N lines)."""
    if not fingerprint:
        return "full", lines[-MAX_RETURN_LINES:]
    fp = [normalize_line(item) for item in fingerprint if normalize_line(item)]
    if not fp:
        return "full", lines[-MAX_RETURN_LINES:]
    norm = [normalize_line(item) for item in lines]
    length = len(fp)
    for index in range(len(norm) - length, -1, -1):
        if norm[index : index + length] == fp:
            return "suffix", lines[index + length :]
    return "full", lines[-MAX_RETURN_LINES:]


def close_session(tab: str, socket: str = TMUX_SOCKET) -> bool:
    """Kill one tunnel session. Refuses anything that is not a tab id."""
    if not TAB_RE.match(tab):
        return False
    # Before the kill: another device's socket reconnects right away and
    # would otherwise start the tab again.
    _mark_closed(tab)
    result = subprocess.run(
        ["tmux", "-L", socket, "kill-session", "-t", f"={tab}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    # Per-device herdr views of this tab (tab_session.sh) go with it.
    names = _tmux(socket, "list-sessions", "-F", "#{session_name}", text=True)
    for name in (names.stdout or "").splitlines() if names.returncode == 0 else []:
        if name.startswith("hv-") and name.endswith(f"-{tab}"):
            _tmux(socket, "kill-session", "-t", f"={name}")
    return result.returncode == 0


def has_session(tab: str, socket: str = TMUX_SOCKET) -> bool:
    if not TAB_RE.match(tab):
        return False
    result = subprocess.run(
        ["tmux", "-L", socket, "has-session", "-t", tab],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def capture_pane(tab: str, socket: str = TMUX_SOCKET) -> list[str] | None:
    if not TAB_RE.match(tab):
        return None
    result = subprocess.run(
        ["tmux", "-L", socket, "capture-pane", "-p", "-J", "-S", "-", "-t", tab],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


def _pane_in_mode(tab: str, socket: str) -> bool:
    result = subprocess.run(
        ["tmux", "-L", socket, "display-message", "-p", "-t", tab, "#{pane_in_mode}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "1"


def _tmux(socket: str, *args: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "-L", socket, *args],
        capture_output=True,
        text=text,
        check=False,
    )


def _send_copy(tab: str, socket: str, *keys: str) -> bool:
    for target in (tab, f"{tab}:1.1"):
        result = _tmux(socket, "send-keys", "-t", target, *keys)
        if result.returncode == 0:
            return True
    return False


def pane_scroll_state(tab: str, socket: str = TMUX_SOCKET) -> dict | None:
    if not TAB_RE.match(tab) or not has_session(tab, socket):
        return None
    result = _tmux(
        socket,
        "display-message",
        "-p",
        "-t",
        tab,
        "#{pane_in_mode}\t#{scroll_position}\t#{history_size}\t#{pane_height}",
        text=True,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split("\t")
    if len(parts) < 4:
        return None
    return {
        "in_mode": parts[0] == "1",
        "position": int(parts[1]) if parts[1].isdigit() else 0,
        "history": int(parts[2]) if parts[2].isdigit() else 0,
        "height": int(parts[3]) if parts[3].isdigit() else 0,
    }


def cancel_copy_mode(tab: str, socket: str = TMUX_SOCKET) -> bool:
    """Leave tmux copy-mode. No-ops (False) if the pane is already live."""
    if not TAB_RE.match(tab) or not has_session(tab, socket):
        return False
    if not _pane_in_mode(tab, socket):
        return False
    if _send_copy(tab, socket, "-X", "cancel"):
        return not _pane_in_mode(tab, socket)
    return False


def scroll_history(tab: str, lines: int, socket: str = TMUX_SOCKET) -> dict | None:
    """Move tmux copy-mode. Negative lines go toward older history."""
    state = pane_scroll_state(tab, socket)
    if state is None:
        return None
    try:
        delta = int(lines)
    except (TypeError, ValueError):
        return {**state, "ok": True, "moved": 0}
    delta = max(-80, min(80, delta))
    if delta == 0:
        return {**state, "ok": True, "moved": 0}

    if delta < 0:
        want = -delta
        if state["history"] <= 0:
            return {**state, "ok": True, "moved": 0}
        if not state["in_mode"]:
            _tmux(socket, "copy-mode", "-e", "-t", tab)
            state = pane_scroll_state(tab, socket) or state
        room = max(0, state["history"] - state["position"])
        moved = min(want, room)
        if moved:
            _send_copy(tab, socket, "-X", "-N", str(moved), "scroll-up")
        after = pane_scroll_state(tab, socket) or state
        return {**after, "ok": True, "moved": -moved}

    if not state["in_mode"]:
        return {**state, "ok": True, "moved": 0}
    moved = min(delta, state["position"])
    if moved:
        _send_copy(tab, socket, "-X", "-N", str(moved), "scroll-down")
    after = pane_scroll_state(tab, socket) or state
    if after["in_mode"] and after["position"] <= 0:
        cancel_copy_mode(tab, socket)
        after = pane_scroll_state(tab, socket) or after
    return {**after, "ok": True, "moved": moved}


def history_payload(
    tab: str,
    fingerprint: list[str],
    socket: str = TMUX_SOCKET,
) -> dict | None:
    if not TAB_RE.match(tab) or not has_session(tab, socket):
        return None
    lines = capture_pane(tab, socket)
    if lines is None:
        return None
    capped = lines[-MAX_RETURN_LINES:]
    mode, out = find_suffix(lines, fingerprint)
    return {"mode": mode, "lines": out, "all": capped, "count": len(lines)}


def _empty_scrollback() -> dict:
    return {"size": 0, "w": 0, "h": 0, "alt": 0, "mode": "none", "lines": []}


def scrollback_state(tab: str, socket: str = TMUX_SOCKET) -> dict | None:
    if not TAB_RE.match(tab) or not has_session(tab, socket):
        return None
    result = _tmux(
        socket,
        "display-message",
        "-p",
        "-t",
        tab,
        "#{history_size}\t#{pane_width}\t#{pane_height}\t#{alternate_on}",
        text=True,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split("\t")
    if len(parts) < 4:
        return None
    return {
        "history_size": int(parts[0]) if parts[0].isdigit() else 0,
        "pane_width": int(parts[1]) if parts[1].isdigit() else 0,
        "pane_height": int(parts[2]) if parts[2].isdigit() else 0,
        "alternate_on": 1 if parts[3] == "1" else 0,
    }


def capture_scrollback(
    tab: str,
    count: int | None = None,
    socket: str = TMUX_SOCKET,
) -> list[str] | None:
    if not TAB_RE.match(tab):
        return None
    if count is not None and count <= 0:
        return []
    start = f"-{int(count)}" if count is not None else "-"
    result = subprocess.run(
        [
            "tmux",
            "-L",
            socket,
            "capture-pane",
            "-p",
            "-e",
            "-S",
            start,
            "-E",
            "-1",
            "-t",
            tab,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


def capture_visible(tab: str, socket: str = TMUX_SOCKET) -> list[str] | None:
    if not TAB_RE.match(tab):
        return None
    result = subprocess.run(
        ["tmux", "-L", socket, "capture-pane", "-p", "-e", "-S", "0", "-t", tab],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


def scrollback_payload(
    tab: str,
    since: int | None,
    w: int | None,
    socket: str = TMUX_SOCKET,
) -> dict:
    if not TAB_RE.match(tab) or not has_session(tab, socket):
        return _empty_scrollback()
    state = scrollback_state(tab, socket)
    if state is None:
        return _empty_scrollback()
    size = state["history_size"]
    pane_w = state["pane_width"]
    pane_h = state["pane_height"]
    alt = state["alternate_on"]
    need_full = since is None or w is None or int(w) != pane_w or int(since) > size
    if need_full:
        lines = capture_scrollback(tab, None, socket) or []
        if len(lines) > MAX_RETURN_LINES:
            lines = lines[-MAX_RETURN_LINES:]
        return {
            "size": size,
            "w": pane_w,
            "h": pane_h,
            "alt": alt,
            "mode": "full",
            "lines": lines,
        }
    delta = size - int(since)
    lines = capture_scrollback(tab, delta, socket) or [] if delta > 0 else []
    return {
        "size": size,
        "w": pane_w,
        "h": pane_h,
        "alt": alt,
        "mode": "append",
        "lines": lines,
    }


def list_sessions(socket: str = TMUX_SOCKET) -> list[dict]:
    """Terminal sessions on this tunnel's tmux socket, newest activity first."""
    result = _tmux(
        socket,
        "list-sessions",
        "-F",
        "#{session_name}\t#{session_activity}\t#{pane_current_path}\t"
        "#{pane_current_command}\t#{session_attached}\t#{session_created}\t#{window_activity}\t#{@rc_prof}",
        text=True,
    )
    now = time.time()
    if result.returncode != 0 or not result.stdout.strip():
        return []
    sessions: list[dict] = []
    for raw in result.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) < 5:
            continue
        name = parts[0].strip()
        if not TAB_RE.match(name):
            continue
        try:
            activity = int(parts[1])
        except ValueError:
            activity = 0
        try:
            attached = int(parts[4])
        except ValueError:
            attached = 0
        created = 0
        if len(parts) > 5:
            try:
                created = int(parts[5])
            except ValueError:
                created = 0
        # Output in the last few seconds (window_activity moves on output):
        # the tab strip shows it as a live dot.
        try:
            busy = len(parts) > 6 and now - int(parts[6]) < BUSY_S
        except ValueError:
            busy = False
        # Profile that opened the tab ("p" + id, tab_session.sh); the tab strip
        # shows its avatar. None until a current page has connected.
        prof = parts[7].strip() if len(parts) > 7 else ""
        path = parts[2]
        home = str(Path.home())
        if path == home:
            path = "~"
        elif path.startswith(home + "/"):
            path = "~" + path[len(home) :]
        sessions.append(
            {
                "id": name,
                "activity": activity,
                "path": path,
                "command": parts[3],
                "attached": attached,
                "created": created,
                "busy": busy,
                "owner": prof[1:] if prof.startswith("p") else None,
            }
        )
    sessions.sort(key=lambda item: item["activity"], reverse=True)
    return sessions


PROF_RE = re.compile(r"^p([a-z0-9][a-z0-9_-]{0,31})?$")


DEVICE_RE = re.compile(r"^d[a-z0-9]{8,32}$")


def tab_size(tab: str, prof: str, socket: str = TMUX_SOCKET, dev: str = "", authority: str = "") -> dict:
    """The tab's size, and whether this profile only watches it.

    The profile that opened a tab owns its size (tab_session.sh); another
    profile's page scales the owner's columns and rows to fit its screen.
    Tabs running herdr follow herdr_size instead.
    """
    if not TAB_RE.match(tab or "") or not PROF_RE.match(prof or ""):
        return {"guest": False}
    result = _tmux(
        socket, "display-message", "-p", "-t", f"={tab}:", "#{window_width}\t#{window_height}\t#{@rc_prof}\t#{pane_current_command}", text=True
    )
    parts = result.stdout.strip().split("\t") if result.returncode == 0 else []
    if len(parts) >= 4 and parts[3] == "herdr":
        return herdr_size(tab, prof, dev, authority, socket=socket)
    if len(parts) < 4 or not parts[2] or parts[2] == prof:
        return {"guest": False}
    try:
        return {"guest": True, "cols": int(parts[0]), "rows": int(parts[1])}
    except ValueError:
        return {"guest": False}


def herdr_size(tab: str, prof: str, dev: str, authority: str, socket: str = TMUX_SOCKET) -> dict:
    """Size of this device's herdr view.

    herdr gives a pane the size of the last client that touched it. When the
    herdr tab on screen holds another profile's panes (the page passes that
    profile as ``authority``), this device's view takes the size of that
    profile's latest herdr view, so touching those panes keeps their owner's
    size; the page scales it to fit. Otherwise the view follows this screen.
    """
    if not DEVICE_RE.match(dev or ""):
        return {"guest": False}
    view = f"hv-{dev}-{tab}"
    listing = _tmux(
        socket,
        "list-sessions",
        "-F",
        "#{session_name}\t#{@rc_vprof}\t#{@rc_dev}\t#{window_width}\t#{window_height}\t#{session_attached}\t#{session_activity}",
        text=True,
    )
    sessions = {}
    for raw in listing.stdout.splitlines() if listing.returncode == 0 else []:
        cols = raw.split("\t")
        if len(cols) < 7 or not (cols[0] == tab or (cols[0].startswith("hv-") and cols[0].endswith("-" + tab))):
            continue
        try:
            sessions[cols[0]] = {"prof": cols[1], "dev": cols[2], "w": int(cols[3]), "h": int(cols[4]), "attached": int(cols[5]), "activity": int(cols[6])}
        except ValueError:
            continue
    mine = view if view in sessions else (tab if tab in sessions and sessions[tab]["dev"] == dev else "")
    if not mine:
        return {"guest": False}
    clients = []
    for raw in (_tmux(socket, "list-clients", "-F", "#{client_name}\t#{client_session}\t#{client_width}\t#{client_height}\t#{client_flags}", text=True).stdout or "").splitlines():
        cols = raw.split("\t")
        if len(cols) >= 5 and cols[1] == mine:
            clients.append(cols)
    refs = []
    if authority and authority != prof and PROF_RE.match(authority):
        refs = [s for name, s in sessions.items() if name != mine and s["prof"] == authority and s["attached"] > 0]
    if not refs:
        # Back to this screen's own size.
        for name, _session, width, height, flags in clients:
            if "ignore-size" in flags.split(","):
                _tmux(socket, "refresh-client", "-t", name, "-f", "!ignore-size")
                _tmux(socket, "resize-window", "-t", f"={mine}:", "-x", width, "-y", height)
        return {"guest": False}
    ref = max(refs, key=lambda s: s["activity"])
    for name, _session, _w, _h, flags in clients:
        if "ignore-size" not in flags.split(","):
            _tmux(socket, "refresh-client", "-t", name, "-f", "ignore-size")
    if (sessions[mine]["w"], sessions[mine]["h"]) != (ref["w"], ref["h"]):
        _tmux(socket, "resize-window", "-t", f"={mine}:", "-x", str(ref["w"]), "-y", str(ref["h"]))
    return {"guest": True, "cols": ref["w"], "rows": ref["h"]}
