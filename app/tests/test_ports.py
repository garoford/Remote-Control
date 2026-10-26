import socket
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from remote_control import tunnel
from remote_control.tunnel import TunnelError, TunnelService


def _listener() -> socket.socket:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock


class PortTests(unittest.TestCase):
    def test_taken_default_port_is_skipped(self) -> None:
        # Debian's ttyd.service on 7681: the proxy must go elsewhere.
        with _listener() as busy:
            taken = busy.getsockname()[1]
            with mock.patch.object(tunnel, "DEFAULT_PORT", taken):
                svc = TunnelService()
                port = svc._pick_proxy_port()
                self.assertNotEqual(port, taken)
                svc.port = port
                self.assertNotIn(svc._pick_internal_port(), (port, taken))

    def test_free_default_port_is_kept(self) -> None:
        with _listener() as probe:
            free = probe.getsockname()[1]
        with mock.patch.object(tunnel, "DEFAULT_PORT", free):
            self.assertEqual(TunnelService()._pick_proxy_port(), free)

    def test_dead_proxy_on_an_open_port_is_an_error(self) -> None:
        # Someone else answering on the port is not our proxy running.
        with _listener() as busy, TemporaryDirectory() as tmp:
            svc = TunnelService()
            svc.port = busy.getsockname()[1]
            svc.proxy_log = Path(tmp) / "proxy.log"
            svc.proxy_pid_file = Path(tmp) / "proxy.pid"
            crash = [sys.executable, "-c", "print('Address already in use'); raise SystemExit(1)"]
            with mock.patch.object(svc, "_proxy_cmd", return_value=crash):
                with self.assertRaises(TunnelError) as err:
                    svc._start_proxy()
            self.assertIn("Address already in use", str(err.exception))


if __name__ == "__main__":
    unittest.main()
