import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_control import herdr, profiles

WRAPPER = Path(__file__).resolve().parents[1] / "remote_control" / "web" / "profiles.zsh"


class SetPaneOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "profiles" / "cuenta2").mkdir(parents=True)
        self.panes = [{"pane_id": "w1:p2", "agent_status": "idle"}]
        for p in (
            mock.patch.object(profiles, "base_dir", lambda: self.base),
            mock.patch.object(herdr, "_api", self.fake_api),
        ):
            p.start()
            self.addCleanup(p.stop)

    def fake_api(self, *args):
        if args[:2] == ("pane", "list"):
            return {"panes": self.panes}
        if args[:2] == ("pane", "process-info"):
            return {"process_info": {"shell_pid": 4242}}
        return None

    def test_pins_the_pane_and_its_mark(self) -> None:
        with mock.patch.object(herdr, "_foreground_pgid", lambda pid: None):
            res = herdr.set_pane_owner("w1:p2", "cuenta2")
        self.assertEqual(res, {"ok": True, "restarted": False})
        self.assertEqual((self.base / "pane-profile" / "w1:p2").read_text(), "cuenta2\n")
        self.assertEqual(json.loads((self.base / "herdr-owners.json").read_text())["w1:p2"], "cuenta2")

    def test_primary_is_an_empty_pin(self) -> None:
        with mock.patch.object(herdr, "_foreground_pgid", lambda pid: None):
            self.assertTrue(herdr.set_pane_owner("w1:p2", "")["ok"])
        self.assertEqual((self.base / "pane-profile" / "w1:p2").read_text(), "\n")

    def test_rejects_bad_input(self) -> None:
        self.assertFalse(herdr.set_pane_owner("../x", "")["ok"])
        self.assertFalse(herdr.set_pane_owner("w1:p2", "nadie")["ok"])
        self.assertFalse(herdr.set_pane_owner("w1:p9", "")["ok"])
        self.assertFalse((self.base / "pane-profile").exists())

    def test_waiting_claude_comes_back_with_the_new_account(self) -> None:
        fg = iter([77, 77, None])
        with (
            mock.patch.object(herdr, "_foreground_pgid", lambda pid: next(fg)),
            mock.patch.object(herdr, "cli_profile", lambda pid: ""),
            mock.patch.object(herdr, "detect_cli", lambda pid, home=None: ("claude", "abc")),
            mock.patch.object(herdr, "resume_command", lambda cli, sid, home=None: f"claude --resume {sid}"),
            mock.patch.object(herdr, "_share_chats", lambda: None),
            mock.patch.object(herdr.os, "killpg") as kill,
            mock.patch.object(herdr.subprocess, "run") as run,
            mock.patch.object(herdr.time, "sleep", lambda s: None),
        ):
            res = herdr.set_pane_owner("w1:p2", "cuenta2")
        self.assertEqual(res, {"ok": True, "restarted": True})
        kill.assert_called_once_with(77, herdr.signal.SIGTERM)
        self.assertEqual(run.call_args[0][0][-1], " RC_PROFILE=cuenta2 claude --resume abc")

    def test_working_claude_is_left_alone(self) -> None:
        self.panes[0]["agent_status"] = "working"
        with (
            mock.patch.object(herdr, "_foreground_pgid", lambda pid: 77),
            mock.patch.object(herdr, "cli_profile", lambda pid: ""),
            mock.patch.object(herdr, "detect_cli", lambda pid, home=None: ("claude", "abc")),
            mock.patch.object(herdr.os, "killpg") as kill,
        ):
            res = herdr.set_pane_owner("w1:p2", "cuenta2")
        self.assertFalse(res["restarted"])
        self.assertIn("trabajando", res["note"])
        kill.assert_not_called()

    def test_dead_panes_lose_their_pin(self) -> None:
        pins = self.base / "pane-profile"
        pins.mkdir()
        (pins / "w1:p2").write_text("cuenta2\n")
        (pins / "w1:p3").write_text("cuenta2\n")
        herdr._drop_dead_pins({"w1:p2"})
        self.assertEqual(sorted(p.name for p in pins.iterdir()), ["w1:p2"])


@unittest.skipUnless(shutil.which("zsh"), "zsh not installed")
class WrapperPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.base = self.home / ".local" / "share" / "remote-control"
        (self.base / "profiles" / "cuenta2").mkdir(parents=True)
        (self.base / "profiles" / "otro").mkdir(parents=True)
        (self.base / "profile-active").write_text("otro\t\n")
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text('#!/bin/sh\necho "$RC_PROFILE|$HOME"\n')
        fake.chmod(0o755)
        self.path = f"{bin_dir}:{os.environ.get('PATH', '')}"

    def run_claude(self, pane: str) -> str:
        env = {"HOME": str(self.home), "PATH": self.path}
        if pane:
            env["HERDR_PANE_ID"] = pane
        out = subprocess.run(
            ["zsh", "-fc", f"source {shlex.quote(str(WRAPPER))}; claude"], env=env, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip()

    def test_pinned_pane_beats_the_active_profile(self) -> None:
        (self.base / "pane-profile").mkdir()
        (self.base / "pane-profile" / "w1:p2").write_text("cuenta2\n")
        self.assertEqual(self.run_claude("w1:p2"), f"cuenta2|{self.base}/profiles/cuenta2")

    def test_pinned_to_primary(self) -> None:
        (self.base / "pane-profile").mkdir()
        (self.base / "pane-profile" / "w1:p2").write_text("\n")
        self.assertEqual(self.run_claude("w1:p2"), f"|{self.home}")

    def test_unpinned_pane_follows_the_active_profile(self) -> None:
        self.assertEqual(self.run_claude("w1:p5"), f"otro|{self.base}/profiles/otro")
        self.assertEqual(self.run_claude(""), f"otro|{self.base}/profiles/otro")


if __name__ == "__main__":
    unittest.main()


class FakeProc:
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.stdin = mock.MagicMock()
        self.dead = False

    def poll(self):
        return 0 if self.dead else None

    def wait(self, timeout=None):
        self.dead = True
        return 0

    def kill(self):
        self.dead = True


class HoldPaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.started = []

        def popen(cmd, **kw):
            proc = FakeProc(cmd)
            self.started.append(proc)
            return proc

        p = mock.patch.object(herdr.subprocess, "Popen", popen)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(herdr._holds.clear)

    def test_owner_size_from_frames(self) -> None:
        # Framed: minus the frame and herdr's scrollbar column.
        self.assertEqual(herdr._owner_size({"w": 62, "h": 43}, False), (59, 41))
        self.assertEqual(herdr._owner_size({"w": 94, "h": 39}, True), (93, 39))

    def test_holds_then_resizes_in_place(self) -> None:
        self.assertTrue(herdr.hold_pane("w1:p1", 80, 30, "dabcdefgh1", 100.0))
        self.assertIn("--takeover", self.started[0].cmd)
        self.assertEqual(self.started[0].cmd[-4:], ["--cols", "80", "--rows", "30"])
        self.assertFalse(herdr.hold_pane("w1:p1", 80, 30, "dabcdefgh1", 101.0))
        self.assertTrue(herdr.hold_pane("w1:p1", 90, 32, "dabcdefgh1", 102.0))
        self.assertEqual(len(self.started), 1)
        sent = self.started[0].stdin.write.call_args[0][0]
        self.assertEqual(json.loads(sent), {"type": "terminal.resize", "cols": 90, "rows": 32})

    def test_other_device_needs_newer_typing(self) -> None:
        herdr.hold_pane("w1:p1", 80, 30, "dabcdefgh1", 100.0)
        self.assertFalse(herdr.hold_pane("w1:p1", 40, 20, "dzzzzzzzz9", 50.0))
        self.assertTrue(herdr.hold_pane("w1:p1", 40, 20, "dzzzzzzzz9", 200.0))
        self.assertEqual(herdr._holds["w1:p1"]["dev"], "dzzzzzzzz9")

    def test_only_the_owners_panes_are_held(self) -> None:
        boxes = [{"x": 0, "y": 0, "w": 62, "h": 43, "lines": []}, {"x": 62, "y": 0, "w": 62, "h": 22, "lines": []}]
        viewer = {"prof": "pcuenta2", "dev": "dabcdefgh1", "single": False, "idle_ms": 0}
        herdr._hold_owned(boxes, ["w1:p1", "w1:p2"], {"w1:p1": "cuenta2", "w1:p2": ""}, viewer)
        self.assertEqual(list(herdr._holds), ["w1:p1"])
        herdr._hold_owned(boxes, ["w1:p1", "w1:p2"], {"w1:p1": "cuenta2", "w1:p2": ""}, {"prof": "../x", "dev": "d1"})
        self.assertEqual(list(herdr._holds), ["w1:p1"])

    def test_dead_and_closed_panes_are_released(self) -> None:
        herdr.hold_pane("w1:p1", 80, 30, "dabcdefgh1", 1.0)
        herdr.hold_pane("w1:p2", 80, 30, "dabcdefgh1", 1.0)
        herdr._drop_dead_holds({"w1:p2"})
        self.assertEqual(list(herdr._holds), ["w1:p2"])
        self.started[0].stdin.close.assert_called_once()
