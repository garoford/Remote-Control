"""Every AI agent on the machine, whoever's it is: the `rc` command.

Agents run in two places: herdr panes (one herdr server, the primary user's,
for everyone) and plain Remote Control tabs (tmux socket cf-remote), which
herdr does not see. Each runs with the account of a profile (profiles.py), so
the owner of an agent is the RC_PROFILE its CLI was started with.

An agent of a profile has that profile's HOME, where herdr finds no server:
everything here talks to the primary HOME's herdr socket explicitly.

Writing to another user's agent goes through `rc prompt`, which says who it
comes from and leaves a note the owner's devices show (workspaces payload).
All users are the same Linux user, so this is courtesy, not a lock.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import profiles
from .history import TMUX_SOCKET, close_session
from .workspaces import PROFILE_CLIS, PROFILE_RE, _children, _cmdline, change_workspaces, open_tab_beside

AGENT_CLIS = PROFILE_CLIS | {"pi"}
NOTES_KEEP = 200
NOTES_FRESH = 15 * 60
PREVIEW_MAX = 200


def primary_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


def herdr_socket() -> str:
    return str(primary_home() / ".config" / "herdr" / "herdr.sock")


def notes_file() -> Path:
    return primary_home() / ".local" / "share" / "remote-control" / "agent-messages.jsonl"


def _run(cmd: list[str], timeout: float = 5) -> subprocess.CompletedProcess | None:
    env = {**os.environ, "HOME": str(primary_home()), "HERDR_SOCKET_PATH": herdr_socket()}
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _herdr(*args: str) -> dict | None:
    out = _run(["herdr", *args])
    if not out or out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except ValueError:
        return None
    return data.get("result") if isinstance(data, dict) else None


def _tmux(*args: str, socket: str = TMUX_SOCKET) -> str:
    out = _run(["tmux", "-L", socket, *args])
    return out.stdout if out and out.returncode == 0 else ""


def label_of(pid: str) -> str:
    if not pid:
        return profiles.primary_name()
    for p in profiles.list_profiles():
        if p["id"] == pid:
            return p["label"]
    return pid


def _profile_of_home(home: str) -> str:
    base = str(primary_home() / ".local" / "share" / "remote-control" / "profiles") + "/"
    if not home.startswith(base):
        return ""
    name = home[len(base) :].split("/")[0]
    return name if PROFILE_RE.match(name) else ""


def owner_of(pid: int) -> str:
    """The profile an agent process runs with: its RC_PROFILE, else its HOME.

    Read from the CLI itself, never from what runs below it: an agent may
    launch another one with a different RC_PROFILE.
    """
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return ""
    values = dict(item.split(b"=", 1) for item in env if b"=" in item)
    prof = values.get(b"RC_PROFILE")
    if prof is not None:
        prof_s = prof.decode("ascii", "replace")
        return prof_s if PROFILE_RE.match(prof_s) else ""
    return _profile_of_home(values.get(b"HOME", b"").decode("utf-8", "replace"))


def git_root(path: str) -> str:
    """The repository a folder belongs to, or the folder itself."""
    p = Path(path)
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return str(d)
    return str(p)


def rc_command(home: Path | None = None) -> Path:
    return (home or primary_home()) / ".local" / "share" / "remote-control" / "bin" / "rc"


def write_rc_command(home: Path | None = None) -> None:
    """Install `rc` where profiles.zsh and tab shells put it on PATH."""
    path = rc_command(home)
    package_root = Path(__file__).resolve().parent.parent
    text = f'#!/bin/sh\nPYTHONPATH="{package_root}" exec python3 -m remote_control.agents "$@"\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    tmp = path.with_name(".rc.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, path)


# --- who is asking ----------------------------------------------------------


def caller() -> dict:
    """Profile and pane of whoever runs `rc` (read before HOME is touched)."""
    prof = os.environ.get("RC_PROFILE")
    if prof is None or (prof and not PROFILE_RE.match(prof)):
        prof = _profile_of_home(os.environ.get("HOME", ""))
    pane = ""
    if os.environ.get("HERDR_PANE_ID"):
        pane = os.environ["HERDR_PANE_ID"]
    elif os.environ.get("TMUX_PANE") and f"/{TMUX_SOCKET}," in os.environ.get("TMUX", ""):
        session = _tmux("display-message", "-p", "-t", os.environ["TMUX_PANE"], "#{session_name}").strip()
        pane = f"rc:{session}" if session else ""
    return {"profile": prof, "pane": pane}


# --- finding agents ---------------------------------------------------------


def _cli_name(argv: list[str]) -> str:
    for part in argv[:2]:
        name = os.path.basename(part)
        if name in AGENT_CLIS:
            return name
    return ""


def find_cli(pane_pid: int) -> tuple[str, int]:
    """(name, pid) of the AI CLI running in a pane (or under its shell), or ("", 0)."""
    queue = [pane_pid]
    seen = 0
    while queue and seen < 32:
        pid = queue.pop(0)
        seen += 1
        name = _cli_name(_cmdline(pid))
        if name:
            return name, pid
        queue.extend(_children(pid))
    return "", 0


def herdr_agents() -> list[dict]:
    result = _herdr("agent", "list") or {}
    out = []
    for a in result.get("agents") or []:
        if not isinstance(a, dict) or not a.get("pane_id"):
            continue
        info = (_herdr("pane", "process-info", "--pane", a["pane_id"]) or {}).get("process_info") or {}
        shell = info.get("shell_pid")
        cli_pid = find_cli(shell)[1] if isinstance(shell, int) else 0
        owner = owner_of(cli_pid) if cli_pid else ""
        out.append(
            {
                "id": a["pane_id"],
                "where": "herdr",
                "cli": str(a.get("agent") or ""),
                "name": str(a.get("name") or a.get("terminal_title_stripped") or ""),
                "owner": owner,
                "cwd": str(a.get("foreground_cwd") or a.get("cwd") or ""),
                "status": str(a.get("agent_status") or ""),
                "pid": cli_pid,
            }
        )
    return out


# A plain tab has no herdr state. tmux stamps every window with the time of
# its last output (window_activity), and a working CLI keeps redrawing its
# spinner and timer: output in the last ACTIVE_S seconds means working. A
# quiet one that shows a question waits for its user.
ACTIVE_S = 3
BLOCKED_RE = re.compile(
    r"Do you want to (proceed|make this edit|create|run|allow)|Allow (this|command|once|always)|"
    r"Would you like to (run|allow|proceed)|Yes, and don't ask again|Approve\b|\(y/n\)|\[y/N\]|\[Y/n\]|"
    r"Do you trust|trust (this|the) (folder|directory|files)",
    re.I,
)


def _status_from(activity: int, target: str, now: float | None = None, socket: str = TMUX_SOCKET) -> str:
    now = time.time() if now is None else now
    if now - activity < ACTIVE_S:
        return "working"
    screen = _tmux("capture-pane", "-p", "-t", target, socket=socket)
    tail = "\n".join(screen.rstrip().splitlines()[-15:])
    return "blocked" if BLOCKED_RE.search(tail) else "idle"


def tab_state(agent: dict) -> tuple[str, int]:
    """(status, time of its last output) of a plain tab agent."""
    target = f"={agent['id'][3:]}:"
    raw = _tmux("display-message", "-p", "-t", target, "#{window_activity}").strip()
    activity = int(raw) if raw.isdigit() else 0
    return _status_from(activity, target), activity


def tab_agents(socket: str = TMUX_SOCKET) -> list[dict]:
    """Agents in plain Remote Control tabs (herdr's own views are skipped)."""
    out = []
    raw = _tmux("list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}\t#{pane_current_path}\t#{window_activity}", socket=socket)
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or parts[0].startswith("hv-") or not parts[1].isdigit():
            continue
        cli, pid = find_cli(int(parts[1]))
        if not cli:
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = parts[2]
        out.append(
            {
                "id": f"rc:{parts[0]}",
                "where": "tab",
                "cli": cli,
                "name": "",
                "owner": owner_of(pid),
                "cwd": cwd,
                "status": _status_from(int(parts[3]) if parts[3].isdigit() else 0, f"={parts[0]}:", socket=socket),
                "pid": pid,
            }
        )
    return out


def list_agents(me: dict | None = None) -> list[dict]:
    me = me or {"profile": "", "pane": ""}
    agents = herdr_agents() + tab_agents()
    for a in agents:
        a["owner_label"] = label_of(a["owner"])
        a["root"] = git_root(a["cwd"]) if a["cwd"] else ""
        a["you"] = bool(me["pane"]) and a["id"] == me["pane"]
    return agents


def same_project(agents: list[dict], path: str) -> list[dict]:
    root = git_root(path)
    return [a for a in agents if not a["you"] and a["root"] == root]


# --- notes for the owner's devices ------------------------------------------


def add_note(note: dict) -> None:
    path = notes_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = (lines + [json.dumps(note, ensure_ascii=False)])[-NOTES_KEEP:]
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def recent_notes(now: float | None = None) -> list[dict]:
    """Notes of the last minutes, for workspaces_payload."""
    now = time.time() if now is None else now
    try:
        lines = notes_file().read_text(encoding="utf-8").splitlines()[-40:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            note = json.loads(line)
        except ValueError:
            continue
        if isinstance(note, dict) and isinstance(note.get("t"), (int, float)) and now - note["t"] < NOTES_FRESH:
            out.append(note)
    return out


# --- talking to an agent ----------------------------------------------------


def _find(agent_id: str, me: dict) -> dict | None:
    return next((a for a in list_agents(me) if a["id"] == agent_id), None)


def read_screen(agent: dict, lines: int) -> str:
    if agent["where"] == "herdr":
        out = _run(["herdr", "agent", "read", agent["id"], "--source", "recent-unwrapped", "--lines", str(lines)])
        raw = out.stdout if out and out.returncode == 0 else ""
    else:
        raw = _tmux("capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", f"={agent['id'][3:]}:")
    return tidy_screen(raw, lines)


# A line that is only box drawing: the rules around a CLI's input box.
RULE_RE = re.compile(r"^\s*[─━═┄┈╌]{8,}\s*$")


def tidy_screen(raw: str, lines: int) -> str:
    """A screen as an agent should read it: no padding spaces, no runs of
    empty rows, and without the input box and status bar under the last
    output (they cost tokens on every read and say nothing)."""
    rows = [r.rstrip() for r in raw.splitlines()]
    # The CLI's own chrome starts at the first rule of the bottom rows.
    for i in range(max(0, len(rows) - 12), len(rows)):
        if RULE_RE.match(rows[i]):
            rows = rows[:i]
            break
    out: list[str] = []
    for row in rows:
        if row or (out and out[-1]):
            out.append(row)
    while out and not out[-1]:
        out.pop()
    out = out[-lines:]
    return "\n".join(out) + "\n" if out else ""


# --- its reply, from the CLI's own session file ----------------------------
# A CLI draws on the alternate screen, so tmux keeps no history: a reply
# taller than the tab (a phone's, often) is lost, and the screen is wrapped
# to its width and framed by the CLI's chrome. The session file each CLI
# writes has the exact text. `rc` remembers what it sent and returns what
# the agent wrote after that message.

LAST_SENT_KEEP = 50
# A longer reply keeps its end, where a summary goes.
REPLY_MAX = 12000


def _sent_file() -> Path:
    return primary_home() / ".local" / "share" / "remote-control" / "agent-last-prompt.json"


def remember_sent(agent_id: str, text: str, at: float) -> None:
    path = _sent_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.pop(agent_id, None)
    data[agent_id] = {"text": text, "t": at}
    data = dict(list(data.items())[-LAST_SENT_KEEP:])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def last_sent(agent_id: str) -> dict | None:
    try:
        entry = json.loads(_sent_file().read_text(encoding="utf-8")).get(agent_id)
    except (OSError, ValueError, AttributeError):
        return None
    return entry if isinstance(entry, dict) and isinstance(entry.get("text"), str) else None


def _home_of_owner(owner: str) -> Path:
    return profiles.home_of(owner) or primary_home()


def _session_files(agent: dict, since: float) -> list[Path]:
    """The CLI's session files written since ``since``, newest first."""
    home = _home_of_owner(agent["owner"])
    cli = agent["cli"]
    if cli == "claude":
        from .workspaces import _claude_session

        sid = _claude_session(agent["pid"], home) if agent.get("pid") else ""
        if sid:
            exact = list((home / ".claude" / "projects").glob(f"*/{sid}.jsonl"))
            if exact:
                return exact
        found = list((home / ".claude" / "projects").glob("*/*.jsonl"))
    elif cli == "pi":
        found = list((home / ".pi" / "agent" / "sessions").glob("*/*.jsonl"))
    elif cli == "codex":
        found = list((home / ".codex" / "sessions").glob("*/*/*/rollout-*.jsonl"))
    else:
        return []
    fresh = []
    for f in found:
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime >= since - 2:
            fresh.append((mtime, f))
    return [f for _m, f in sorted(fresh, reverse=True)[:8]]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _turns(path: Path) -> list[tuple[str, str]]:
    """(role, text) of every message, for the formats of Claude Code
    (type/message), pi (type message/message) and Codex (payload)."""
    out = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else d.get("payload")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [
                str(c.get("text") or "")
                for c in content
                if isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text")
            ]
        else:
            texts = []
        text = "\n\n".join(t for t in texts if t.strip())
        if text:
            out.append((role, text))
    return out


def reply_since(agent: dict, sent: str, since: float) -> str | None:
    """What the agent wrote after the message ``sent``, or None if no
    session file has that message."""
    needle = _norm(sent)[:80]
    if not needle:
        return None
    for path in _session_files(agent, since):
        turns = _turns(path)
        anchor = None
        for i in range(len(turns) - 1, -1, -1):
            if turns[i][0] == "user" and needle in _norm(turns[i][1]):
                anchor = i
                break
        if anchor is None:
            continue
        said = [text for role, text in turns[anchor + 1 :] if role == "assistant"]
        return "\n\n".join(said).strip()
    return None


def last_reply(agent: dict) -> str | None:
    """Its reply to the last message `rc` sent it (None: nothing to go on)."""
    sent = last_sent(agent["id"])
    reply = reply_since(agent, sent["text"], sent["t"]) if sent else None
    if reply and len(reply) > REPLY_MAX:
        cut = len(reply) - REPLY_MAX
        reply = f"[… {cut} earlier characters left out]\n" + reply[cut:]
    return reply


# How long `--wait` waits by default: under the 10 minutes a Claude Code
# command may run. 0 waits forever (for a command run in the background).
WAIT_DEFAULT = 540
# herdr states that end a wait. blocked: it asks for input or a permission.
FINISHED = ("idle", "done", "blocked")
# A prompt a plain tab agent has not reacted to in this time never started.
START_S = 10
# Silence that ends a turn when the CLI's session file already holds a reply
# (reply_since); without one it takes ACTIVE_S.
QUICK_S = 1.5


def _until(timeout: int) -> list[str]:
    args = [x for state in FINISHED for x in ("--until", state)]
    return args + (["--timeout", str(timeout * 1000)] if timeout else [])


def _herdr_status(out: subprocess.CompletedProcess | None) -> str:
    """The state herdr reports after a wait, or "error:<code>"."""
    if out is None:
        return "error:timeout"
    try:
        data = json.loads(out.stdout or out.stderr)
    except ValueError:
        return "error:unknown" if out.returncode else "idle"
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return f"error:{data['error'].get('code') or 'unknown'}"
    agent = ((data.get("result") or {}).get("agent") or {}) if isinstance(data, dict) else {}
    return str(agent.get("agent_status") or ("error:unknown" if out.returncode else "idle"))


def _wait_tab(agent: dict, timeout: int, sent_at: int | None = None) -> str:
    """Until a tab agent is idle or blocked. ``sent_at``: when a prompt was
    typed; the turn it starts counts only once it reacted after that."""
    start = time.monotonic()
    while True:
        status, activity = tab_state(agent)
        waited = time.monotonic() - start
        if sent_at is not None and activity < sent_at:
            if waited >= START_S:
                return "error:agent_prompt_stalled"
        elif status != "working" and (sent_at is None or time.time() - sent_at > ACTIVE_S):
            return status
        elif sent_at is not None and time.time() - activity >= QUICK_S and last_reply(agent):
            # Quiet, and its session file already has the reply: done,
            # without the full ACTIVE_S of silence.
            return _status_from(0, f"={agent['id'][3:]}:")
        if timeout and waited >= timeout:
            return "error:timeout"
        time.sleep(0.5)


def wait_for(agent: dict, timeout: int = WAIT_DEFAULT) -> str:
    """Block until the agent finishes its turn: idle, done, blocked or error:*."""
    if agent["where"] == "herdr":
        out = _run(["herdr", "agent", "wait", agent["id"], *_until(timeout)], timeout=timeout + 15 if timeout else None)
        return _herdr_status(out)
    return _wait_tab(agent, timeout)


def prompt(agent: dict, text: str, me: dict, wait: int | None = None) -> dict:
    """Send text to an agent; a note for its owner if it is someone else's.

    With ``wait`` (seconds, 0 = no limit) it also waits for the turn that
    text starts to end. Returns {"sent", "foreign", "status"}.
    """
    foreign = agent["owner"] != me["profile"]
    if foreign:
        text = f"[Mensaje del agente de {label_of(me['profile'])}] {text}"
    status = ""
    remember_sent(agent["id"], text, time.time())
    if agent["where"] == "herdr":
        cmd = ["herdr", "agent", "prompt", agent["id"], text]
        limit: float | None = 15
        if wait is not None:
            # herdr waits for a working state first, so the old turn never counts.
            cmd += ["--wait", *_until(wait)]
            limit = wait + 15 if wait else None
        out = _run(cmd, timeout=limit)
        if wait is not None:
            status = _herdr_status(out)
            # Rejected before anything was typed.
            if status in ("error:agent_blocked", "error:agent_not_found"):
                return {"sent": False, "foreign": foreign, "status": status}
        elif not out or out.returncode != 0:
            return {"sent": False, "foreign": foreign, "status": "error:send"}
    else:
        target = f"={agent['id'][3:]}:"
        sent_at = int(time.time())
        _tmux("send-keys", "-t", target, "-l", text)
        time.sleep(0.3)
        _tmux("send-keys", "-t", target, "Enter")
    if foreign:
        add_note(
            {
                "t": time.time(),
                "from": me["profile"],
                "fromLabel": label_of(me["profile"]),
                "fromPane": me["pane"],
                "to": agent["owner"],
                "toLabel": agent["owner_label"],
                "target": agent["id"],
                "cli": agent["cli"],
                "text": text[:PREVIEW_MAX],
            }
        )
        if agent["where"] == "herdr":
            _run(
                [
                    "herdr", "notification", "show", f"Agente de {label_of(me['profile'])} → {agent['owner_label']}",
                    "--body", f"Le escribió a {agent['cli']} en {agent['id']}", "--sound", "request",
                ]
            )
    if wait is not None and agent["where"] != "herdr":
        status = _wait_tab(agent, wait, sent_at)
    return {"sent": True, "foreign": foreign, "status": status}


# --- delegating to a new tab ------------------------------------------------
# An agent in a plain Remote Control tab opens another tab right after its
# own, in the same workspace, starts an AI CLI there with its user's account
# and hands it the task. In herdr the same is a pane (herdr pane split).

# A CLI that has not drawn its prompt by then is reported, not waited for.
READY_S = 60


def _tab_of(pane: str) -> str:
    return pane[3:] if pane.startswith("rc:") else ""


def _wait_ready(agent: dict, limit: float = READY_S) -> str:
    """Until the new CLI is up and quiet: idle, blocked (a question first,
    like trusting the folder) or error:*."""
    target = f"={agent['id'][3:]}:"
    start = time.monotonic()
    while time.monotonic() - start < limit:
        time.sleep(1)
        pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").strip()
        if not pid.isdigit():
            return "error:tab_closed"
        if not find_cli(int(pid))[0]:
            continue
        status, _activity = tab_state(agent)
        if status != "working":
            return status
    pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").strip()
    return "error:timeout" if pid.isdigit() and find_cli(int(pid))[0] else "error:cli_not_started"


def spawn(cli: str, cli_args: list[str], me: dict, cwd: str) -> tuple[dict | None, str]:
    """Open a tab next to the caller's with ``cli`` running as the caller's
    user. Returns (agent, status from _wait_ready) or (None, error)."""
    beside = _tab_of(me["pane"])
    tab = open_tab_beside(beside, Path(cwd), env={"RC_PROFILE": me["profile"]})
    if not tab:
        return None, "error:tab_not_started"
    target = f"={tab}:"
    # The size belongs to the profile of the tab it came from (tab_session.sh).
    size_owner = _tmux("show-options", "-t", f"={beside}:", "-qv", "@rc_prof").strip()
    if size_owner:
        _tmux("set-option", "-t", target, "@rc_prof", size_owner)
    # Who may `rc close` it.
    _tmux("set-option", "-t", target, "@rc_spawned_by", me["profile"] or "-")
    _tmux("send-keys", "-t", target, "-l", shlex.join([cli, *cli_args]))
    _tmux("send-keys", "-t", target, "Enter")
    agent = {
        "id": f"rc:{tab}", "where": "tab", "cli": cli, "name": "", "owner": me["profile"],
        "owner_label": label_of(me["profile"]), "cwd": cwd, "status": "", "you": False, "pid": 0,
    }
    return agent, _wait_ready(agent)


def close_spawned(agent_id: str, me: dict) -> str:
    """Close a tab `rc spawn` opened for this user. "" or why not."""
    tab = _tab_of(agent_id)
    if not tab or agent_id == me["pane"]:
        return "only tabs opened with `rc spawn` can be closed"
    by = _tmux("show-options", "-t", f"={tab}:", "-qv", "@rc_spawned_by").strip()
    if not by:
        return "it was not opened with `rc spawn`; ask your user to close it"
    if by != (me["profile"] or "-"):
        return "another user opened it"
    if not close_session(tab):
        return "tmux did not close it"
    change_workspaces({"op": "forget", "tab": tab})
    return ""


def report(agent: dict, status: str, lines: int, waited: int) -> int:
    """What a wait ended with, the agent's screen and the exit code."""
    if status in ("idle", "done"):
        reply = last_reply(agent)
        if reply:
            print(f"{agent['id']} finished. Its reply:\n")
            print(reply)
        else:
            print(f"{agent['id']} finished: {status}. Last lines of its screen:\n")
            sys.stdout.write(read_screen(agent, lines))
        return 0
    if status == "blocked":
        print(f"{agent['id']} is blocked: it waits for input or a permission. Its screen:\n")
        sys.stdout.write(read_screen(agent, lines))
        return 2
    if status == "error:timeout":
        print(f"{agent['id']} is still working after {waited}s. Keep waiting with `rc wait {agent['id']}`.")
        return 3
    if status == "error:agent_blocked":
        print(f"rc: {agent['id']} is blocked (input or a permission pending); nothing was sent.", file=sys.stderr)
        return 2
    if status == "error:agent_prompt_stalled":
        print(f"rc: {agent['id']} did not start working; check it with `rc read {agent['id']}`.", file=sys.stderr)
        return 1
    print(f"rc: waiting for {agent['id']} failed ({status})", file=sys.stderr)
    return 1


# --- command line -----------------------------------------------------------


def _table(agents: list[dict]) -> str:
    if not agents:
        return "(no agents)"
    rows = [("ID", "CLI", "OWNER", "STATUS", "FOLDER")]
    for a in agents:
        cli = a["cli"] + (f" ({a['name']})" if a["name"] else "")
        rows.append((a["id"], cli, a["owner_label"], a["status"] or "-", a["cwd"] + ("  ← you" if a["you"] else "")))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    return "\n".join("  ".join(r[i].ljust(widths[i]) for i in range(4)) + "  " + r[4] for r in rows)


def main(argv: list[str] | None = None) -> int:
    me = caller()
    # From here on paths are the primary user's: profiles and herdr live there.
    os.environ["HOME"] = str(primary_home())
    parser = argparse.ArgumentParser(prog="rc", description="Agents running on this Remote Control machine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("agents", help="list every agent (herdr and plain tabs) with its owner")
    p_list.add_argument("--here", nargs="?", const=os.getcwd(), default=None, metavar="PATH",
                        help="only the other agents in the same project (git repo) as PATH (default: here)")
    p_list.add_argument("--json", action="store_true")
    p_read = sub.add_parser("read", help="show an agent's screen")
    p_read.add_argument("id")
    p_read.add_argument("--lines", type=int, default=60)
    p_prompt = sub.add_parser("prompt", help="send a prompt to an agent")
    p_prompt.add_argument("id")
    p_prompt.add_argument("text")
    p_prompt.add_argument("--wait", nargs="?", type=int, const=WAIT_DEFAULT, default=None, metavar="SECONDS",
                          help=f"wait until it finishes and print its screen (default {WAIT_DEFAULT}s, 0 = no limit)")
    p_prompt.add_argument("--lines", type=int, default=40, help="screen lines printed after --wait")
    p_wait = sub.add_parser("wait", help="wait until an agent finishes its turn, then print its screen")
    p_wait.add_argument("id")
    p_wait.add_argument("--timeout", type=int, default=WAIT_DEFAULT, help=f"seconds (default {WAIT_DEFAULT}, 0 = no limit)")
    p_wait.add_argument("--lines", type=int, default=40)
    p_spawn = sub.add_parser("spawn", help="open a tab next to yours (same workspace) with an AI CLI; not in herdr")
    p_spawn.add_argument("cli", choices=sorted(AGENT_CLIS))
    p_spawn.add_argument("cli_args", nargs="*", metavar="ARG", help="arguments for the CLI (put them after --)")
    p_spawn.add_argument("--cwd", default=os.getcwd(), help="its folder (default: here)")
    p_spawn.add_argument("--prompt", help="task to send once it is ready")
    p_spawn.add_argument("--wait", nargs="?", type=int, const=WAIT_DEFAULT, default=None, metavar="SECONDS",
                         help="with --prompt: wait until it finishes, as rc prompt --wait")
    p_spawn.add_argument("--lines", type=int, default=40)
    p_reply = sub.add_parser("reply", help="its reply to the last rc prompt, without waiting")
    p_reply.add_argument("id")
    p_close = sub.add_parser("close", help="close a tab you opened with rc spawn")
    p_close.add_argument("id")
    sub.add_parser("whoami", help="your profile and pane")
    args = parser.parse_args(argv)

    if args.cmd == "spawn":
        return _spawn_cmd(args, me)
    if args.cmd == "close":
        why = close_spawned(args.id, me)
        if why:
            print(f"rc: {why}", file=sys.stderr)
            return 1
        print(f"Closed {args.id}.")
        return 0

    if args.cmd == "whoami":
        print(json.dumps({**me, "label": label_of(me["profile"]), "home": str(profiles.home_of(me["profile"]) or primary_home())}))
        return 0
    if args.cmd == "agents":
        agents = list_agents(me)
        if args.here is not None:
            agents = same_project(agents, args.here)
        if args.json:
            print(json.dumps(agents, ensure_ascii=False, indent=1))
        else:
            print(_table(agents))
            if args.here is not None:
                print(f"\n{len(agents)} other agent(s) in {git_root(args.here)}")
        return 0
    agent = _find(args.id, me)
    if not agent:
        print(f"rc: no agent {args.id} (see `rc agents`)", file=sys.stderr)
        return 1
    if args.cmd == "read":
        sys.stdout.write(read_screen(agent, max(1, min(args.lines, 2000))))
        return 0
    if args.cmd == "reply":
        reply = last_reply(agent)
        if reply is None:
            why = "rc has sent it nothing" if last_sent(agent["id"]) is None else "its session file has no reply"
            print(f"rc: no reply ({why}); see its screen with `rc read {agent['id']}`", file=sys.stderr)
            return 1
        print(reply or "(it has not written anything since your last message)")
        return 0
    if agent["you"]:
        print("rc: that is you", file=sys.stderr)
        return 1
    lines = max(1, min(args.lines, 2000))
    if args.cmd == "wait":
        timeout = max(0, args.timeout)
        return report(agent, wait_for(agent, timeout), lines, timeout)
    return _send(agent, args.text, me, args.wait, lines)


def _send(agent: dict, text: str, me: dict, wait: int | None, lines: int) -> int:
    wait = None if wait is None else max(0, wait)
    result = prompt(agent, text, me, wait=wait)
    if not result["sent"]:
        if result["status"] == "error:agent_blocked":
            return report(agent, result["status"], lines, 0)
        print(f"rc: could not send to {agent['id']} ({result['status']})", file=sys.stderr)
        return 1
    if result["foreign"]:
        print(f"Sent. {agent['owner_label']} owns that agent and was notified.")
    else:
        print("Sent.")
    if wait is None:
        return 0
    return report(agent, result["status"], lines, wait)


def _spawn_cmd(args: argparse.Namespace, me: dict) -> int:
    if os.environ.get("HERDR_PANE_ID"):
        print(
            "rc: you are in a herdr pane: open another pane instead (`herdr pane split --current "
            "--direction right --cwd \"$PWD\" --no-focus`, then `herdr agent start <name> --kind <kind> "
            "--pane <new-pane-id>`) and use rc prompt/wait with its id.",
            file=sys.stderr,
        )
        return 1
    if not _tab_of(me["pane"]):
        print("rc: spawn works from a Remote Control tab (yours is not one).", file=sys.stderr)
        return 1
    cwd = os.path.abspath(args.cwd)
    if not os.path.isdir(cwd):
        print(f"rc: no folder {cwd}", file=sys.stderr)
        return 1
    lines = max(1, min(args.lines, 2000))
    agent, status = spawn(args.cli, args.cli_args, me, cwd)
    if agent is None:
        print(f"rc: could not open a tab ({status})", file=sys.stderr)
        return 1
    print(f"Opened {agent['id']} ({args.cli}) next to your tab. Close it when done: rc close {agent['id']}")
    if status == "blocked":
        print("It asks something before it starts (trusting the folder, a login…); nothing was sent. Its screen:\n")
        sys.stdout.write(read_screen(agent, lines))
        return 2
    if status.startswith("error:"):
        print(f"rc: {args.cli} is not ready ({status[6:]}); check it with `rc read {agent['id']}`.", file=sys.stderr)
        return 1
    if args.prompt is None:
        return 0
    return _send(agent, args.prompt, me, args.wait, lines)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
