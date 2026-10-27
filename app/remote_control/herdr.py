"""Read the running herdr layout so the Phone group can copy it as tabs.

herdr shows its panes side by side, which does not fit a phone. "Auto" turns
each herdr workspace into a workspace and each pane into a tab that reopens
the same conversation in the same folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
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


def pane_marks(raw_boxes: object, viewer: object = None) -> dict:
    """Owner profile of each pane frame the page sees ("" is the primary user).

    ``viewer`` ({prof, dev, single, idle_ms}) says who measured the frames:
    the panes of that profile get held at the size they have on its screen.
    """
    from .profiles import read_active

    boxes = _clean_boxes(raw_boxes)
    snap = (_api("api", "snapshot") or {}).get("snapshot")
    if not boxes or not isinstance(snap, dict):
        return {"ok": True, "marks": []}
    live = {p["pane_id"] for p in snap.get("panes") or [] if isinstance(p, dict) and isinstance(p.get("pane_id"), str)}
    _drop_dead_holds(live)
    if not live:
        return {"ok": True, "marks": []}
    owners = _load_owners()
    fresh = {pid: owners.get(pid, "") for pid in live if pid in owners}
    if len(fresh) != len(live) or len(owners) != len(fresh):
        active = read_active()
        for pid in live - fresh.keys():
            # A CLI started there first already noted its owner (profiles.zsh).
            fresh[pid] = _read_pin(pid, active)
        _save_owners(fresh)
        _drop_dead_pins(live)
    _write_missing_pins(fresh)
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
    if isinstance(viewer, dict):
        _hold_owned(boxes, ids, fresh, viewer)
    return {"ok": True, "marks": [{"box": i, "pane": pid, "owner": fresh.get(pid, "")} for i, pid in enumerate(ids)]}


# --- each pane keeps its owner's size ------------------------------------------------
# A herdr pane is one terminal with one size, and herdr gives it the size of
# the last herdr client that attached: another person opening the tab would
# reflow the owner's agent. Instead, while its owner's page reports the
# pane's frame, a herdr terminal controller holds the pane at that size
# (`herdr terminal session control --takeover`: it owns the size only; input
# from every herdr client still goes through). Everyone else sees the pane
# at the owner's size inside their own layout.

PROF_ARG_RE = re.compile(r"^p([a-z0-9][a-z0-9_-]{0,31})?$")
DEVICE_ID_RE = re.compile(r"^d[a-z0-9]{8,32}$")
HOLD_MIN = (10, 3)
HOLD_MAX = (1000, 500)
# Two devices of the owner on screen: the one typed on last sets the size.
_holds: dict[str, dict] = {}
_holds_lock = threading.Lock()


def _owner_size(box: dict, single: bool) -> tuple[int, int]:
    """Pane size from the frame measured on screen.

    Framed panes: the box includes the frame, and herdr keeps one column for
    its scrollbar. A tab with one pane has no frame: the box is the area
    right of the sidebar, still minus the scrollbar column.
    """
    if single:
        return box["w"] - 1, box["h"]
    return box["w"] - 3, box["h"] - 2


def _hold_owned(boxes: list[dict], ids: list[str], owners: dict[str, str], viewer: dict) -> None:
    prof = str(viewer.get("prof") or "")
    dev = str(viewer.get("dev") or "")
    if not PROF_ARG_RE.match(prof) or not DEVICE_ID_RE.match(dev):
        return
    try:
        idle_ms = max(0, int(viewer.get("idle_ms") or 0))
    except (TypeError, ValueError):
        idle_ms = 0
    typed_at = time.time() - idle_ms / 1000
    single = viewer.get("single") is True and len(boxes) == 1
    for box, pid in zip(boxes, ids):
        if owners.get(pid, "") != prof[1:]:
            continue
        cols, rows = _owner_size(box, single)
        if HOLD_MIN[0] <= cols <= HOLD_MAX[0] and HOLD_MIN[1] <= rows <= HOLD_MAX[1]:
            hold_pane(pid, cols, rows, dev, typed_at)


def hold_pane(pane: str, cols: int, rows: int, dev: str, typed_at: float) -> bool:
    """Keep a pane at cols x rows. Returns whether this report changed anything."""
    with _holds_lock:
        hold = _holds.get(pane)
        alive = hold is not None and hold["proc"].poll() is None
        if alive and hold["dev"] != dev and typed_at <= hold["typed_at"]:
            return False
        if alive and hold["size"] == (cols, rows):
            hold.update(dev=dev, typed_at=max(typed_at, hold["typed_at"]) if hold["dev"] == dev else typed_at)
            return False
        if alive:
            try:
                hold["proc"].stdin.write(json.dumps({"type": "terminal.resize", "cols": cols, "rows": rows}) + "\n")
                hold["proc"].stdin.flush()
                hold.update(size=(cols, rows), dev=dev, typed_at=typed_at)
                return True
            except (OSError, ValueError):
                _stop(hold)
        try:
            proc = subprocess.Popen(
                [_herdr_bin(), "terminal", "session", "control", pane, "--takeover", "--cols", str(cols), "--rows", str(rows)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        except OSError:
            _holds.pop(pane, None)
            return False
        _holds[pane] = {"proc": proc, "size": (cols, rows), "dev": dev, "typed_at": typed_at}
        return True


def _stop(hold: dict) -> None:
    proc = hold["proc"]
    try:
        # Closing stdin releases the terminal; herdr lays the pane out again.
        proc.stdin.close()
    except (OSError, ValueError):
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def release_pane(pane: str) -> None:
    with _holds_lock:
        hold = _holds.pop(pane, None)
    if hold is not None:
        _stop(hold)


def _drop_dead_holds(live: set[str]) -> None:
    with _holds_lock:
        gone = [pid for pid, hold in _holds.items() if pid not in live or hold["proc"].poll() is not None]
        holds = [_holds.pop(pid) for pid in gone]
    for hold in holds:
        _stop(hold)


# --- changing whose a pane is ----------------------------------------------------
# Clicking a pane's mark picks another profile for it. Every pane's owner is
# kept in pane-profile/<pane id>, which the shell wrapper (web/profiles.zsh)
# reads through HERDR_PANE_ID: every CLI started in that pane runs with its
# owner's account, whichever device typed last. A Claude waiting for input
# in the pane comes back right away with the new account, in the same chat
# (rc_sharechats.py links it into every profile).

PANE_RE = re.compile(r"^w[0-9A-Za-z]+:p[0-9A-Za-z]+$")
RESTART_WAIT_S = 6.0


def _pins_dir() -> Path:
    from .profiles import base_dir

    return base_dir() / "pane-profile"


def _read_pin(pane: str, default: str) -> str:
    try:
        return (_pins_dir() / pane).read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _write_missing_pins(owners: dict[str, str]) -> None:
    """Every known pane's owner, where the shell wrapper reads it."""
    try:
        _pins_dir().mkdir(parents=True, exist_ok=True)
        have = {p.name for p in _pins_dir().iterdir()}
    except OSError:
        return
    for pane, prof in owners.items():
        if pane not in have and PANE_RE.match(pane):
            try:
                (_pins_dir() / pane).write_text(prof + "\n", encoding="utf-8")
            except OSError:
                pass


def _drop_dead_pins(live: set[str]) -> None:
    try:
        pins = list(_pins_dir().iterdir())
    except OSError:
        return
    for pin in pins:
        if not PANE_RE.match(pin.name) or pin.name not in live:
            try:
                pin.unlink()
            except OSError:
                pass


def _foreground_pgid(shell_pid: int) -> int | None:
    """Process group of the job in front of the pane's shell, None at a prompt."""
    try:
        stat = Path(f"/proc/{shell_pid}/stat").read_text()
    except OSError:
        return None
    fields = stat[stat.rindex(")") + 2 :].split()
    try:
        pgrp, tpgid = int(fields[2]), int(fields[5])
    except (IndexError, ValueError):
        return None
    return tpgid if tpgid > 0 and tpgid != pgrp else None


def _share_chats() -> None:
    from .profiles import base_dir

    script = Path(__file__).with_name("web") / "rc_sharechats.py"
    try:
        subprocess.run([sys.executable, "-I", str(script), str(base_dir())], timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _move_cli(pane: str, prof: str, status: str) -> dict:
    """Bring a waiting Claude in a herdr pane back with its new account, same chat."""
    proc = (_api("pane", "process-info", "--pane", pane) or {}).get("process_info") or {}
    shell_pid = proc.get("shell_pid")
    if not isinstance(shell_pid, int) or shell_pid <= 0:
        return {"restarted": False}

    def run(line: str) -> None:
        subprocess.run([_herdr_bin(), "pane", "run", pane, line], capture_output=True, timeout=5, check=False)

    return _restart_cli(shell_pid, prof, status == "working", run)


def _restart_cli(shell_pid: int, prof: str, working: bool, run) -> dict:
    """Close a waiting Claude under shell_pid and reopen it as prof, same chat.

    ``run`` types a command line in that shell. A Claude at work, or another
    CLI, keeps its account until someone closes it.
    """
    fg = _foreground_pgid(shell_pid)
    if fg is None:
        return {"restarted": False}
    current = cli_profile(shell_pid)
    cli, sid = detect_cli(shell_pid, home=_profile_home(current))
    if not cli or current == prof:
        return {"restarted": False}
    if cli != "claude" or not sid:
        return {"restarted": False, "note": f"{cli} sigue con la cuenta anterior hasta que lo cierres"}
    if working:
        return {"restarted": False, "note": "Claude está trabajando: el cambio aplica cuando lo vuelvas a abrir"}
    _share_chats()
    try:
        os.killpg(fg, signal.SIGTERM)
    except OSError:
        return {"restarted": False, "note": "No se pudo cerrar Claude"}
    deadline = time.monotonic() + RESTART_WAIT_S
    while _foreground_pgid(shell_pid) is not None:
        if time.monotonic() > deadline:
            return {"restarted": False, "note": "Claude no se cerró: ciérralo y ábrelo de nuevo"}
        time.sleep(0.2)
    line = resume_command("claude", sid, home=_profile_home(prof)) or "claude"
    try:
        run(f" RC_PROFILE={prof} {line}")
    except (OSError, subprocess.SubprocessError):
        return {"restarted": False, "note": "Claude se cerró: ábrelo de nuevo"}
    return {"restarted": True}


def set_pane_owner(pane: object, prof: object) -> dict:
    """Make a herdr pane another profile's: its mark, and the account its CLIs use."""
    from .profiles import home_of

    if not isinstance(pane, str) or not PANE_RE.match(pane):
        return {"ok": False, "error": "Panel no válido"}
    prof = str(prof or "")
    if prof and home_of(prof) is None:
        return {"ok": False, "error": "Ese perfil no existe"}
    panes = (_api("pane", "list") or {}).get("panes") or []
    info = next((p for p in panes if isinstance(p, dict) and p.get("pane_id") == pane), None)
    if info is None:
        return {"ok": False, "error": "Ese panel ya no existe"}
    owners = _load_owners()
    if owners.get(pane, "") != prof:
        # The new owner's page holds it at their size from its next report.
        release_pane(pane)
    owners[pane] = prof
    _save_owners(owners)
    try:
        _pins_dir().mkdir(parents=True, exist_ok=True)
        (_pins_dir() / pane).write_text(prof + "\n", encoding="utf-8")
    except OSError:
        return {"ok": False, "error": "No se pudo guardar"}
    return {"ok": True, **_move_cli(pane, prof, str(info.get("agent_status") or ""))}


# --- changing whose a tab is ------------------------------------------------------
# A plain tab (no herdr) has one owner: @rc_prof on its tmux session. Its
# devices size the tab, everyone else attaches with ignore-size and sees it
# scaled (tab_session.sh), and its CLIs run with the owner's account
# (web/profiles.zsh reads @rc_prof). tab_session.sh notes each client's
# profile in @rc_cprof_<tty> so their flags can follow the change.

# Output within this many seconds: the CLI in the tab is busy, leave it be.
TAB_BUSY_S = 4


def _client_key(client: str) -> str:
    return "@rc_cprof_" + re.sub(r"[^a-z0-9]", "_", client)


def set_tab_owner(tab: object, prof: object) -> dict:
    """Make a tab another profile's: its size, its dot and the account its CLIs use."""
    from .history import TAB_RE, TMUX_SOCKET, _tmux
    from .profiles import home_of

    if not isinstance(tab, str) or not TAB_RE.match(tab):
        return {"ok": False, "error": "Pestaña no válida"}
    prof = str(prof or "")
    if prof and home_of(prof) is None:
        return {"ok": False, "error": "Ese perfil no existe"}
    info = _tmux(
        TMUX_SOCKET, "display-message", "-p", "-t", f"={tab}:", "#{pane_pid}\t#{pane_current_command}\t#{window_activity}", text=True
    )
    parts = info.stdout.strip().split("\t") if info.returncode == 0 else []
    if len(parts) < 3:
        return {"ok": False, "error": "Esa pestaña ya no existe"}
    if parts[1] == "herdr":
        return {"ok": False, "error": "En herdr se cambia el usuario de cada panel"}
    _tmux(TMUX_SOCKET, "set-option", "-t", f"={tab}:", "@rc_prof", "p" + prof)
    # The new owner's devices size the tab; everyone else's only watch it.
    clients = _tmux(TMUX_SOCKET, "list-clients", "-t", f"={tab}", "-F", "#{client_name}\t#{client_width}\t#{client_height}", text=True)
    owner_size = None
    for raw in (clients.stdout or "").splitlines():
        name, _, size = raw.partition("\t")
        seen = _tmux(TMUX_SOCKET, "show-options", "-sv", _client_key(name), text=True).stdout.strip()
        if not seen:
            continue
        if seen == "p" + prof:
            _tmux(TMUX_SOCKET, "refresh-client", "-t", name, "-f", "!ignore-size")
            owner_size = owner_size or size.split("\t")
        else:
            _tmux(TMUX_SOCKET, "refresh-client", "-t", name, "-f", "ignore-size")
    if owner_size and len(owner_size) == 2:
        _tmux(TMUX_SOCKET, "resize-window", "-t", f"={tab}:", "-x", owner_size[0], "-y", owner_size[1])
    try:
        shell_pid, activity = int(parts[0]), int(parts[2])
    except ValueError:
        return {"ok": True, "restarted": False}

    def run(line: str) -> None:
        _tmux(TMUX_SOCKET, "send-keys", "-t", f"={tab}:", "-l", line)
        _tmux(TMUX_SOCKET, "send-keys", "-t", f"={tab}:", "Enter")

    working = time.time() - activity < TAB_BUSY_S
    return {"ok": True, **_restart_cli(shell_pid, prof, working, run)}
