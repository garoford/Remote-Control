import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_control import agents


def done(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class AgentsTests(unittest.TestCase):
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
        self.repo = self.home / "proj"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "web").mkdir()

    def fake_run(self, cmd, timeout=5):
        if cmd[:3] == ["herdr", "agent", "list"]:
            listed = [
                {"pane_id": "w1:p1", "agent": "claude", "agent_status": "idle", "cwd": str(self.repo)},
                {"pane_id": "w1:p2", "agent": "pi", "name": "helper", "agent_status": "working", "cwd": str(self.repo / "web")},
            ]
            return done(json.dumps({"result": {"agents": listed}}))
        if cmd[:3] == ["herdr", "pane", "process-info"]:
            return done(json.dumps({"result": {"process_info": {"shell_pid": 100 if cmd[-1] == "w1:p1" else 200}}}))
        if cmd[:2] == ["tmux", "-L"] and "list-panes" in cmd:
            return done("hv-dx-rcabc\t300\t/x\nrcabcdef0123456789\t400\t/tmp\n")
        return done("")

    def listed(self, me=None):
        with mock.patch.object(agents, "_run", self.fake_run), \
             mock.patch.object(agents, "owner_of", lambda pid: "rosa" if pid == 201 else ""), \
             mock.patch.object(agents, "find_cli", lambda pid: {400: ("codex", 401), 100: ("claude", 101), 200: ("pi", 201)}.get(pid, ("", 0))), \
             mock.patch.object(agents, "label_of", lambda pid: pid or "main"):
            return agents.list_agents(me)

    def test_lists_herdr_and_tab_agents_with_owner(self) -> None:
        found = {a["id"]: a for a in self.listed({"profile": "", "pane": "w1:p1"})}
        self.assertEqual(set(found), {"w1:p1", "w1:p2", "rc:rcabcdef0123456789"})
        self.assertEqual(found["w1:p2"]["owner"], "rosa")
        self.assertEqual(found["w1:p2"]["root"], str(self.repo))
        self.assertTrue(found["w1:p1"]["you"])
        self.assertEqual(found["rc:rcabcdef0123456789"]["cli"], "codex")

    def test_here_shows_the_others_in_the_same_repo(self) -> None:
        others = agents.same_project(self.listed({"profile": "", "pane": "w1:p1"}), str(self.repo / "web"))
        self.assertEqual([a["id"] for a in others], ["w1:p2"])

    def test_caller_from_a_profile_home(self) -> None:
        env = {"HOME": str(self.home / ".local/share/remote-control/profiles/rosa"), "HERDR_PANE_ID": "w1:p2"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(agents.caller(), {"profile": "rosa", "pane": "w1:p2"})

    def test_prompt_to_another_users_agent_is_signed_and_noted(self) -> None:
        sent = []
        target = {"id": "w1:p2", "where": "herdr", "cli": "pi", "owner": "rosa", "owner_label": "Rosa"}

        def run(cmd, timeout=5):
            sent.append(cmd)
            return done("")

        with mock.patch.object(agents, "_run", run), mock.patch.object(agents, "label_of", lambda pid: pid or "main"):
            self.assertEqual(agents.prompt(target, "hola", {"profile": "", "pane": "w1:p1"}), "foreign")
        self.assertEqual(sent[0], ["herdr", "agent", "prompt", "w1:p2", "[Mensaje del agente de main] hola"])
        notes = agents.recent_notes()
        self.assertEqual((notes[0]["to"], notes[0]["from"], notes[0]["target"]), ("rosa", "", "w1:p2"))
        self.assertEqual(agents.recent_notes(now=notes[0]["t"] + agents.NOTES_FRESH + 1), [])

    def test_prompt_to_own_agent_is_plain(self) -> None:
        sent = []
        target = {"id": "w1:p1", "where": "herdr", "cli": "claude", "owner": "", "owner_label": "main"}
        with mock.patch.object(agents, "_run", lambda cmd, timeout=5: sent.append(cmd) or done("")):
            self.assertEqual(agents.prompt(target, "hola", {"profile": "", "pane": "w1:p2"}), "ok")
        self.assertEqual(sent, [["herdr", "agent", "prompt", "w1:p1", "hola"]])
        self.assertEqual(agents.recent_notes(), [])

    def test_rc_command_points_at_this_package(self) -> None:
        agents.write_rc_command(self.home)
        text = agents.rc_command(self.home).read_text()
        self.assertIn(str(Path(agents.__file__).resolve().parent.parent), text)
        self.assertTrue(os.access(agents.rc_command(self.home), os.X_OK))


if __name__ == "__main__":
    unittest.main()
