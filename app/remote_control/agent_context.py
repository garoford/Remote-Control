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
            "- herdr belongs to the primary user: one server for everyone, so `herdr agent list`",
            "  shows every user's agents. Open a pane for a user's agent with",
            "  `herdr pane split --current --direction right --cwd \"$PWD\" --no-focus --env RC_PROFILE=<id>`",
            "  and then `herdr agent start <name> --kind <kind> --pane <new-pane-id>`.",
            "- Whose an agent is: `herdr pane process-info --pane <pane-id>` gives its pid, then",
            "  `tr '\\0' '\\n' < /proc/<pid>/environ | grep -E '^(HOME|RC_PROFILE)='`. Ask your user",
            "  before prompting an agent that belongs to someone else.",
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
