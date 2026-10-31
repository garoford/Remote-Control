import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
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
        self.assertEqual(res, {"ok": True, "restarted": True, "cli": "Claude"})
        kill.assert_called_once_with(77, herdr.signal.SIGTERM)
        self.assertEqual(run.call_args[0][0][-1], " RC_PROFILE=cuenta2 claude --resume abc")

    def test_cursor_agy_and_pi_come_back_in_their_chat(self) -> None:
        for cli, sid, line in (
            ("agent", "c0ffee12-aaaa", "agent --resume c0ffee12-aaaa"),
            ("agy", "2337caa1-e924", "agy --conversation 2337caa1-e924"),
            ("pi", "01a17df3-dce2", "pi --session 01a17df3-dce2"),
        ):
            fg = iter([77, 77, None])
            with (
                mock.patch.object(herdr, "_foreground_pgid", lambda pid: next(fg)),
                mock.patch.object(herdr, "cli_profile", lambda pid: ""),
                mock.patch.object(herdr, "detect_cli", lambda pid, home=None, c=cli, s=sid: (c, s)),
                mock.patch.object(herdr, "_share_chats", lambda: None),
                mock.patch.object(herdr.os, "killpg"),
                mock.patch.object(herdr.subprocess, "run") as run,
                mock.patch.object(herdr.time, "sleep", lambda s: None),
            ):
                res = herdr.set_pane_owner("w1:p2", "cuenta2")
            self.assertEqual(res, {"ok": True, "restarted": True, "cli": herdr.CLI_NAMES[cli]}, cli)
            self.assertEqual(run.call_args[0][0][-1], f" RC_PROFILE=cuenta2 {line}")

    def test_a_cli_with_no_chat_yet_just_reopens(self) -> None:
        fg = iter([77, 77, None])
        with (
            mock.patch.object(herdr, "_foreground_pgid", lambda pid: next(fg)),
            mock.patch.object(herdr, "cli_profile", lambda pid: ""),
            mock.patch.object(herdr, "detect_cli", lambda pid, home=None: ("pi", "")),
            mock.patch.object(herdr, "_share_chats", lambda: None),
            mock.patch.object(herdr.os, "killpg"),
            mock.patch.object(herdr.subprocess, "run") as run,
            mock.patch.object(herdr.time, "sleep", lambda s: None),
        ):
            herdr.set_pane_owner("w1:p2", "cuenta2")
        self.assertEqual(run.call_args[0][0][-1], " RC_PROFILE=cuenta2 pi")

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

    def run_claude(self, pane: str, tmux: str = "") -> str:
        env = {"HOME": str(self.home), "PATH": self.path}
        if pane:
            env["HERDR_PANE_ID"] = pane
        if tmux:
            env["TMUX"] = tmux
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

    def fake_tmux(self, owner: str) -> None:
        fake = self.home / "bin" / "tmux"
        fake.write_text(f'#!/bin/sh\n[ "$1 $2 $3" = "show-options -qv @rc_prof" ] && echo {owner}\n')
        fake.chmod(0o755)

    def test_tab_runs_as_its_owner(self) -> None:
        self.fake_tmux("pcuenta2")
        self.assertEqual(self.run_claude("", "/tmp/tmux-1000/cf-remote,123,0"), f"cuenta2|{self.base}/profiles/cuenta2")
        self.fake_tmux("p")
        self.assertEqual(self.run_claude("", "/tmp/tmux-1000/cf-remote,123,0"), f"|{self.home}")

    def test_device_that_typed_last_does_not_pick_the_account(self) -> None:
        # The active profile is "otro"; a tab owned by the primary stays primary.
        self.fake_tmux("p")
        self.assertEqual(self.run_claude("", "/tmp/tmux-1000/cf-remote,123,0"), f"|{self.home}")
        # A terminal outside Remote Control is the primary user's.
        self.assertEqual(self.run_claude(""), f"|{self.home}")
        self.assertEqual(self.run_claude("", "/tmp/tmux-1000/default,123,0"), f"|{self.home}")

    def test_new_pane_belongs_to_whoever_opened_it_for_good(self) -> None:
        self.assertEqual(self.run_claude("w1:p5"), f"otro|{self.base}/profiles/otro")
        self.assertEqual((self.base / "pane-profile" / "w1:p5").read_text(), "otro\n")
        # Someone else typing later does not move it.
        (self.base / "profile-active").write_text("cuenta2\t\n")
        self.assertEqual(self.run_claude("w1:p5"), f"otro|{self.base}/profiles/otro")



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


class OuterBoxesTests(unittest.TestCase):
    def test_a_table_inside_a_pane_is_not_a_pane(self) -> None:
        left = {"x": 9, "y": 1, "w": 21, "h": 9, "lines": []}
        table = {"x": 11, "y": 3, "w": 14, "h": 5, "lines": []}
        right = {"x": 30, "y": 1, "w": 22, "h": 5, "lines": []}
        self.assertEqual(herdr._outer_boxes([left, table, right]), [left, right])


class SetTabOwnerTests(unittest.TestCase):
    TAB = "rcabcdefghij"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "profiles" / "cuenta2").mkdir(parents=True)
        self.calls = []
        self.command = "zsh"
        self.activity = 0
        self.screen = "❯ \n"
        self.clients = {"/dev/pts/6": "pcuenta2", "/dev/pts/7": "p", "/dev/pts/8": ""}
        from remote_control import history

        for p in (
            mock.patch.object(profiles, "base_dir", lambda: self.base),
            mock.patch.object(history, "_tmux", self.fake_tmux),
        ):
            p.start()
            self.addCleanup(p.stop)

    def fake_tmux(self, socket, *args, text=False):
        self.calls.append(args)
        out = ""
        if args[0] == "display-message":
            out = f"4242\t{self.command}\t{self.activity}\n"
        elif args[0] == "capture-pane":
            out = self.screen
        elif args[0] == "list-sessions":
            out = f"{self.TAB}\n"
        elif args[0] == "list-clients":
            out = "/dev/pts/6\t120\t40\n/dev/pts/7\t200\t50\n/dev/pts/8\t90\t30\n"
        elif args[:2] == ("show-options", "-sv"):
            name = "/dev/pts/" + args[2].rsplit("_", 1)[1]
            out = self.clients.get(name, "")
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    def test_new_owner_sizes_the_tab_and_others_watch(self) -> None:
        with mock.patch.object(herdr, "_foreground_pgid", lambda pid: None):
            res = herdr.set_tab_owner(self.TAB, "cuenta2")
        self.assertEqual(res, {"ok": True, "restarted": False})
        self.assertIn(("set-option", "-t", f"={self.TAB}:", "@rc_prof", "pcuenta2"), self.calls)
        self.assertIn(("refresh-client", "-t", "/dev/pts/6", "-f", "!ignore-size"), self.calls)
        self.assertIn(("refresh-client", "-t", "/dev/pts/7", "-f", "ignore-size"), self.calls)
        # A client of unknown profile is left as it was.
        self.assertFalse(any(c[:3] == ("refresh-client", "-t", "/dev/pts/8") for c in self.calls))
        self.assertIn(("resize-window", "-t", f"={self.TAB}:", "-x", "120", "-y", "40"), self.calls)

    def test_rejects_bad_input_and_herdr_tabs(self) -> None:
        self.assertFalse(herdr.set_tab_owner("../x", "")["ok"])
        self.assertFalse(herdr.set_tab_owner(self.TAB, "nadie")["ok"])
        self.command = "herdr"
        self.assertIn("panel", herdr.set_tab_owner(self.TAB, "")["error"])

    def test_busy_tab_keeps_its_cli(self) -> None:
        self.activity = int(time.time())
        self.screen = "✻ Pensando… (12s · esc to interrupt)\n"
        with (
            mock.patch.object(herdr, "_foreground_pgid", lambda pid: 77),
            mock.patch.object(herdr, "cli_profile", lambda pid: ""),
            mock.patch.object(herdr, "detect_cli", lambda pid, home=None: ("claude", "abc")),
            mock.patch.object(herdr.os, "killpg") as kill,
        ):
            res = herdr.set_tab_owner(self.TAB, "cuenta2")
        kill.assert_not_called()
        self.assertIn("trabajando", res["note"])

    def test_a_waiting_claude_that_just_redrew_moves(self) -> None:
        # Fresh output (it just opened, or the tab was resized) is no work.
        self.activity = int(time.time())
        self.screen = "Claude Code v2.1\n❯ \n  ? for shortcuts\n"
        fg = iter([77, None])
        with (
            mock.patch.object(herdr, "_foreground_pgid", lambda pid: next(fg, None)),
            mock.patch.object(herdr, "cli_profile", lambda pid: ""),
            mock.patch.object(herdr, "detect_cli", lambda pid, home=None: ("claude", "abc")),
            mock.patch.object(herdr, "_share_chats"),
            mock.patch.object(herdr, "resume_command", lambda cli, sid, home=None: "claude --resume abc"),
            mock.patch.object(herdr.os, "killpg") as kill,
        ):
            res = herdr.set_tab_owner(self.TAB, "cuenta2")
        kill.assert_called_once()
        self.assertEqual(res, {"ok": True, "restarted": True, "cli": "Claude"})
        self.assertIn(("send-keys", "-t", f"={self.TAB}:", "-l", " RC_PROFILE=cuenta2 claude --resume abc"), self.calls)

    def test_marks_note_every_known_panes_owner(self) -> None:
        herdr._write_missing_pins({"w1:p1": "cuenta2", "w1:p2": "", "rcabcdefghij": "x"})
        # A CLI already noted the owner of a brand-new pane: that wins.
        (self.base / "pane-profile" / "w1:p3").write_text("cuenta2\n")
        self.assertEqual(herdr._read_pin("w1:p3", ""), "cuenta2")
        self.assertEqual(herdr._read_pin("w1:p9", "dflt"), "dflt")
        pins = self.base / "pane-profile"
        self.assertEqual(sorted(p.name for p in pins.iterdir()), ["w1:p1", "w1:p2", "w1:p3"])
        self.assertEqual((pins / "w1:p1").read_text(), "cuenta2\n")


class PickLayoutTests(unittest.TestCase):
    """Several herdr tabs with nearly the same shape: which one the page shows."""

    BOXES = [
        {"x": 26, "y": 1, "w": 67, "h": 34, "lines": ["Ran 6 shell commands"]},
        {"x": 93, "y": 1, "w": 78, "h": 17, "lines": ["GNU nano 8.7.1 addons.txt"]},
        {"x": 93, "y": 18, "w": 78, "h": 17, "lines": ["9 files changed, 579 insertions(+), 90 deletions(-)"]},
    ]

    def layout(self, tab, left, top):
        panes = [
            (f"{tab}:a", (0, 0, left, 34)),
            (f"{tab}:b", (left, 0, 145 - left, top)),
            (f"{tab}:c", (left, top, 145 - left, 34 - top)),
        ]
        return {"tab_id": tab, "panes": [{"pane_id": pid, "rect": dict(zip(("x", "y", "width", "height"), r))} for pid, r in panes]}

    def candidates(self, *layouts):
        out = []
        for layout in layouts:
            found = herdr._shape_fit(self.BOXES, herdr._layout_panes(layout))
            if found:
                out.append((layout, found[0], found[1]))
        return out

    def setUp(self) -> None:
        herdr._last_tab.clear()

    def test_scrollbar_glyph_is_dropped_from_lines(self) -> None:
        boxes = herdr._clean_boxes([{"x": 0, "y": 0, "w": 9, "h": 9, "lines": ["9 files changed  \u2595", "\u2502abc def"]}])
        self.assertEqual(boxes[0]["lines"], ["9 files changed", "abc def"])

    def test_text_on_screen_decides(self) -> None:
        cands = self.candidates(self.layout("w8:t1", 67, 17), self.layout("w2:t1", 73, 17))
        self.assertEqual(len(cands), 2)
        texts = {"w2:t1:b": "GNU nano 8.7.1 addons.txt more"}
        with mock.patch.object(herdr, "_pane_text", lambda pid: texts.get(pid, "")):
            self.assertEqual(herdr._pick_layout(self.BOXES, cands, "dabcdef0123"), ["w2:t1:a", "w2:t1:b", "w2:t1:c"])

    def test_no_text_match_falls_back_to_closest_shape(self) -> None:
        cands = self.candidates(self.layout("w2:t1", 73, 17), self.layout("w8:t1", 67, 17))
        with mock.patch.object(herdr, "_pane_text", lambda pid: ""):
            self.assertEqual(herdr._pick_layout(self.BOXES, cands, "dabcdef0123")[0], "w8:t1:a")

    def test_same_shape_keeps_the_devices_last_tab(self) -> None:
        cands = self.candidates(self.layout("w8:t1", 67, 17), self.layout("w9:t1", 67, 17))
        with mock.patch.object(herdr, "_pane_text", lambda pid: ""):
            self.assertIsNone(herdr._pick_layout(self.BOXES, cands, "dabcdef0123"))
            herdr._last_tab["dabcdef0123"] = "w9:t1"
            self.assertEqual(herdr._pick_layout(self.BOXES, cands, "dabcdef0123")[0], "w9:t1:a")


if __name__ == "__main__":
    unittest.main()


class LockTests(unittest.TestCase):
    TAB = "rcabcdefghij"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "profiles" / "cuenta2").mkdir(parents=True)
        (self.base / "herdr-owners.json").write_text(json.dumps({"w1:p2": "cuenta2", "w1:p3": ""}))
        self.calls = []
        self.opts = {"@rc_prof": "pcuenta2"}
        self.clients = {"/dev/pts/6": ("pcuenta2", "attached"), "/dev/pts/7": ("p", "attached,ignore-size")}
        from remote_control import history

        for p in (
            mock.patch.object(profiles, "base_dir", lambda: self.base),
            mock.patch.object(history, "_tmux", self.fake_tmux),
        ):
            p.start()
            self.addCleanup(p.stop)

    def fake_tmux(self, socket, *args, text=False):
        self.calls.append(args)
        out = ""
        if args[0] == "set-option":
            if "-u" in args:
                self.opts.pop(args[-1], None)
            else:
                self.opts[args[-2]] = args[-1]
        elif args[0] == "show-options" and args[1] == "-sv":
            name = "/dev/pts/" + args[2].rsplit("_", 1)[1]
            out = self.clients.get(name, ("", ""))[0]
        elif args[0] == "show-options":
            out = self.opts.get(args[-1], "")
        elif args[0] == "list-clients" and "-t" in args:
            out = "\n".join(self.clients)
        elif args[0] == "list-clients":
            out = "\n".join(f"{n}\t{f}" for n, (_, f) in self.clients.items())
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    def test_only_the_owner_locks_a_pane(self) -> None:
        self.assertFalse(herdr.set_lock("pane", "w1:p2", True, "p")["ok"])
        self.assertTrue(herdr.set_lock("pane", "w1:p2", True, "pcuenta2")["ok"])
        self.assertEqual(herdr.lock_owner("w1:p2"), "cuenta2")
        self.assertIsNone(herdr.lock_owner("w1:p3"))
        self.assertTrue(herdr.set_lock("pane", "w1:p2", False, "pcuenta2")["ok"])
        self.assertIsNone(herdr.lock_owner("w1:p2"))

    def test_locked_pane_keeps_its_owner(self) -> None:
        herdr.set_lock("pane", "w1:p2", True, "pcuenta2")
        res = herdr.set_pane_owner("w1:p2", "", by="p")
        self.assertFalse(res["ok"])
        self.assertIn("bloqueado", res["error"])

    def test_locked_tab_turns_other_clients_read_only(self) -> None:
        self.assertFalse(herdr.set_lock("tab", self.TAB, True, "p")["ok"])
        self.assertTrue(herdr.set_lock("tab", self.TAB, True, "pcuenta2")["ok"])
        self.assertEqual(self.opts.get("@rc_lock"), "1")
        self.assertEqual(herdr.lock_owner("rc:" + self.TAB), "cuenta2")
        self.assertIn(("refresh-client", "-t", "/dev/pts/7", "-f", "read-only"), self.calls)
        self.assertFalse(any(c[:4] == ("refresh-client", "-t", "/dev/pts/6", "-f") and c[4] == "read-only" for c in self.calls))

    def test_unlock_clears_read_only_and_keeps_ignore_size(self) -> None:
        self.opts["@rc_lock"] = "1"
        self.clients["/dev/pts/7"] = ("p", "attached,ignore-size,read-only")
        self.assertTrue(herdr.set_lock("tab", self.TAB, False, "pcuenta2")["ok"])
        self.assertNotIn("@rc_lock", self.opts)
        self.assertIn(("switch-client", "-c", "/dev/pts/7", "-r"), self.calls)
        self.assertIn(("refresh-client", "-t", "/dev/pts/7", "-f", "ignore-size"), self.calls)

    def test_locks_of_closed_panes_go(self) -> None:
        herdr.set_lock("pane", "w1:p2", True, "pcuenta2")
        herdr._drop_dead_locks({"w1:p3"})
        self.assertIsNone(herdr.lock_owner("w1:p2"))


class SendInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.started = []
        herdr._holds.clear()
        self.addCleanup(herdr._holds.clear)

    def fake(self):
        import io

        proc = FakeProc([])
        proc.stdin = io.StringIO()
        return proc

    def popen(self, cmd, **kw):
        proc = self.fake()
        self.started.append((cmd, proc))
        return proc

    def written(self, proc) -> list:
        return [json.loads(line) for line in proc.stdin.getvalue().splitlines()]

    def test_goes_into_that_pane_whatever_herdr_has_focused(self) -> None:
        with mock.patch.object(herdr.subprocess, "Popen", self.popen):
            self.assertTrue(herdr.send_input("w1:p2", "hola\x1b[D"))
            self.assertTrue(herdr.send_input("w1:p2", "\r"))
        self.assertEqual(len(self.started), 1)
        cmd, proc = self.started[0]
        self.assertEqual(cmd[1:], ["terminal", "session", "control", "w1:p2", "--takeover"])
        self.assertEqual(self.written(proc), [{"type": "terminal.input", "text": "hola\x1b[D"}, {"type": "terminal.input", "text": "\r"}])

    def test_reuses_the_controller_that_holds_its_size(self) -> None:
        held = self.fake()
        herdr._holds["w1:p2"] = {"proc": held, "size": (80, 24), "dev": "dabcdefgh1", "typed_at": 1.0}
        with mock.patch.object(herdr.subprocess, "Popen", self.popen):
            self.assertTrue(herdr.send_input("w1:p2", "x"))
        self.assertEqual(self.started, [])
        self.assertEqual(self.written(held), [{"type": "terminal.input", "text": "x"}])

    def test_rejects_bad_input(self) -> None:
        with mock.patch.object(herdr.subprocess, "Popen", self.popen):
            self.assertFalse(herdr.send_input("../x", "x"))
            self.assertFalse(herdr.send_input("w1:p2", ""))
        self.assertEqual(self.started, [])


class DeviceViewTests(unittest.TestCase):
    """The tab on a device's screen, from herdr itself: no shape guessing."""

    TAB = "rcabcdefghij"
    DEV = "dabcdef0123"
    BOXES = PickLayoutTests.BOXES

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        # Two tabs with exactly the same shape, in two workspaces.
        one = PickLayoutTests.layout(None, "w3:t1", 73, 17)
        two = PickLayoutTests.layout(None, "w4:t1", 73, 17)
        one["focused_pane_id"] = "w3:t1:b"
        two["focused_pane_id"] = "w4:t1:a"
        self.snap = {
            "workspaces": [
                {"workspace_id": "w3", "label": "FOFA", "active_tab_id": "w3:t1"},
                {"workspace_id": "w4", "label": "Quoruz", "active_tab_id": "w4:t1"},
            ],
            "layouts": [one, two],
            "panes": [{"pane_id": p["pane_id"]} for lay in (one, two) for p in lay["panes"]],
        }
        self.sessions = f"hv-{self.DEV}-{self.TAB}\t\n{self.TAB}\tdzzzzzzzz9\n"
        self.title = "workspace: Quoruz"
        from remote_control import history

        for p in (
            mock.patch.object(profiles, "base_dir", lambda: self.base),
            mock.patch.object(history, "_tmux", self.fake_tmux),
            mock.patch.object(herdr, "_api", lambda *a: {"snapshot": self.snap}),
            mock.patch.object(herdr, "_hold_owned", lambda *a: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def fake_tmux(self, socket, *args, text=False):
        out = ""
        if args[0] == "list-sessions":
            out = self.sessions
        elif args[0] == "display-message":
            out = self.title + "\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    def marks(self, tab=TAB):
        return herdr.pane_marks(self.BOXES, {"prof": "p", "dev": self.DEV, "tab": tab})

    def test_same_shape_tabs_are_told_apart_by_the_clients_workspace(self) -> None:
        res = self.marks()
        self.assertTrue(res["exact"])
        self.assertEqual([m["pane"] for m in res["marks"]], ["w4:t1:a", "w4:t1:b", "w4:t1:c"])
        self.assertEqual([m["focused"] for m in res["marks"]], [True, False, False])
        self.title = "workspace: FOFA"
        self.assertEqual([m["pane"] for m in self.marks()["marks"]], ["w3:t1:a", "w3:t1:b", "w3:t1:c"])

    def test_the_device_that_opened_the_tab_uses_the_tab_itself(self) -> None:
        self.sessions = f"{self.TAB}\t{self.DEV}\n"
        self.assertEqual(herdr._device_client(self.DEV, self.TAB), self.TAB)
        self.assertTrue(self.marks()["exact"])

    def test_frames_that_do_not_fit_its_tab_get_no_marks(self) -> None:
        res = herdr.pane_marks(self.BOXES[:2], {"prof": "p", "dev": self.DEV, "tab": self.TAB})
        self.assertEqual(res["marks"], [])

    def test_two_workspaces_with_one_name_fall_back_to_shapes(self) -> None:
        self.snap["workspaces"][0]["label"] = "Quoruz"
        self.assertIsNone(herdr.device_view(self.DEV, self.TAB, self.snap))
