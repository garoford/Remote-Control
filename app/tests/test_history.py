import shutil
import subprocess
import time
import unittest

from remote_control.history import find_suffix, history_payload, normalize_line


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


if __name__ == "__main__":
    unittest.main()
