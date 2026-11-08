import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from remote_control import herdr, proxy
from remote_control.proxy import Sidecar


class HerdrInputSocketTests(unittest.TestCase):
    """/rc-herdr-input acknowledges what the page asks it to, with its id, and
    answers pings: a page whose socket stops answering replaces it."""

    def setUp(self) -> None:
        self.side = Sidecar("127.0.0.1", 0, "127.0.0.1", 0, Path(tempfile.mkdtemp()))
        self.page, server = socket.socketpair()
        self.addCleanup(self.page.close)
        self.typed = []
        patches = [
            mock.patch.object(herdr, "send_input", lambda pane, text: self.typed.append((pane, text)) or True),
            mock.patch.object(herdr, "lock_owner", lambda pane: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        headers = {"upgrade": "websocket", "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="}
        self.thread = threading.Thread(target=self.side._serve_herdr_input, args=(server, {"prof": ["p"]}, headers), daemon=True)
        self.thread.start()
        self.page.settimeout(3)
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.page.recv(1)
        self.assertIn(b"101", head)

    def send(self, msg: dict) -> None:
        self.page.sendall(proxy._ws_frame(json.dumps(msg).encode(), 0x1))

    def reply(self) -> dict:
        opcode, payload = proxy._ws_read(self.page, 1 << 16)
        self.assertEqual(opcode, 0x1)
        return json.loads(payload)

    def test_ack_carries_the_id(self) -> None:
        self.send({"pane": "w1:p1", "text": "hola", "ack": True, "id": 7})
        self.assertEqual(self.reply(), {"pane": "w1:p1", "ok": True, "id": 7})
        self.assertEqual(self.typed, [("w1:p1", "hola")])

    def test_ping_is_answered(self) -> None:
        self.send({"ping": 1, "id": 3})
        self.assertEqual(self.reply(), {"pong": True, "id": 3})
        self.assertEqual(self.typed, [])

    def test_without_ack_only_failures_answer(self) -> None:
        self.send({"pane": "w1:p1", "text": "a"})
        self.send({"ping": 1, "id": 1})
        # The first reply is the pong: the plain send said nothing.
        self.assertEqual(self.reply(), {"pong": True, "id": 1})


if __name__ == "__main__":
    unittest.main()
