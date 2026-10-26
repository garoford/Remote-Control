"""Shared-machine context for AI CLIs while Remote Control runs.

Several people share one Remote Control, each with their own CLI logins
(profiles.py: an extra profile is just another HOME). An agent that does not
know this looks for tokens in the wrong HOME, launches other agents with the
account of whoever typed last, and cannot tell whose the agents in herdr are.

While the tunnel runs, a managed block with that context is kept in the global
instructions file of every CLI, in the primary HOME and in each profile's
HOME. Only the block between the markers is ever touched; the user's own text
stays as it is, and a file that held nothing else is removed again on stop.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import profiles

BEGIN = "<!-- remote-control:begin (managed by Remote Control, do not edit) -->"
END = "<!-- remote-control:end -->"
_BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)

# (global instructions file relative to a HOME, CLI dir that marks it in use,
# executables). A file is written where its CLI is installed or already used.
TARGETS = (
    (".claude/CLAUDE.md", ".claude", ("claude",)),
    (".codex/AGENTS.md", ".codex", ("codex",)),
    (".gemini/GEMINI.md", ".gemini", ("gemini", "agy")),
    (".qwen/QWEN.md", ".qwen", ("qwen",)),
    (".config/opencode/AGENTS.md", ".config/opencode", ("opencode",)),
    (".copilot/copilot-instructions.md", ".copilot", ("copilot",)),
    (".pi/agent/AGENTS.md", ".pi", ("pi",)),
)
# Cursor Agent has no global rules file: it reads AGENTS.md in every folder
# from the project up to /, so the primary HOME's one reaches projects there.
CURSOR_TARGET = ("AGENTS.md", ".cursor", ("agent", "cursor-agent"))


def _homes() -> list[tuple[str, str, Path]]:
    """(profile id, label, HOME) for everyone; "" is the primary user."""
    out = [("", profiles.primary_name(), Path.home())]
    for p in profiles.list_profiles():
        home = profiles.home_of(p["id"])
        if home:
            out.append((p["id"], p["label"], home))
    return out


def _who(pid: str, label: str) -> str:
    return f"`{label}` (primary user)" if not pid else f"`{label}` (profile `{pid}`)"


def render(me: str | None) -> str:
    """The block for the HOME of profile ``me`` ("" is the primary user).

    Each HOME has its own file, so the CLI reading it runs as that user: the
    block says so outright. None is the one file every user's Cursor Agent
    reads (CURSOR_TARGET), which cannot tell them apart.
    """
    homes = _homes()
    users = []
    for pid, label, home in homes:
        mark = "  ← you" if pid == me else ""
        users.append(f"- {_who(pid, label)}: HOME `{home}`, launch with `RC_PROFILE={pid}`{mark}")
    primary_home = homes[0][2]
    rc = primary_home / ".local" / "share" / "remote-control" / "bin" / "rc"
    if me is None:
        you = [
            "**Who you are:** this file is shared by every user, so it cannot say. In Cursor Agent",
            "you are the primary user unless `RC_PROFILE` is set in your environment (then you are",
            "that profile). Other CLIs get their own file that says it directly.",
        ]
    else:
        mine = next(((pid, label, home) for pid, label, home in homes if pid == me), homes[0])
        you = [
            f"**You are {_who(mine[0], mine[1])}.** Your HOME is `{mine[2]}` and your",
            f"`RC_PROFILE` is {f'`{mine[0]}`' if mine[0] else 'empty'}. This is certain: this file lives in that HOME and",
            "only a CLI started with it reads it, so do not check it again. Your logins and",
            "tokens are the ones in that HOME.",
        ]
    return "\n".join(
        [
            BEGIN,
            "## Remote Control: this machine is shared",
            "",
            *you,
            "",
            "### Other agents: yes, you can see them",
            "",
            "Other AI agents run here at the same time, yours and other users', in herdr panes and in",
            "plain Remote Control tabs. When asked whether you can see or reach other agents, the",
            f"answer is yes: use `rc` (`{rc}` if it is not on PATH).",
            "",
            "- `rc agents`: every agent with its id, CLI, owner (which user), status and folder.",
            "- `rc agents --here`: the other agents working in your project (same git repo). Run it",
            "  before editing a shared project and coordinate if someone else is in it.",
            "- `rc read <id>`: that agent's screen. `rc prompt <id> \"text\"`: write to it. For an",
            "  agent of another user, ask your user first; its owner is notified. Always use",
            "  `rc prompt`, not `herdr agent prompt`, so they know who wrote.",
            "- Delegating a task whose result you need: `rc prompt <id> \"task\" --wait`. It returns when",
            "  that agent finishes its turn and prints the end of its screen (exit 0 finished, 2 it",
            "  is blocked on input or a permission, 3 still working after the limit, 540s by default).",
            "  For a long task run `rc prompt <id> \"task\" --wait 0` (or `rc wait <id> --timeout 0`)",
            "  as a background command, so you are told when it ends and can keep working. Ask it",
            "  to end its answer with a short summary, or to write it to a file you then read.",
            "  A plain message that needs no answer goes without `--wait`.",
            "- No agent to delegate to, in a plain Remote Control tab (not herdr):",
            "  `rc spawn <cli> --prompt \"task\" --wait` opens a tab next to yours in the same workspace,",
            "  starts that CLI (claude, codex…) with your user's account and hands it the task, with the",
            "  same exit codes as `--wait`. Then `rc read`/`prompt`/`wait` it like any agent and",
            "  `rc close <id>` when you are done. Exit 2 right after it opens: the CLI asks something",
            "  first (trust this folder, a login); tell your user. In herdr, open a pane instead (below).",
            "- `rc whoami`: your profile and pane. Full herdr control: `herdr --skill`.",
            "",
            "Several people use this machine at the same time through Remote Control, each with",
            "their own AI CLI logins. They all are the same Linux user: a profile is just another",
            "HOME, and its CLI logins and tokens (`.claude`, `.codex`, `.cursor`, `.gemini`,",
            "`.config`…) live inside it. Never read, copy or move another user's tokens.",
            "",
            "Users right now:",
            *users,
            "",
            "- Projects are shared and keep their absolute paths. `~` is your own HOME, not",
            f"  `{primary_home}`; use absolute paths for shared files.",
            "- Launch an agent as a given user: `RC_PROFILE=<id> claude` (`RC_PROFILE= claude` for",
            "  the primary). Without it a CLI takes the profile of whichever device typed last in",
            "  Remote Control, which may be someone else.",
            "- herdr belongs to the primary user: one server for everyone. Open a pane for a user's",
            "  agent with `herdr pane split --current --direction right --cwd \"$PWD\" --no-focus --env RC_PROFILE=<id>`",
            "  and then `herdr agent start <name> --kind <kind> --pane <new-pane-id>`.",
            "- herdr integrations live in each HOME: `HOME=<home> herdr integration install <kind>`.",
            END,
            "",
        ]
    )


def _in_use(home: Path, marker: str, bins: tuple[str, ...]) -> bool:
    return (home / marker).is_dir() or any(shutil.which(b) for b in bins)


def _targets() -> list[tuple[Path, str | None]]:
    """Every file to write, with whose block it gets (render)."""
    paths: list[tuple[Path, str | None]] = []
    for pid, _label, home in _homes():
        for rel, marker, bins in TARGETS:
            if _in_use(home, marker, bins):
                paths.append((home / rel, pid))
        if not pid and _in_use(home, CURSOR_TARGET[1], CURSOR_TARGET[2]):
            paths.append((home / CURSOR_TARGET[0], None))
    return paths


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.rc-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _put_block(path: Path, block: str) -> None:
    try:
        old = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        old = ""
    if _BLOCK_RE.search(old):
        new = _BLOCK_RE.sub(lambda _m: block, old, count=1)
    else:
        new = block + ("\n" + old if old.strip() else "")
    if new != old:
        _write(path, new)


def _drop_block(path: Path) -> None:
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        return
    if not _BLOCK_RE.search(old):
        return
    new = _BLOCK_RE.sub("", old).lstrip("\n")
    if new.strip():
        _write(path, new)
    else:
        path.unlink(missing_ok=True)


def sync() -> None:
    """Write the current context everywhere (tunnel start, profiles change)."""
    for path, me in _targets():
        try:
            _put_block(path, render(me))
        except OSError:
            pass


def clear() -> None:
    """Take the block out again (tunnel stopped)."""
    for _pid, _label, home in _homes():
        for rel in [t[0] for t in TARGETS] + [CURSOR_TARGET[0]]:
            _drop_block(home / rel)
