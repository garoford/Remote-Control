import subprocess
import unittest
from pathlib import Path
from unittest import mock

from remote_control import herdr, workspaces


class HerdrCopyOneSpaceTests(unittest.TestCase):
    """Auto copies the herdr space you pick into Phone; the rest of Phone stays."""

    PLAN = {
        "workspaces": [
            {
                "id": "w5",
                "name": "temporaltatoo",
                "panes": [
                    {"label": "claude", "cwd": "/tmp", "command": "RC_PROFILE=katherine claude --resume abc", "owner": "katherine"},
                    {"label": "zsh", "cwd": "/tmp", "command": "", "owner": ""},
                ],
            }
        ]
    }

    def setUp(self) -> None:
        self.tmux_calls = []
        self.closed = []
        self.asked = []

        def plan(workspace=""):
            self.asked.append(workspace)
            return self.PLAN if workspace == "w5" else {"error": "Ese space de herdr ya no existe"}

        for p in (
            mock.patch.object(herdr, "herdr_plan", plan),
            mock.patch.object(herdr, "tab_launch_context", lambda: (Path("/nonexistent.conf"), "/bin/zsh", {})),
            mock.patch.object(workspaces, "tab_env_args", lambda conf, shell: []),
            mock.patch.object(workspaces.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0)),
            mock.patch.object(workspaces, "_tmux", lambda *a, **k: self.tmux_calls.append(a)),
            mock.patch.object(workspaces, "close_session", lambda tab, socket=None: self.closed.append(tab)),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.state = {
            "active": "wsother",
            "tabs": {"rcold1": {}, "rcother": {}},
            "workspaces": [
                {"id": "wsold", "name": "temporaltatoo", "tabs": ["rcold1"], "lastTab": "", "group": "gphone"},
                {"id": "wsother", "name": "FOFA", "tabs": ["rcother"], "lastTab": "", "group": "gphone"},
            ],
        }

    def test_copies_only_that_space_and_replaces_its_old_copy(self) -> None:
        res = workspaces._herdr_apply(self.state, "cf-remote", "w5")
        self.assertTrue(res["ok"])
        self.assertEqual(self.asked, ["w5"])
        names = [(w["name"], w["group"]) for w in self.state["workspaces"]]
        self.assertEqual(names, [("FOFA", "gphone"), ("temporaltatoo", "gphone")])
        self.assertEqual(self.closed, ["rcold1"])
        new = self.state["workspaces"][-1]
        self.assertEqual(len(new["tabs"]), 2)
        self.assertEqual(res["id"], new["id"])
        # Each tab belongs to its pane's owner, and the chat is resumed.
        owners = [c[-1] for c in self.tmux_calls if "@rc_prof" in c]
        self.assertEqual(owners, ["pkatherine", "p"])
        self.assertIn(("cf-remote", "send-keys", "-t", f"={new['tabs'][0]}:", "-l", "RC_PROFILE=katherine claude --resume abc"), self.tmux_calls)

    def test_needs_a_space(self) -> None:
        self.assertFalse(workspaces._herdr_apply(self.state, "cf-remote", "")["ok"])
        self.assertFalse(workspaces._herdr_apply(self.state, "cf-remote", "w9")["ok"])
        self.assertEqual(len(self.state["workspaces"]), 2)


if __name__ == "__main__":
    unittest.main()
