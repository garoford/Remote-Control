#!/usr/bin/env python3
"""Share AI CLI chats between every Remote Control profile.

Each profile keeps its chats in its own HOME, so `claude --resume` (or pi's)
only listed that profile's. This hardlinks every chat into every other
profile's HOME: one file, many names, so resuming someone else's chat
continues that same chat for everyone. Claude skips symlinked transcripts,
hence hardlinks. Logins and tokens are never touched, only chat transcripts.

A chat is handed to each HOME once (shared-chats.json): a chat someone deletes
does not come back from another profile's copy.

Cursor Agent and Antigravity (agy) keep each chat in an SQLite database next
to its journal (-wal/-shm): a hardlinked database would get one journal per
HOME. Their whole chat folder is shared instead: in every profile it is a
symlink to the primary's, so all of them open the very same files. What a
profile already had is moved in there first (nothing is deleted).

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


# Folders (or files) of a HOME that every profile shares with the primary's.
SHARED = (
    Path(".cursor") / "chats",
    Path(".gemini") / "antigravity-cli" / "conversations",
    Path(".gemini") / "antigravity-cli" / "brain",
    Path(".gemini") / "antigravity-cli" / "conversation_summaries.db",
)


def _keep_aside(path: Path, tag: str) -> Path:
    """A free name beside path for something that would collide."""
    n = 1
    while True:
        other = path.with_name(f"{path.stem}.{tag}{'' if n == 1 else n}{path.suffix}")
        if not other.exists() and not other.is_symlink():
            return other
        n += 1


def _move_db(src: Path, dest: Path) -> None:
    """An SQLite database with its journal files."""
    for side in ("", "-wal", "-shm"):
        part = src.with_name(src.name + side)
        if part.exists():
            os.rename(part, dest.with_name(dest.name + side))


def share_folders(base: Path) -> int:
    """Every profile's SHARED paths become symlinks to the primary's. Returns links made."""
    every = homes(base)
    primary, made = every[0], 0
    for home in every[1:]:
        for rel in SHARED:
            target, link = primary / rel, home / rel
            is_db = rel.suffix == ".db"
            # Folders exist up front; a database is made by its CLI, through the
            # link, in the primary's folder.
            (target.parent if is_db else target).mkdir(parents=True, exist_ok=True)
            if link.is_symlink():
                if os.path.realpath(link) == os.path.realpath(target):
                    continue
                link.unlink()
            elif link.exists():
                if is_db:
                    # Two indexes cannot be merged: the primary's wins, this
                    # one stays beside it under another name.
                    _move_db(link, _keep_aside(target, home.name) if target.exists() else target)
                else:
                    for item in list(link.iterdir()):
                        dest = target / item.name
                        if dest.exists() or dest.is_symlink():
                            dest = _keep_aside(dest, home.name)
                        os.rename(item, dest)
                    link.rmdir()
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link)
            made += 1
    return made


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
    try:
        with open(base / "shared-chats.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            share_folders(base)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
