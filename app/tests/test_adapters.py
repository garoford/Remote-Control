import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from remote_control import adapters, agent_context, agents, profiles, workspaces

PROFILES_ZSH = Path(__file__).resolve().parents[1] / "remote_control" / "web" / "profiles.zsh"

GOOD = {
    "cli": "zed-agent",
    "is_ai_cli": True,
    "name": "Zed Agent",
    "bins": ["zed-agent", "zeda"],
    "home_dirs": [".zed-agent", ".config/zed-agent"],
    "context_file": ".zed-agent/AGENTS.md",
    "sessions": {
        "glob": ".zed-agent/sessions/*.jsonl",
        "format": "jsonl",
        "role": "msg.role",
        "text": "msg.content",
        "user": "human",
        "assistant": "bot",
    },
    "resume": {"flag": "--resume", "by": "id"},
}


class ValidateTests(unittest.TestCase):
    def test_good_adapter_is_kept(self) -> None:
        out = adapters.validate(dict(GOOD), "zed-agent")
        self.assertEqual(out["context_file"], ".zed-agent/AGENTS.md")
        self.assertEqual(out["bins"], ["zed-agent", "zeda"])
        self.assertEqual(out["sessions"]["user"], "human")
        self.assertEqual(out["resume"], {"flag": "--resume", "by": "id", "id_re": ""})

    def test_dangerous_paths_are_dropped(self) -> None:
        for bad in ("/etc/passwd.md", "~/.zed-agent/A.md", ".ssh/config.md", "../x/AGENTS.md",
                    ".zed-agent/../.ssh/a.md", ".config/AGENTS.md", ".local/AGENTS.md", ".bashrc"):
            out = adapters.validate({**GOOD, "context_file": bad}, "zed-agent")
            self.assertEqual(out["context_file"], "", bad)

    def test_context_must_be_markdown_in_its_folders(self) -> None:
        self.assertEqual(adapters.validate({**GOOD, "context_file": ".zed-agent/config.toml"}, "zed-agent")["context_file"], "")
        self.assertEqual(adapters.validate({**GOOD, "context_file": ".other/AGENTS.md"}, "zed-agent")["context_file"], "")
        ok = adapters.validate({**GOOD, "context_file": ".config/zed-agent/rules.md"}, "zed-agent")
        self.assertEqual(ok["context_file"], ".config/zed-agent/rules.md")

    def test_protected_home_dirs_are_dropped(self) -> None:
        out = adapters.validate({**GOOD, "home_dirs": [".ssh", ".config", ".aws", ".zed-agent/sub", ".zed-agent"]}, "zed-agent")
        self.assertEqual(out["home_dirs"], [".zed-agent"])

    def test_bad_names_flags_and_fields(self) -> None:
        self.assertIsNone(adapters.validate({**GOOD, "cli": "rm -rf"}, "rm -rf"))
        self.assertIsNone(adapters.validate({**GOOD, "cli": "other"}, "zed-agent"))
        self.assertIsNone(adapters.validate({**GOOD, "resume": {"flag": "--x; rm", "by": "id"}}, "zed-agent")["resume"])
        bad_sessions = {**GOOD["sessions"], "role": "a b"}
        out = adapters.validate({**GOOD, "sessions": bad_sessions}, "zed-agent")
        self.assertIsNone(out["sessions"])
        # Resuming by id needs the session files.
        self.assertIsNone(out["resume"])

    def test_not_an_ai_cli(self) -> None:
        out = adapters.validate({"cli": "htop", "is_ai_cli": False}, "htop")
        self.assertFalse(out["is_ai_cli"])

    def test_parse_answer(self) -> None:
        self.assertEqual(adapters.parse_answer('{"a": 1}'), {"a": 1})
        self.assertEqual(adapters.parse_answer('Here:\n```json\n{"a": 2}\n```\n'), {"a": 2})
        self.assertEqual(adapters.parse_answer('sure {"a": 3} done'), {"a": 3})
        self.assertIsNone(adapters.parse_answer("no idea"))


class LearnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        tool = self.bin / "zed-agent"
        tool.write_text("#!/bin/sh\necho 'Zed Agent: an AI coding agent. Chat with the model.'\n")
        tool.chmod(0o755)
        plain = self.bin / "plaintool"
        plain.write_text("#!/bin/sh\necho 'plaintool: copies files around'\n")
        plain.chmod(0o755)
        (self.home / ".zed-agent").mkdir()
        (self.home / ".claude").mkdir()
        patches = [
            mock.patch.dict(os.environ, {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin"}),
            mock.patch.object(adapters, "runners", lambda: [("fake", ["true"])]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        adapters._cache["key"] = None
        self.asked = []

        def fake_ask(cli, ev):
            self.asked.append((cli, ev))
            return dict(GOOD), "fake"

        p = mock.patch.object(adapters, "ask_ai", fake_ask)
        p.start()
        self.addCleanup(p.stop)

    def test_learn_applies_everywhere(self) -> None:
        self.assertTrue(profiles.create("Rosa Diaz")["ok"])
        rosa = profiles.profiles_dir() / "rosa-diaz"
        (rosa / ".zed-agent").mkdir()
        adapter, msg = adapters.learn("zed-agent")
        self.assertIsNotNone(adapter, msg)
        self.assertIn("Aprendí a usar Zed Agent", msg)
        # The evidence sent never holds file contents, only names.
        ev = self.asked[0][1]
        self.assertIn("AI coding agent", ev["help"])
        # Context in every HOME where it is in use, as that HOME's user.
        own = (self.home / ".zed-agent" / "AGENTS.md").read_text()
        theirs = (rosa / ".zed-agent" / "AGENTS.md").read_text()
        self.assertIn(agent_context.BEGIN, own)
        self.assertIn("primary user", own.split("**You are")[1].split("\n")[0])
        self.assertIn("rosa-diaz", theirs.split("**You are")[1].split("\n")[0])
        # The wrapper list, `rc` and the notes see it.
        self.assertEqual(adapters.bins_file().read_text(), "zed-agent\nzeda\n")
        self.assertIn("zed-agent", workspaces.profile_clis())
        self.assertEqual(agents._cli_name(["/x/bin/zed-agent", "--fast"]), "zed-agent")
        note = json.loads(agents.notes_file().read_text().splitlines()[-1])
        self.assertEqual((note["kind"], note["cli"]), ("adapter", "zed-agent"))

    def test_learned_once(self) -> None:
        adapters.learn("zed-agent")
        adapter, msg = adapters.learn("zed-agent")
        self.assertIn("ya está aprendido", msg)
        self.assertEqual(len(self.asked), 1)

    def test_not_ai_by_help_asks_nobody(self) -> None:
        adapter, msg = adapters.learn("plaintool")
        self.assertIsNone(adapter)
        self.assertEqual(self.asked, [])
        self.assertFalse(adapters.load_all()["plaintool"]["is_ai_cli"])
        self.assertNotIn("plaintool", adapters.known())

    def test_native_is_never_learned(self) -> None:
        adapter, msg = adapters.learn("claude")
        self.assertIsNone(adapter)
        self.assertIn("nativo", msg)

    def test_forget_takes_the_block_out(self) -> None:
        adapters.learn("zed-agent")
        ctx = self.home / ".zed-agent" / "AGENTS.md"
        self.assertTrue(ctx.exists())
        self.assertTrue(adapters.forget("zed-agent"))
        self.assertFalse(ctx.exists())
        self.assertNotIn("zed-agent", adapters.known())
        self.assertEqual(adapters.bins_file().read_text(), "")

    def test_invalid_answer_waits_before_trying_again(self) -> None:
        with mock.patch.object(adapters, "ask_ai", lambda cli, ev: ({"cli": "x"}, "fake")):
            adapter, msg = adapters.learn("zed-agent")
        self.assertIsNone(adapter)
        self.assertFalse(adapters.may_try("zed-agent"))
        self.assertTrue(adapters.may_try("zed-agent", now=time.time() + adapters.RETRY_AFTER_S + 1))

    def test_daily_budget(self) -> None:
        for i in range(adapters.DAILY_BUDGET):
            adapters._note_try(f"cli{i}", True)
        self.assertFalse(adapters.may_try("another"))

    def test_resume_and_replies(self) -> None:
        adapters.learn("zed-agent")
        sessions = self.home / ".zed-agent" / "sessions"
        sessions.mkdir()
        sid = "abc12345-6789"
        lines = [
            {"msg": {"role": "human", "content": "hola"}},
            {"msg": {"role": "bot", "content": [{"type": "text", "text": "buenas"}]}},
            {"msg": {"role": "tool", "content": "x"}},
        ]
        (sessions / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        adapter = adapters.get("zed-agent")
        self.assertEqual(adapters.turns(sessions / f"{sid}.jsonl", adapter), [("user", "hola"), ("assistant", "buenas")])
        self.assertEqual(workspaces.resume_command("zed-agent", sid), f"zed-agent --resume {sid}")
        self.assertEqual(workspaces.resume_command("zed-agent", "bad id;"), "zed-agent")
        self.assertEqual(workspaces.resume_command("htop", sid), "")
        # The session of a running process: the newest file since it started.
        self.assertEqual(adapters.session_id(adapter, os.getpid(), self.home), sid)
        agent = {"cli": "zed-agent", "owner": "", "pid": 0}
        with mock.patch.object(agents, "primary_home", lambda: self.home):
            self.assertEqual(agents._session_files(agent, 0), [sessions / f"{sid}.jsonl"])

    def test_wrapper_runs_learned_cli_with_profile(self) -> None:
        adapters.learn("zed-agent")
        self.assertTrue(profiles.create("Rosa Diaz")["ok"])
        script = f'source "{PROFILES_ZSH}"; whence -w zed-agent; RC_PROFILE=rosa-diaz _rc_cli printenv HOME'
        out = subprocess.run(["zsh", "-fc", script], capture_output=True, text=True, env=os.environ, timeout=10)
        self.assertIn("zed-agent: function", out.stdout)
        self.assertIn(str(profiles.profiles_dir() / "rosa-diaz"), out.stdout)


class CandidateTests(unittest.TestCase):
    def test_name_of(self) -> None:
        self.assertEqual(adapters._name_of(["/usr/bin/node", "/opt/x/bin/zed-agent.js", "--fast"]), "zed-agent")
        self.assertEqual(adapters._name_of(["-zsh"]), "zsh")
        self.assertEqual(adapters._name_of(["python3", "-m"]), "")
        self.assertEqual(adapters._name_of(["/home/u/.local/bin/foo", "x"]), "foo")

    def test_shape_keeps_roles_drops_text(self) -> None:
        shape = adapters._shape({"role": "user", "content": "my secret token sk-123", "n": 3, "parts": [{"type": "text", "text": "x"}]})
        self.assertEqual(shape, {"role": "user", "content": "<text>", "n": "<number>", "parts": [{"type": "text", "text": "<text>"}]})
        self.assertEqual(adapters._shape({"me@mail.com/x": 1, "ok": 2}), {"<key0>": "<number>", "ok": "<number>"})


if __name__ == "__main__":
    unittest.main()
