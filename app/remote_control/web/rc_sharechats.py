#!/usr/bin/env python3
"""Share AI CLI chats between every Remote Control profile.

Each profile keeps its chats in its own HOME, so `claude --resume` (or pi's)
only listed that profile's. This hardlinks every chat into every other
profile's HOME: one file, many names, so resuming someone else's chat
continues that same chat for everyone. Claude skips symlinked transcripts,
hence hardlinks. Logins and tokens are never touched, only chat transcripts.

A chat is handed to each HOME once (shared-chats.json): a chat someone deletes
does not come back from another profile's copy.

Usage: rc_sharechats.py BASE   (BASE = the primary's ~/.local/share/remote-control)
"""

import fcntl
import json
import os
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# CLIs that keep one transcript per chat as <store>/<dir>/<id>.jsonl.
STORES = {
    "claude": Path(".claude") / "projects",
    "pi": Path(".pi") / "agent" / "sessions",
}


def homes(base: Path) -> list[Path]:
    """The primary's HOME and every profile's, in a stable order."""
    found = [base.parent.parent.parent]
    profiles = base / "profiles"
    if profiles.is_dir():
        found += sorted(p for p in profiles.iterdir() if ID_RE.match(p.name) and p.is_dir() and not p.is_symlink())
    return found


def chats(home: Path) -> dict[str, Path]:
    """<cli>/<dir>/<id>.jsonl -> file, for the transcripts this HOME holds."""
    found = {}
    for cli, store in STORES.items():
        try:
            dirs = list((home / store).iterdir())
        except OSError:
            continue
        for d in dirs:
            if d.is_symlink() or not d.is_dir():
                continue
            try:
                for f in d.iterdir():
                    if f.suffix == ".jsonl" and not f.is_symlink() and f.is_file():
                        found[f"{cli}/{d.name}/{f.name}"] = f
            except OSError:
                continue
    return found


def place(home: Path, name: str) -> Path:
    cli, rest = name.split("/", 1)
    return home / STORES[cli] / rest


def sync(base: Path) -> int:
    """Link every chat into every HOME that has not had it yet. Returns links made."""
    every = homes(base)
    if len(every) < 2:
        return 0
    state_path = base / "shared-chats.json"
    with open(base / "shared-chats.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        held = {home: chats(home) for home in every}
        made = 0
        changed = False
        for home in every:
            key = str(home)
            # Entries from before pi was shared have no CLI prefix: Claude's.
            given = {n if n.split("/", 1)[0] in STORES else f"claude/{n}" for n in state.get(key) or []}
            before = len(given)
            # What this HOME holds now counts as handed over: deleting it later sticks.
            given.update(held[home])
            for other in every:
                if other == home:
                    continue
                for name, src in held[other].items():
                    if name in given:
                        continue
                    dest = place(home, name)
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        os.link(src, dest)
                    except FileExistsError:
                        pass
                    except OSError:
                        continue
                    given.add(name)
                    made += 1
            if len(given) != before:
                state[key] = sorted(given)
                changed = True
        if changed:
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, state_path)
    return made


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    base = Path(sys.argv[1])
    if not base.is_dir():
        return 0
    try:
        sync(base)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
