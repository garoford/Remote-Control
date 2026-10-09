import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_control.gate import (
    GateUsage,
    consume_gate_args,
    load_gate,
    preference_path,
    read_password,
    set_cookie,
    write_password,
)
from remote_control.tunnel import TunnelService


class GatePrefTests(unittest.TestCase):
    def test_password_file_is_private(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(read_password(home), "")
            write_password("clave", home)
            path = preference_path(home)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(read_password(home), "clave")
            write_password("", home)
            self.assertFalse(path.exists())
            self.assertEqual(read_password(home), "")

    def test_cli_strips_gate_flags(self) -> None:
        rest, password, clear = consume_gate_args(
            ["remote-control", "--password", "abc", "--foo"]
        )
        self.assertEqual(rest, ["remote-control", "--foo"])
        self.assertEqual(password, "abc")
        self.assertFalse(clear)

        rest, password, clear = consume_gate_args(["remote-control", "--password=abc"])
        self.assertEqual(rest, ["remote-control"])
        self.assertEqual(password, "abc")

        rest, password, clear = consume_gate_args(["remote-control", "--clear-password"])
        self.assertEqual(rest, ["remote-control"])
        self.assertIsNone(password)
        self.assertTrue(clear)

        with self.assertRaises(GateUsage):
            consume_gate_args(["remote-control", "--password"])
        with self.assertRaises(GateUsage):
            consume_gate_args(["remote-control", "--password", ""])
        with self.assertRaises(GateUsage):
            consume_gate_args(
                ["remote-control", "--password", "a", "--clear-password"]
            )

    def test_runtime_gate_is_passed_only_with_password(self) -> None:
        with TemporaryDirectory() as tmp:
            svc = TunnelService()
            svc.run_dir = Path(tmp)
            svc.gate_file = svc.run_dir / "gate.json"
            svc.ensure_dirs()
            svc._prepare_gate("secret")
            self.assertEqual(stat.S_IMODE(svc.gate_file.stat().st_mode), 0o600)
            gate = load_gate(svc.gate_file)
            self.assertTrue(gate.check("secret"))
            self.assertFalse(gate.check("other"))
            self.assertTrue(gate.cookie_ok(f"rc_gate={gate.token()}"))
            self.assertFalse(gate.cookie_ok("rc_gate=" + "ab" * 32))
            self.assertIn("--gate", svc._proxy_cmd())
            self.assertIn(str(svc.gate_file), svc._proxy_cmd())
            svc._prepare_gate(None)
            self.assertIsNone(svc._gate_path)
            self.assertFalse(svc.gate_file.exists())
            self.assertNotIn("--gate", svc._proxy_cmd())

    def test_public_cookie_is_secure_and_host_only(self) -> None:
        public = set_cookie("abc", "xyz.trycloudflare.com")
        self.assertIn("Secure", public)
        self.assertIn("HttpOnly", public)
        self.assertNotIn("Domain=", public)
        local = set_cookie("abc", "127.0.0.1:7681")
        self.assertNotIn("Secure", local)


if __name__ == "__main__":
    unittest.main()
