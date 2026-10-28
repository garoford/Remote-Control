#!/usr/bin/env python3
"""Run a program in its own pty and drop "clear scrollback" (ESC [3J) from its output.

Ink-based CLIs (Cursor Agent, Claude Code) send ESC[2J ESC[3J whenever their
live area outgrows the screen. tmux obeys ESC[3J by wiping the pane history,
so the conversation can no longer be scrolled. Right after a resize the CLI
reprints the whole conversation, so there the wipe is kept: otherwise every
resize would leave another copy in the history. Everything else passes through.

Usage: rc_keephistory.py PROGRAM [ARGS...]
"""

import errno
import fcntl
import os
import pty
import select
import signal
import sys
import termios
import time
import tty

DROP = b"\x1b[3J"
# A resize makes the CLI clear and reprint everything within this window.
RESIZE_GRACE_S = 1.5


def write_all(fd: int, data: bytes) -> None:
    while data:
        try:
            n = os.write(fd, data)
        except InterruptedError:
            continue
        data = data[n:]


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        return 2
    if not os.isatty(0) or not os.isatty(1):
        os.execvp(argv[0], argv)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except OSError as exc:
            os.write(2, f"{argv[0]}: {exc.strerror}\r\n".encode())
            os._exit(127)

    resized = [0.0]

    def sync_size(*_):
        resized[0] = time.monotonic()
        try:
            size = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
        except OSError:
            pass

    # The child starts with our terminal settings, then takes over from there.
    try:
        termios.tcsetattr(fd, termios.TCSANOW, termios.tcgetattr(0))
    except termios.error:
        pass
    sync_size()
    signal.signal(signal.SIGWINCH, sync_size)

    # The program runs in its own session, so a signal for this job (Remote
    # Control closing it to reopen it with another account, a closed tab) is
    # passed on to it, and this keeps going until it ends and the terminal is
    # restored: dying here would leave the shell's terminal raw, and the next
    # program drawn in steps.
    def pass_on(signum, _frame):
        try:
            os.kill(pid, signum)
        except OSError:
            pass

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT):
        signal.signal(signum, pass_on)
    saved = termios.tcgetattr(0)
    tty.setraw(0)
    tail = b""
    try:
        while True:
            try:
                ready, _, _ = select.select([0, fd], [], [])
            except InterruptedError:
                continue
            if fd in ready:
                try:
                    data = os.read(fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                data = tail + data
                if time.monotonic() - resized[0] > RESIZE_GRACE_S:
                    data = data.replace(DROP, b"")
                # Keep a cut-off "ESC [ 3" for the next read.
                tail = b""
                for n in range(len(DROP) - 1, 0, -1):
                    if data.endswith(DROP[:n]):
                        tail, data = data[-n:], data[:-n]
                        break
                if data:
                    write_all(1, data)
            if 0 in ready:
                try:
                    data = os.read(0, 65536)
                except OSError:
                    break
                if not data:
                    break
                write_all(fd, data)
    finally:
        if tail:
            write_all(1, tail)
        termios.tcsetattr(0, termios.TCSADRAIN, saved)
    while True:
        try:
            _, status = os.waitpid(pid, 0)
            break
        except InterruptedError:
            continue
        except ChildProcessError:
            return 0
    return os.waitstatus_to_exitcode(status) if status else 0


if __name__ == "__main__":
    code = main()
    sys.exit(code if code >= 0 else 128 - code)
