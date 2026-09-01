import socket
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_control.proxy import Sidecar
from remote_control.tunnel import TunnelService


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(port: int, timeout: float = 4.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"port {port} did not open")


class SidecarTtydTests(unittest.TestCase):
    def test_sidecar_fronts_real_ttyd(self) -> None:
        listen = _free_port()
        upstream = _free_port()
        tmp = TemporaryDirectory()
        assets = Path(tmp.name)
        svc = TunnelService()
        svc.assets_dir = assets
        svc.ensure_dirs()
        svc._prepare_rc_assets()
        svc._prepare_ttyd_index()

        ttyd = subprocess.Popen(
            [
                "ttyd",
                "--interface",
                "127.0.0.1",
                "--port",
                str(upstream),
                "--index",
                str(svc.ttyd_index),
                "/bin/true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sidecar = Sidecar("127.0.0.1", listen, "127.0.0.1", upstream, assets)
        thread = __import__("threading").Thread(target=sidecar.serve_forever, daemon=True)
        thread.start()
        try:
            _wait_port(upstream)
            _wait_port(listen)
            raw = socket.create_connection(("127.0.0.1", listen), timeout=3)
            raw.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            chunks = []
            while True:
                piece = raw.recv(65536)
                if not piece:
                    break
                chunks.append(piece)
            raw.close()
            data = b"".join(chunks)
            self.assertIn(b"200", data.split(b"\r\n", 1)[0])
            self.assertIn(b"ttyd", data.lower())
            self.assertNotIn(b"data:font/woff2;base64", data)
            self.assertIn(b"/rc-assets/cache.js", data)
            self.assertIn(b"rc-touch-boot", data)
            phone = socket.create_connection(("127.0.0.1", listen), timeout=3)
            phone.sendall(
                b"GET / HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                b"AppleWebKit/605.1.15 Mobile/15E148\r\n"
                b"Connection: close\r\n\r\n"
            )
            phone_chunks = []
            while True:
                piece = phone.recv(65536)
                if not piece:
                    break
                phone_chunks.append(piece)
            phone.close()
            phone_data = b"".join(phone_chunks)
            self.assertIn(b"200", phone_data.split(b"\r\n", 1)[0])
            self.assertNotIn(b"rc-font-preload-reg", phone_data)
            self.assertIn(b"ui-monospace", phone_data)
            if svc.font_mobile_url:
                self.assertIn(svc.font_mobile_url.encode(), phone_data)
            if svc.font_reg_url:
                self.assertIn(svc.font_reg_url.encode(), data)
                font = socket.create_connection(("127.0.0.1", listen), timeout=3)
                font.sendall(
                    f"GET {svc.font_reg_url} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
                )
                font_data = b""
                while True:
                    piece = font.recv(65536)
                    if not piece:
                        break
                    font_data += piece
                font.close()
                self.assertIn(b"font/woff2", font_data)
                self.assertIn(b"immutable", font_data)
            ws = socket.create_connection(("127.0.0.1", listen), timeout=3)
            ws.sendall(
                b"GET /ws HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n"
                b"Sec-WebSocket-Protocol: tty\r\n"
                b"\r\n"
            )
            upgrade = ws.recv(4096)
            ws.close()
            self.assertIn(b"101", upgrade.split(b"\r\n", 1)[0])
        finally:
            sidecar.stop()
            ttyd.terminate()
            try:
                ttyd.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ttyd.kill()
            tmp.cleanup()
