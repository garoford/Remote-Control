"""Read the running herdr layout so the Phone group can copy it as tabs.

herdr shows its panes side by side, which does not fit a phone. "Auto" turns
each herdr workspace into a workspace and each pane into a tab that reopens
the same conversation in the same folder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .workspaces import AUTO_RESUME, _profile_home, cli_profile, detect_cli, foreground_argv, rerun_line, resume_command

LABEL_MAX = 60


def _herdr_bin() -> str:
    return shutil.which("herdr") or str(Path.home() / ".local" / "bin" / "herdr")


def _api(*args: str) -> dict | None:
    try:
        result = subprocess.run(
            [_herdr_bin(), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    out = data.get("result") if isinstance(data, dict) else None
    return out if isinstance(out, dict) else None


def _pane_command(pane_id: str) -> str:
    """What to type in the copied tab: the conversation resume, a known CLI, or nothing."""
    info = _api("pane", "process-info", "--pane", pane_id)
    proc = (info or {}).get("process_info") or {}
    shell_pid = proc.get("shell_pid")
    if isinstance(shell_pid, int) and shell_pid > 0:
        profile = cli_profile(shell_pid)
        home = _profile_home(profile)
        cli, resume = detect_cli(shell_pid, home=home)
        line = resume_command(cli, resume, home=home)
        if line:
            return (f"RC_PROFILE={profile} " if home else "") + line
        argv = foreground_argv(shell_pid)
    else:
        argv = []
    if not argv:
        fg = proc.get("foreground_processes") or []
        argv = fg[0].get("argv") if fg and isinstance(fg[0], dict) else []
    if (
        isinstance(argv, list)
        and argv
        and all(isinstance(a, str) and "\n" not in a and "\r" not in a for a in argv)
        and os.path.basename(argv[0]) in AUTO_RESUME
    ):
        return rerun_line(argv)
    return ""


def _short(text: str) -> str:
    return " ".join(str(text or "").split())[:LABEL_MAX]


def herdr_plan() -> dict:
    """{"workspaces": [{name, panes: [{label, cwd, command}]}]} or {"error": ...}."""
    workspaces = _api("workspace", "list")
    panes = _api("pane", "list")
    if workspaces is None or panes is None:
        return {"error": "herdr no está corriendo"}
    home = str(Path.home())
    by_ws: dict[str, list[dict]] = {}
    for pane in panes.get("panes") or []:
        if isinstance(pane, dict) and pane.get("workspace_id"):
            by_ws.setdefault(pane["workspace_id"], []).append(pane)
    out = []
    for ws in sorted(workspaces.get("workspaces") or [], key=lambda w: w.get("number") or 0):
        items = []
        # pane list already comes in tab order, then pane order inside the tab
        for pane in by_ws.get(ws.get("workspace_id"), []):
            cwd = str(pane.get("foreground_cwd") or pane.get("cwd") or home)
            if not os.path.isdir(cwd):
                cwd = home
            label = _short(pane.get("label") or pane.get("terminal_title_stripped") or os.path.basename(cwd))
            items.append({"label": label, "cwd": cwd, "command": _pane_command(str(pane.get("pane_id") or ""))})
        if items:
            out.append({"name": _short(ws.get("label")) or f"herdr {ws.get('number', '')}".strip(), "panes": items})
    if not out:
        return {"error": "herdr no tiene paneles abiertos"}
    return {"workspaces": out}


def tab_launch_context() -> tuple[Path, str, dict[str, str]]:
    """tmux conf, login shell and environment that tunnel tabs start with."""
    from .tunnel import TunnelService

    service = TunnelService()
    return service.run_dir / "tmux.tab.conf", service._resolve_user_shell(), service._session_env()
