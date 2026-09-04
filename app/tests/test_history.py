import shutil
import subprocess
import time
import unittest

from remote_control.history import (
    cancel_copy_mode,
    find_suffix,
    history_payload,
    normalize_line,
    scroll_history,
)


class HistorySuffixTests(unittest.TestCase):
    def test_empty_fingerprint_is_full(self):
        lines = [f"line {i}" for i in range(10)]
        mode, out = find_suffix(lines, [])
        self.assertEqual(mode, "full")
        self.assertEqual(out, lines)

    def test_suffix_after_fingerprint(self):
        lines = ["a", "b", "c", "d", "e", "f"]
        mode, out = find_suffix(lines, ["c", "d"])
        self.assertEqual(mode, "suffix")
        self.assertEqual(out, ["e", "f"])

    def test_fingerprint_uses_last_match(self):
        lines = ["x", "y", "x", "y", "z"]
        mode, out = find_suffix(lines, ["x", "y"])
        self.assertEqual(mode, "suffix")
        self.assertEqual(out, ["z"])

    def test_missing_fingerprint_is_full(self):
        lines = ["only", "these"]
        mode, out = find_suffix(lines, ["nope"])
        self.assertEqual(mode, "full")
        self.assertEqual(out, lines)

    def test_trailing_space_does_not_break_match(self):
        lines = ["prompt %  ", "hello"]
        mode, out = find_suffix(lines, ["prompt %"])
        self.assertEqual(mode, "suffix")
        self.assertEqual(out, ["hello"])

    def test_normalize_strips_cr(self):
        self.assertEqual(normalize_line("hi \r\n"), "hi")

    def test_tmux_capture_reconciles_suffix(self):
        if not shutil.which("tmux"):
            self.skipTest("tmux missing")
        socket = "rc-testhist"
        tab = "rcabc1234567aa"
        subprocess.run(["tmux", "-L", socket, "kill-server"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started = subprocess.run(
            ["tmux", "-L", socket, "new-session", "-d", "-s", tab, "--", "bash", "--norc", "--noprofile"],
            check=False,
        )
        if started.returncode != 0:
            self.skipTest("could not start tmux")
        try:
            for text in ("echo rc-hist-one", "echo rc-hist-two", "echo rc-hist-three"):
                subprocess.run(
                    ["tmux", "-L", socket, "send-keys", "-t", tab, text, "Enter"],
                    check=True,
                )
            time.sleep(0.4)
            payload = history_payload(tab, ["rc-hist-one"], socket=socket)
            self.assertIsNotNone(payload)
            self.assertIn("all", payload)
            self.assertIn("rc-hist-one", "\n".join(payload["all"]))
            self.assertIn("rc-hist-three", "\n".join(payload["all"]))
            joined = "\n".join(payload["lines"])
            if payload["mode"] == "suffix":
                self.assertIn("rc-hist-two", joined)
                self.assertIn("rc-hist-three", joined)
                self.assertNotIn("rc-hist-one", joined)
            else:
                self.assertIn("rc-hist-three", joined)
        finally:
            subprocess.run(
                ["tmux", "-L", socket, "kill-server"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def test_cancel_unknown_tab_is_false(self) -> None:
        self.assertFalse(cancel_copy_mode("rcnotasession1"))

    def test_cancel_leaves_copy_mode(self) -> None:
        if not shutil.which("tmux"):
            self.skipTest("tmux missing")
        socket = "rc-testcopy"
        tab = "rcabc1234567bb"
        subprocess.run(
            ["tmux", "-L", socket, "kill-server"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = subprocess.run(
            [
                "tmux",
                "-L",
                socket,
                "new-session",
                "-d",
                "-s",
                tab,
                "--",
                "bash",
                "--norc",
                "--noprofile",
            ],
            check=False,
        )
        if started.returncode != 0:
            self.skipTest("could not start tmux")
        try:
            entered = subprocess.run(
                ["tmux", "-L", socket, "copy-mode", "-t", tab],
                check=False,
            )
            self.assertEqual(entered.returncode, 0)
            mode = subprocess.run(
                ["tmux", "-L", socket, "display-message", "-p", "-t", tab, "#{pane_in_mode}"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(mode.stdout.strip(), "1")
            self.assertTrue(cancel_copy_mode(tab, socket=socket))
            left = subprocess.run(
                ["tmux", "-L", socket, "display-message", "-p", "-t", tab, "#{pane_in_mode}"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(left.stdout.strip(), "0")
            self.assertFalse(cancel_copy_mode(tab, socket=socket))
        finally:
            subprocess.run(
                ["tmux", "-L", socket, "kill-server"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def test_scroll_unknown_tab_is_none(self) -> None:
        self.assertIsNone(scroll_history("rcnotasession1", -8))

    def test_scroll_history_clamps_and_returns(self) -> None:
        if not shutil.which("tmux"):
            self.skipTest("tmux missing")
        socket = "rc-testscroll"
        tab = "rcabc1234567cc"
        subprocess.run(
            ["tmux", "-L", socket, "kill-server"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = subprocess.run(
            [
                "tmux",
                "-L",
                socket,
                "new-session",
                "-d",
                "-s",
                tab,
                "--",
                "bash",
                "--norc",
                "--noprofile",
            ],
            check=False,
        )
        if started.returncode != 0:
            self.skipTest("could not start tmux")
        try:
            for i in range(1, 60):
                subprocess.run(
                    ["tmux", "-L", socket, "send-keys", "-t", tab, f"echo line-{i}", "Enter"],
                    check=True,
                )
            time.sleep(0.4)
            up = scroll_history(tab, -12, socket=socket)
            self.assertIsNotNone(up)
            self.assertTrue(up["ok"])
            self.assertTrue(up["in_mode"])
            self.assertEqual(up["position"], 12)
            self.assertEqual(up["moved"], -12)
            top = scroll_history(tab, -80, socket=socket)
            self.assertIsNotNone(top)
            if top["position"] < top["history"]:
                top = scroll_history(tab, -80, socket=socket)
            self.assertIsNotNone(top)
            self.assertTrue(top["in_mode"])
            self.assertEqual(top["position"], top["history"])
            stuck = scroll_history(tab, -20, socket=socket)
            self.assertIsNotNone(stuck)
            self.assertEqual(stuck["position"], stuck["history"])
            self.assertEqual(stuck["moved"], 0)
            view = subprocess.run(
                ["tmux", "-L", socket, "capture-pane", "-p", "-t", tab],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertIn("line-", view.stdout)
            down = scroll_history(tab, 4, socket=socket)
            self.assertIsNotNone(down)
            self.assertTrue(down["in_mode"])
            self.assertEqual(down["position"], top["history"] - 4)
            live = scroll_history(tab, 80, socket=socket)
            self.assertIsNotNone(live)
            if live["in_mode"]:
                live = scroll_history(tab, 80, socket=socket)
            self.assertIsNotNone(live)
            self.assertFalse(live["in_mode"])
            self.assertEqual(live["position"], 0)
            again = scroll_history(tab, 5, socket=socket)
            self.assertIsNotNone(again)
            self.assertFalse(again["in_mode"])
            self.assertEqual(again["moved"], 0)
        finally:
            subprocess.run(
                ["tmux", "-L", socket, "kill-server"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    unittest.main()
