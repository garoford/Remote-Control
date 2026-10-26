import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from remote_control import agents, workspaces

A, B, ME = "rc" + "a" * 12, "rc" + "b" * 12, "rc" + "c" * 12


class OpenTabBesideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        state = {
            "workspaces": [
                {"id": "w00000001", "name": "Mine", "tabs": [ME, A], "lastTab": A, "created": 1, "group": "gpc"},
                {"id": "w00000002", "name": "Other", "tabs": [B], "lastTab": B, "created": 2, "group": "gpc"},
                {"id": "w00000003", "name": "Empty", "tabs": [], "lastTab": "", "created": 3, "group": "gpc"},
            ],
            "active": "w00000003",
        }
        (self.base / "workspaces.json").write_text(json.dumps(state), encoding="utf-8")
        self.live = [ME, A, B]

    def start(self, tab, folder, socket=None, env=None):
        self.started = (tab, folder, env)
        self.live.append(tab)
        return True

    def sessions(self, socket=None):
        return [{"id": t} for t in self.live]

    def test_new_tab_goes_right_after_the_caller_not_in_the_active_workspace(self) -> None:
        with mock.patch.object(workspaces, "start_tab", self.start), mock.patch.object(
            workspaces, "list_sessions", self.sessions
        ):
            tab = workspaces.open_tab_beside(ME, Path("/tmp"), base=self.base, env={"RC_PROFILE": ""})
        state = workspaces.load_state(self.base)
        self.assertEqual(state["workspaces"][0]["tabs"], [ME, tab, A])
        self.assertEqual(state["workspaces"][1]["tabs"], [B])
        # Nobody's screen moves: the active workspace and its last tab stay.
        self.assertEqual(state["active"], "w00000003")
        self.assertEqual(state["workspaces"][2]["tabs"], [])
        self.assertEqual(state["workspaces"][2]["lastTab"], "")
        self.assertEqual(state["workspaces"][0]["lastTab"], A)
        self.assertEqual(self.started[2], {"RC_PROFILE": ""})

    def test_tmux_failure_leaves_the_state_alone(self) -> None:
        before = (self.base / "workspaces.json").read_text(encoding="utf-8")
        with mock.patch.object(workspaces, "start_tab", return_value=False):
            self.assertEqual(workspaces.open_tab_beside(ME, Path("/tmp"), base=self.base), "")
        self.assertEqual((self.base / "workspaces.json").read_text(encoding="utf-8"), before)


class SpawnCommandTests(unittest.TestCase):
    def run_rc(self, argv, env):
        err = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stderr(err), mock.patch.object(agents, "spawn") as spawn:
            code = agents.main(argv)
        return code, err.getvalue(), spawn

    def test_refused_in_herdr(self) -> None:
        code, err, spawn = self.run_rc(["spawn", "claude"], {"HERDR_PANE_ID": "w1:p1", "RC_PROFILE": ""})
        self.assertEqual(code, 1)
        self.assertIn("herdr pane split", err)
        spawn.assert_not_called()

    def test_refused_outside_a_tab(self) -> None:
        env = {"RC_PROFILE": ""}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERDR_PANE_ID", None)
            os.environ.pop("TMUX_PANE", None)
            code, err, spawn = self.run_rc(["spawn", "claude"], env)
        self.assertEqual(code, 1)
        self.assertIn("Remote Control tab", err)
        spawn.assert_not_called()

    def test_unknown_cli_is_rejected(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            agents.main(["spawn", "bash"])


class CloseTests(unittest.TestCase):
    me = {"profile": "", "pane": f"rc:{ME}"}

    def close(self, spawned_by: str, agent_id: str = f"rc:{A}"):
        with mock.patch.object(agents, "_tmux", return_value=spawned_by + "\n"), mock.patch.object(
            agents, "close_session", return_value=True
        ) as kill, mock.patch.object(agents, "change_workspaces") as forget:
            why = agents.close_spawned(agent_id, self.me)
        return why, kill, forget

    def test_closes_own_spawned_tab(self) -> None:
        why, kill, forget = self.close("-")
        self.assertEqual(why, "")
        kill.assert_called_once_with(A)
        forget.assert_called_once_with({"op": "forget", "tab": A})

    def test_never_closes_a_tab_it_did_not_open(self) -> None:
        for by, expected in (("", "not opened with"), ("cuenta2", "another user")):
            why, kill, _ = self.close(by)
            self.assertIn(expected, why)
            kill.assert_not_called()

    def test_never_closes_itself_or_herdr_panes(self) -> None:
        for target in (f"rc:{ME}", "w1:p1"):
            why, kill, _ = self.close("-", target)
            self.assertTrue(why)
            kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
