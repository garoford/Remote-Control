import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_control import agent_context, profiles, workspaces

PROFILES_ZSH = Path(__file__).resolve().parents[1] / "remote_control" / "web" / "profiles.zsh"


class AgentContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        patches = [
            mock.patch.dict(os.environ, {"HOME": str(self.home)}),
            # Only claude and cursor are "installed".
            mock.patch.object(agent_context.shutil, "which", lambda b: f"/bin/{b}" if b in ("claude", "agent") else None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.assertTrue(profiles.create("Rosa Diaz")["ok"])
        self.rosa = profiles.profiles_dir() / "rosa-diaz"

    def test_sync_writes_every_home_and_keeps_user_text(self) -> None:
        own = self.home / ".claude" / "CLAUDE.md"
        own.parent.mkdir(parents=True)
        own.write_text("# My rules\n", encoding="utf-8")
        (self.rosa / ".codex").mkdir()

        agent_context.sync()

        text = own.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(agent_context.BEGIN))
        self.assertTrue(text.endswith("# My rules\n"))
        self.assertIn("`Rosa Diaz` (profile `rosa-diaz`)", text)
        self.assertIn(f"HOME `{self.rosa}`", text)
        self.assertTrue((self.rosa / ".claude" / "CLAUDE.md").is_file())
        # Codex is not installed but Rosa already uses it.
        self.assertTrue((self.rosa / ".codex" / "AGENTS.md").is_file())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        # Cursor only reads AGENTS.md up the folder tree: the primary HOME's.
        self.assertTrue((self.home / "AGENTS.md").is_file())
        self.assertFalse((self.rosa / "AGENTS.md").exists())

    def test_each_home_says_who_it_is(self) -> None:
        agent_context.sync()
        mine = (self.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        rosa = (self.rosa / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        shared = (self.home / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(f"**You are `{profiles.primary_name()}` (primary user).**", mine)
        self.assertIn("**You are `Rosa Diaz` (profile `rosa-diaz`).**", rosa)
        self.assertIn(f"HOME `{self.rosa}`, launch with `RC_PROFILE=rosa-diaz`  ← you", rosa)
        self.assertNotIn("← you", shared)
        self.assertIn("cannot say", shared)

    def test_sync_twice_keeps_one_block_and_updates_it(self) -> None:
        agent_context.sync()
        profiles.create("Ana")
        agent_context.sync()
        text = (self.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(agent_context.BEGIN), 1)
        self.assertIn("`Ana` (profile `ana`)", text)

    def test_clear_restores_user_files_and_removes_ours(self) -> None:
        own = self.home / ".claude" / "CLAUDE.md"
        own.parent.mkdir(parents=True)
        own.write_text("# My rules\n", encoding="utf-8")
        agent_context.sync()
        agent_context.clear()
        self.assertEqual(own.read_text(encoding="utf-8"), "# My rules\n")
        self.assertFalse((self.home / "AGENTS.md").exists())
        self.assertFalse((self.rosa / ".claude" / "CLAUDE.md").exists())


@unittest.skipUnless(subprocess.run(["which", "zsh"], capture_output=True).returncode == 0, "zsh missing")
class WrapperProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        base = self.home / ".local" / "share" / "remote-control"
        (base / "profiles" / "rosa").mkdir(parents=True)
        # Rosa's device typed last.
        (base / "profile-active").write_text(f"rosa\t{base / 'profiles' / 'rosa'}\n", encoding="utf-8")

    def home_seen(self, env_profile: str | None) -> str:
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"]}
        if env_profile is not None:
            env["RC_PROFILE"] = env_profile
        script = f'source "{PROFILES_ZSH}"; _rc_cli printenv HOME RC_PROFILE'
        out = subprocess.run(["zsh", "-f", "-c", script], env=env, capture_output=True, text=True, check=True)
        return out.stdout

    def test_without_rc_profile_follows_the_device_that_typed_last(self) -> None:
        self.assertEqual(self.home_seen(None).split("\n")[:2], [str(self.home / ".local/share/remote-control/profiles/rosa"), "rosa"])

    def test_empty_rc_profile_forces_the_primary(self) -> None:
        self.assertEqual(self.home_seen("").split("\n")[:2], [str(self.home), ""])


class RerunProfileTests(unittest.TestCase):
    def test_primary_cli_is_pinned_to_the_primary(self) -> None:
        self.assertEqual(workspaces._rerun({"cli": "agent", "resume": "abc12345-def"}), "RC_PROFILE= agent --resume abc12345-def")
        with mock.patch.object(workspaces.shutil, "which", lambda n: f"/usr/bin/{n}"):
            self.assertEqual(workspaces._rerun({"argv": ["/usr/bin/codex"]}), "RC_PROFILE= codex")

    def test_other_commands_stay_as_they_were(self) -> None:
        with mock.patch.object(workspaces.shutil, "which", lambda n: f"/usr/bin/{n}"):
            self.assertEqual(workspaces._rerun({"argv": ["/usr/bin/htop"]}), "htop")

    def test_profile_cli_keeps_its_profile(self) -> None:
        with mock.patch.object(workspaces, "_profile_home", lambda p: Path("/tmp") if p == "rosa" else None):
            self.assertEqual(
                workspaces._rerun({"cli": "agent", "resume": "abc12345-def", "profile": "rosa"}),
                "RC_PROFILE=rosa agent --resume abc12345-def",
            )
