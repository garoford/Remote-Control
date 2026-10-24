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


# --- who opened each herdr pane ------------------------------------------------
# The page marks every herdr pane with the avatar of the profile that opened
# it. herdr does not know about profiles, so a pane belongs to the profile
# active (the device that typed last, profiles.py) when it first shows up in
# the snapshot. The page polls every few seconds while a herdr tab is open,
# so that is the device that just split or opened it.

MARK_BOXES = 32
MARK_LINES = 8
SHAPE_TOLERANCE = 0.06


def _owners_file() -> Path:
    from .profiles import base_dir

    return base_dir() / "herdr-owners.json"


def _load_owners() -> dict[str, str]:
    try:
        data = json.loads(_owners_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _save_owners(owners: dict[str, str]) -> None:
    path = _owners_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(owners), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _clean_boxes(raw: object) -> list[dict]:
    """Pane frames the page found on its own screen: cells plus a few lines of text."""
    boxes = []
    if not isinstance(raw, list):
        return boxes
    for item in raw[:MARK_BOXES]:
        if not isinstance(item, dict):
            return []
        try:
            x, y, w, h = (int(item[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            return []
        if w < 3 or h < 3 or x < 0 or y < 0:
            return []
        lines = item.get("lines")
        lines = [" ".join(str(t).split())[:200] for t in lines[:MARK_LINES]] if isinstance(lines, list) else []
        boxes.append({"x": x, "y": y, "w": w, "h": h, "lines": [t for t in lines if len(t) >= 4]})
    return boxes


def _norm(rects: list[tuple[int, int, int, int]]) -> list[tuple[float, float, float, float]]:
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    width = max(r[0] + r[2] for r in rects) - left or 1
    height = max(r[1] + r[3] for r in rects) - top or 1
    return [((r[0] - left) / width, (r[1] - top) / height, r[2] / width, r[3] / height) for r in rects]


def _layout_panes(layout: dict) -> list[tuple[str, tuple[int, int, int, int]]]:
    panes = []
    if layout.get("zoomed"):
        # A zoomed tab shows only its focused pane, filling the area.
        area = layout.get("area") or {}
        pid = layout.get("focused_pane_id")
        if isinstance(pid, str):
            panes.append((pid, (area.get("x", 0), area.get("y", 0), area.get("width", 1), area.get("height", 1))))
        return panes
    for pane in layout.get("panes") or []:
        rect = pane.get("rect") if isinstance(pane, dict) else None
        if isinstance(rect, dict) and isinstance(pane.get("pane_id"), str):
            panes.append((pane["pane_id"], (rect.get("x", 0), rect.get("y", 0), rect.get("width", 1), rect.get("height", 1))))
    return panes


def _shape_match(boxes: list[dict], panes: list[tuple[str, tuple]]) -> list[str] | None:
    """Pane id for each box when the layout has the same shape, else None."""
    if len(panes) != len(boxes):
        return None
    seen = _norm([(b["x"], b["y"], b["w"], b["h"]) for b in boxes])
    want = _norm([rect for _, rect in panes])
    out: list[str] = []
    used: set[int] = set()
    for box in seen:
        best, best_d = -1, 9.0
        for i, rect in enumerate(want):
            if i in used:
                continue
            d = max(abs(a - b) for a, b in zip(box, rect))
            if d < best_d:
                best, best_d = i, d
        if best < 0 or best_d > SHAPE_TOLERANCE:
            return None
        used.add(best)
        out.append(panes[best][0])
    return out


def _pane_text(pane_id: str) -> str:
    try:
        result = subprocess.run(
            [_herdr_bin(), "pane", "read", pane_id, "--source", "visible"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(result.stdout.split()) if result.returncode == 0 else ""


def pane_marks(raw_boxes: object) -> dict:
    """Owner profile of each pane frame the page sees ("" is the primary user)."""
    from .profiles import read_active

    boxes = _clean_boxes(raw_boxes)
    snap = (_api("api", "snapshot") or {}).get("snapshot")
    if not boxes or not isinstance(snap, dict):
        return {"ok": True, "marks": []}
    live = {p["pane_id"] for p in snap.get("panes") or [] if isinstance(p, dict) and isinstance(p.get("pane_id"), str)}
    if not live:
        return {"ok": True, "marks": []}
    owners = _load_owners()
    fresh = {pid: owners.get(pid, "") for pid in live if pid in owners}
    if len(fresh) != len(live) or len(owners) != len(fresh):
        active = read_active()
        for pid in live - fresh.keys():
            fresh[pid] = active
        _save_owners(fresh)
    candidates = []
    for layout in snap.get("layouts") or []:
        if isinstance(layout, dict) and (ids := _shape_match(boxes, _layout_panes(layout))):
            candidates.append((layout, ids))
    if len(candidates) > 1:
        # Same shape in several tabs: the text on screen says which one this is.
        scored = []
        for layout, ids in candidates:
            score = 0
            for box, pid in zip(boxes, ids):
                text = _pane_text(pid)
                score += sum(1 for line in box["lines"] if line in text)
            scored.append((score, layout, ids))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
            return {"ok": True, "marks": []}
        candidates = [(scored[0][1], scored[0][2])]
    if not candidates:
        return {"ok": True, "marks": []}
    _, ids = candidates[0]
    return {"ok": True, "marks": [{"box": i, "pane": pid, "owner": fresh.get(pid, "")} for i, pid in enumerate(ids)]}
