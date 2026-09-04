"""tmux pane history + fingerprint reconcile for the tty cache."""

from __future__ import annotations

import re
import subprocess

TAB_RE = re.compile(r"^rc[a-z0-9]{10,32}$")
MAX_RETURN_LINES = 20000
TMUX_SOCKET = "cf-remote"


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
