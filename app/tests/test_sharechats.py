import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
import time
from unittest import mock

from remote_control import workspaces

_SPEC = importlib.util.spec_from_file_location(
    "rc_sharechats", Path(__file__).resolve().parents[1] / "remote_control" / "web" / "rc_sharechats.py"
)
sharechats = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sharechats)


class ShareChatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.primary = Path(self.tmp.name) / "me"
        self.base = self.primary / ".local" / "share" / "remote-control"
        self.other = self.base / "profiles" / "cuenta2"
        self.other.mkdir(parents=True)

    def chat(self, home: Path, name: str, text: str = "{}\n") -> Path:
        path = home / ".claude" / "projects" / "-proj" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def theirs(self, home: Path, name: str) -> Path:
        return home / ".claude" / "projects" / "-proj" / name

    def test_links_both_ways_as_the_same_file(self) -> None:
        mine = self.chat(self.primary, "a.jsonl")
        self.chat(self.other, "b.jsonl")
        self.assertEqual(sharechats.sync(self.base), 2)
        self.assertTrue(os.path.samefile(mine, self.theirs(self.other, "a.jsonl")))
        self.assertTrue(self.theirs(self.primary, "b.jsonl").is_file())
        # Resuming in the other profile continues the same chat.
        with open(self.theirs(self.other, "a.jsonl"), "a", encoding="utf-8") as f:
            f.write("more\n")
        self.assertIn("more", mine.read_text(encoding="utf-8"))
        self.assertEqual(sharechats.sync(self.base), 0)

    def test_deleted_chat_does_not_come_back(self) -> None:
        mine = self.chat(self.primary, "a.jsonl")
        sharechats.sync(self.base)
        self.theirs(self.other, "a.jsonl").unlink()
        sharechats.sync(self.base)
        self.assertFalse(self.theirs(self.other, "a.jsonl").exists())
        mine.unlink()
        self.chat(self.other, "c.jsonl")
        sharechats.sync(self.base)
        self.assertFalse(mine.exists())

    def test_skips_symlinks_and_other_files(self) -> None:
        self.chat(self.primary, "notes.txt")
        target = self.chat(self.primary, "real.jsonl")
        (target.parent / "link.jsonl").symlink_to(target)
        sharechats.sync(self.base)
        self.assertFalse(self.theirs(self.other, "notes.txt").exists())
        self.assertFalse(self.theirs(self.other, "link.jsonl").exists())
        self.assertTrue(self.theirs(self.other, "real.jsonl").is_file())

    def test_new_profile_gets_existing_chats(self) -> None:
        self.chat(self.primary, "a.jsonl")
        sharechats.sync(self.base)
        late = self.base / "profiles" / "late"
        late.mkdir()
        sharechats.sync(self.base)
        self.assertTrue(self.theirs(late, "a.jsonl").is_file())

    def test_pi_sessions_are_shared_too(self) -> None:
        path = self.primary / ".pi" / "agent" / "sessions" / "--proj--" / "s.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
        sharechats.sync(self.base)
        self.assertTrue(os.path.samefile(path, self.other / ".pi" / "agent" / "sessions" / "--proj--" / "s.jsonl"))

    def test_old_state_without_cli_prefix_still_sticks(self) -> None:
        self.chat(self.primary, "a.jsonl")
        (self.base / "shared-chats.json").write_text(json.dumps({str(self.other): ["-proj/a.jsonl"]}), encoding="utf-8")
        sharechats.sync(self.base)
        self.assertFalse(self.theirs(self.other, "a.jsonl").exists())

    def test_only_primary_does_nothing(self) -> None:
        self.other.rmdir()
        self.chat(self.primary, "a.jsonl")
        self.assertEqual(sharechats.sync(self.base), 0)


class ShareFoldersTests(unittest.TestCase):
    """Cursor's and Antigravity's chats: one folder for every profile (temp HOMEs only)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.primary = Path(tmp.name) / "home"
        self.base = self.primary / ".local" / "share" / "remote-control"
        self.luis = self.base / "profiles" / "luis"
        self.luis.mkdir(parents=True)

    def test_profiles_open_the_primarys_chats(self) -> None:
        mine = self.primary / ".cursor" / "chats" / "h1" / "chat-a"
        mine.mkdir(parents=True)
        (mine / "store.db").write_text("a")
        sharechats.share_folders(self.base)
        link = self.luis / ".cursor" / "chats"
        self.assertTrue(link.is_symlink())
        self.assertEqual((link / "h1" / "chat-a" / "store.db").read_text(), "a")
        # A chat the profile starts lands in the shared folder too.
        (link / "h1" / "chat-b").mkdir()
        self.assertTrue((self.primary / ".cursor" / "chats" / "h1" / "chat-b").is_dir())
        self.assertEqual(sharechats.share_folders(self.base), 0)  # already shared

    def test_what_a_profile_had_is_moved_in_never_lost(self) -> None:
        theirs = self.luis / ".gemini" / "antigravity-cli" / "conversations"
        theirs.mkdir(parents=True)
        (theirs / "x.db").write_text("luis")
        ours = self.primary / ".gemini" / "antigravity-cli" / "conversations"
        ours.mkdir(parents=True)
        (ours / "x.db").write_text("primary")
        index = self.luis / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
        index.write_text("luis index")
        sharechats.share_folders(self.base)
        names = sorted(p.name for p in ours.iterdir())
        self.assertEqual(names, ["x.db", "x.luis.db"])
        self.assertEqual((ours / "x.luis.db").read_text(), "luis")
        self.assertTrue(index.is_symlink())
        self.assertEqual((self.primary / ".gemini" / "antigravity-cli" / "conversation_summaries.db").read_text(), "luis index")


class PiSessionTests(unittest.TestCase):
    def test_the_session_it_last_wrote_since_it_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            folder = home / ".pi" / "agent" / "sessions" / "--home-x-proyecto--"
            folder.mkdir(parents=True)
            old = folder / "2026-10-01T00-00-00-000Z_01aaaaaa-0000-7000-8000-000000000001.jsonl"
            new = folder / "2026-10-28T00-00-00-000Z_01bbbbbb-0000-7000-8000-000000000002.jsonl"
            old.write_text("{}")
            new.write_text("{}")
            now = time.time()
            os.utime(old, (now - 3600, now - 3600))
            os.utime(new, (now, now))
            with mock.patch.object(workspaces.os, "readlink", lambda p: "/home/x/proyecto"), \
                 mock.patch.object(workspaces, "_started_at", lambda pid: now - 60):
                self.assertEqual(workspaces._pi_session(1, home), "01bbbbbb-0000-7000-8000-000000000002")
            with mock.patch.object(workspaces.os, "readlink", lambda p: "/home/x/proyecto"), \
                 mock.patch.object(workspaces, "_started_at", lambda pid: now + 60):
                self.assertEqual(workspaces._pi_session(1, home), "")  # nothing written since it started

    def test_pi_is_recognised_by_its_script(self) -> None:
        self.assertTrue(workspaces._is_pi(["node", "/x/install/releases/1.0/node_modules/.bin/pi", "--session", "a"]))
        self.assertFalse(workspaces._is_pi(["python3", "script.py"]))


if __name__ == "__main__":
    unittest.main()
