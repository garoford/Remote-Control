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
    mode, out = find_suffix(lines, fingerprint)
    return {"mode": mode, "lines": out, "count": len(lines)}
