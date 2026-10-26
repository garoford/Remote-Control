import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
