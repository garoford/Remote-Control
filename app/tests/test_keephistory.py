import os
import pty
import signal
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "remote_control" / "web" / "rc_keephistory.py"


class SignalTests(unittest.TestCase):
    def test_a_term_closes_the_program_and_gives_the_terminal_back(self) -> None:
        # Remote Control closes Cursor Agent this way to reopen it with another
        # account: the shell's terminal must not stay raw.
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        child_pid_file = Path(tempfile.mkdtemp()) / "program.pid"
        prog = f"import os,time; open({str(child_pid_file.resolve())!r},'w').write(str(os.getpid())); time.sleep(60)"
        wrapper = os.fork()
        if wrapper == 0:
            os.setsid()
            for fd in (0, 1, 2):
                os.dup2(slave, fd)
            os.execv(sys.executable, [sys.executable, str(SCRIPT), sys.executable, "-c", prog])
        os.close(slave)
        self.addCleanup(child_pid_file.unlink, missing_ok=True)
        deadline = time.monotonic() + 10
        while not child_pid_file.exists() or not child_pid_file.read_text():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        program = int(child_pid_file.read_text())
        self.assertFalse(termios.tcgetattr(master)[3] & termios.ICANON)  # raw while it runs
        os.kill(wrapper, signal.SIGTERM)
        _, status = os.waitpid(wrapper, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 128 + signal.SIGTERM)
        self.assertTrue(termios.tcgetattr(master)[3] & termios.ICANON)  # restored
        with self.assertRaises(ProcessLookupError):
            for _ in range(50):
                os.kill(program, 0)
                time.sleep(0.05)

    def test_a_terminal_left_raw_is_neither_passed_on_nor_given_back(self) -> None:
        # Raw output (no OPOST) makes every newline stay in its column: the
        # program drew its prompt again on a new row at each key.
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        raw = termios.tcgetattr(slave)
        raw[1] &= ~termios.OPOST
        raw[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(slave, termios.TCSANOW, raw)
        seen = Path(tempfile.mkdtemp()) / "oflag"
        prog = f"import termios; open({str(seen)!r},'w').write(str(termios.tcgetattr(0)[1]))"
        wrapper = os.fork()
        if wrapper == 0:
            os.setsid()
            for fd in (0, 1, 2):
                os.dup2(slave, fd)
            os.execv(sys.executable, [sys.executable, str(SCRIPT), sys.executable, "-c", prog])
        os.close(slave)
        deadline = time.monotonic() + 10
        while os.waitpid(wrapper, os.WNOHANG)[0] == 0:
            self.assertLess(time.monotonic(), deadline)
            try:
                os.read(master, 4096)
            except OSError:
                pass
            time.sleep(0.02)
        self.assertTrue(int(seen.read_text()) & termios.OPOST)  # the program got a sane one
        after = termios.tcgetattr(master)
        self.assertTrue(after[1] & termios.OPOST and after[3] & termios.ICANON)  # and so did the shell


if __name__ == "__main__":
    unittest.main()
