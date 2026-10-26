import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from remote_control import agents


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class ReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        for p in (
            mock.patch.dict(os.environ, {"HOME": str(self.home)}),
            mock.patch.object(agents, "primary_home", lambda: self.home),
        ):
            p.start()
            self.addCleanup(p.stop)

    def agent(self, cli: str) -> dict:
        return {"id": "rc:rc" + "a" * 12, "where": "tab", "cli": cli, "owner": "", "pid": 0}

    def test_pi_reply_skips_thinking_and_older_turns(self) -> None:
        write_jsonl(
            self.home / ".pi/agent/sessions/--proj--/s.jsonl",
            [
                {"type": "session", "id": "x"},
                {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "first question"}]}},
                {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]}},
                {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "second\n  question here"}]}},
                {"type": "message", "message": {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "secret reasoning"},
                    {"type": "text", "text": "the new answer"},
                ]}},
            ],
        )
        reply = agents.reply_since(self.agent("pi"), "second question here", time.time() - 60)
        self.assertEqual(reply, "the new answer")

    def test_claude_reply_joins_its_texts_and_ignores_tools(self) -> None:
        write_jsonl(
            self.home / ".claude/projects/-proj/abc.jsonl",
            [
                {"type": "user", "message": {"role": "user", "content": "[Mensaje del agente de x] do the task"}},
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "Checking."},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ]}},
                {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "a b"}]}},
                {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done: 2 files."}]}},
            ],
        )
        reply = agents.reply_since(self.agent("claude"), "[Mensaje del agente de x] do the task", time.time() - 60)
        self.assertEqual(reply, "Checking.\n\nDone: 2 files.")

    def test_codex_payload_format(self) -> None:
        write_jsonl(
            self.home / ".codex/sessions/2026/10/26/rollout-1.jsonl",
            [
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi codex"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}},
            ],
        )
        self.assertEqual(agents.reply_since(self.agent("codex"), "hi codex", time.time() - 60), "hello")

    def test_old_files_and_missing_message_give_none(self) -> None:
        path = self.home / ".pi/agent/sessions/--proj--/s.jsonl"
        write_jsonl(path, [{"type": "message", "message": {"role": "user", "content": "q"}}])
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertIsNone(agents.reply_since(self.agent("pi"), "q", time.time() - 60))
        self.assertIsNone(agents.reply_since(self.agent("pi"), "never sent", 0))
        self.assertIsNone(agents.reply_since(self.agent("gemini"), "q", 0))

    def test_last_reply_uses_what_rc_sent_and_keeps_the_end(self) -> None:
        agent = self.agent("pi")
        write_jsonl(
            self.home / ".pi/agent/sessions/--proj--/s.jsonl",
            [
                {"type": "message", "message": {"role": "user", "content": "the task"}},
                {"type": "message", "message": {"role": "assistant", "content": "x" * 20 + "SUMMARY"}},
            ],
        )
        self.assertIsNone(agents.last_reply(agent))
        agents.remember_sent(agent["id"], "the task", time.time() - 5)
        with mock.patch.object(agents, "REPLY_MAX", 10):
            reply = agents.last_reply(agent)
        self.assertTrue(reply.endswith("xxxSUMMARY"))
        self.assertIn("17 earlier characters left out", reply)


class TidyScreenTests(unittest.TestCase):
    def test_drops_padding_blank_runs_and_the_input_box(self) -> None:
        raw = "answer line one   \n\n\n\nline two  \n\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n  ⏵⏵ auto mode on\n\n\n"
        self.assertEqual(agents.tidy_screen(raw, 40), "answer line one\n\nline two\n")

    def test_markdown_dashes_are_kept(self) -> None:
        self.assertEqual(agents.tidy_screen("a\n----------\nb\n", 40), "a\n----------\nb\n")


if __name__ == "__main__":
    unittest.main()
