import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_control import autostart, gate
from remote_control.app import main


class FakeStatus:
    def __init__(self, running: bool, url: str | None = None) -> None:
        self.running = running
        self.url = url


class AutostartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        env["HOME"] = str(self.home)
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_switch_writes_and_removes_the_entry(self) -> None:
        self.assertFalse(autostart.is_enabled())
        wrapper = self.home / ".local" / "bin" / "remote-control"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("#!/bin/sh\n")
        autostart.set_enabled(True)
        path = self.home / ".config" / "autostart" / autostart.ENTRY_NAME
        text = path.read_text()
        self.assertIn(f"Exec={wrapper} start", text)
        self.assertIn("NoDisplay=true", text)
        self.assertTrue(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertFalse(path.exists())
        self.assertFalse(autostart.is_enabled())

    def test_entry_disabled_by_the_desktop_counts_as_off(self) -> None:
        autostart.set_enabled(True)
        path = autostart.entry_path()
        path.write_text(path.read_text() + "Hidden=true\n")
        self.assertFalse(autostart.is_enabled())

    def test_without_the_installed_wrapper_it_runs_this_package(self) -> None:
        self.assertIn("-m remote_control start", autostart.start_command())

    def test_cli_subcommand(self) -> None:
        with mock.patch("builtins.print"):
            self.assertEqual(main(["remote-control", "autostart", "on"]), 0)
            self.assertTrue(autostart.is_enabled())
            self.assertEqual(main(["remote-control", "autostart", "off"]), 0)
            self.assertEqual(main(["remote-control", "autostart", "nope"]), 2)
        self.assertFalse(autostart.is_enabled())


class HeadlessStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"HOME": self.tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.tunnel = mock.Mock()
        p = mock.patch("remote_control.tunnel.TunnelService", return_value=self.tunnel)
        p.start()
        self.addCleanup(p.stop)
        self.print = mock.patch("builtins.print").start()
        self.addCleanup(mock.patch.stopall)

    def test_a_running_tunnel_is_left_alone(self) -> None:
        self.tunnel.status.return_value = FakeStatus(True, "https://x.trycloudflare.com")
        self.assertEqual(autostart.headless_start(), 0)
        self.tunnel.start.assert_not_called()

    def test_never_starts_without_a_password(self) -> None:
        self.tunnel.status.return_value = FakeStatus(False)
        self.tunnel.start.return_value = "https://y.trycloudflare.com"
        self.assertEqual(autostart.headless_start(), 0)
        saved = gate.read_password()
        self.assertTrue(saved)
        self.tunnel.start.assert_called_once_with(saved)

    def test_retries_while_the_network_comes_up(self) -> None:
        gate.write_password("clave")
        self.tunnel.status.return_value = FakeStatus(False)
        self.tunnel.start.side_effect = [RuntimeError("no network"), "https://z.trycloudflare.com"]
        with mock.patch.object(autostart.time, "sleep") as sleep:
            self.assertEqual(autostart.headless_start(attempts=3, retry_s=7), 0)
        sleep.assert_called_once_with(7)
        self.assertEqual(self.tunnel.start.call_count, 2)

    def test_gives_up_after_the_last_attempt(self) -> None:
        gate.write_password("clave")
        self.tunnel.status.return_value = FakeStatus(False)
        self.tunnel.start.side_effect = RuntimeError("Falta instalar: ttyd.")
        with mock.patch.object(autostart.time, "sleep"):
            self.assertEqual(autostart.headless_start(attempts=2, retry_s=0), 1)


if __name__ == "__main__":
    unittest.main()
