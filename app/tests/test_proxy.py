import hashlib
import http.client
import http.server
import json
import socket
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_control.proxy import Sidecar


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


INDEX_HTML = (
    b"<!doctype html><html><head>"
    b'<link id="rc-font-preload-reg" rel="preload" as="font" '
    b'href="/rc-assets/font-regular-aaa.woff2">'
    b'<style id="cf-remote-theme">@font-face{font-family:\'FiraCode Nerd Font Mono\'}'
    b"</style></head><body>ttyd-index</body></html>"
)


class _Upstream(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/token"):
            body = b'{"token":"test"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = INDEX_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


class ProxyAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.assets = Path(self.tmp.name)
        payload = b"woff2-bytes"
        digest = hashlib.sha256(payload).hexdigest()[:12]
        self.font_name = f"font-regular-{digest}.woff2"
        (self.assets / self.font_name).write_bytes(payload)
        (self.assets / "cache.js").write_text("window.RC_CACHE=1;", encoding="utf-8")
        (self.assets / "sw.js").write_text("self.RC_SW=1;", encoding="utf-8")
        (self.assets / "font-mobile-ccc.woff2").write_bytes(b"woff2-mobile")
        (self.assets / "manifest.json").write_text(
            json.dumps(
                {
                    "fonts": [f"/rc-assets/{self.font_name}"],
                    "mobileFonts": ["/rc-assets/font-mobile-ccc.woff2"],
                }
            ),
            encoding="utf-8",
        )

        self.up_port = _free_port()
        self.listen_port = _free_port()
        self.upstream = http.server.HTTPServer(("127.0.0.1", self.up_port), _Upstream)
        self.up_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.up_thread.start()
        self.sidecar = Sidecar(
            "127.0.0.1",
            self.listen_port,
            "127.0.0.1",
            self.up_port,
            self.assets,
        )
        self.side_thread = threading.Thread(target=self.sidecar.serve_forever, daemon=True)
        self.side_thread.start()
        self._wait_port(self.listen_port)

    def tearDown(self) -> None:
        self.sidecar.stop()
        self.upstream.shutdown()
        self.tmp.cleanup()

    def _wait_port(self, port: int) -> None:
        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                pass
        self.fail(f"port {port} did not open")

    def _get(self, path: str, headers: dict[str, str] | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.listen_port, timeout=3)
        try:
            conn.request("GET", path, headers=headers or {})
            raw = conn.getresponse()
            body = raw.read()
            headers = {key.lower(): value for key, value in raw.getheaders()}

            class _Resp:
                def __init__(self) -> None:
                    self.status = raw.status
                    self._body = body
                    self._headers = headers

                def read(self) -> bytes:
                    return self._body

                def getheader(self, name: str) -> str | None:
                    return self._headers.get(name.lower())

            return _Resp()
        finally:
            conn.close()

    def test_font_is_immutable(self) -> None:
        resp = self._get(f"/rc-assets/{self.font_name}")
        body = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "font/woff2")
        self.assertIn("immutable", resp.getheader("Cache-Control") or "")
        self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), "*")
        self.assertEqual(body, b"woff2-bytes")

    def test_sw_allows_root_scope(self) -> None:
        resp = self._get("/rc-assets/sw.js")
        resp.read()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Service-Worker-Allowed"), "/")

    def test_index_is_proxied(self) -> None:
        resp = self._get("/")
        body = resp.read()
        self.assertIn(b"ttyd-index", body)
        self.assertIn(b"rc-font-preload-reg", body)
        self.assertEqual(resp.status, 200)

    def test_mobile_index_keeps_regular_font(self) -> None:
        resp = self._get(
            "/",
            {
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                )
            },
        )
        body = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"ttyd-index", body)
        self.assertNotIn(b"font-bold-bbb", body)
        self.assertIn(b"FiraCode Nerd Font Mono", body)
        self.assertIn(self.font_name.encode(), body)
        self.assertIn("User-Agent", resp.getheader("Vary") or "")

    def test_manifest_follows_ua(self) -> None:
        desktop = json.loads(self._get("/rc-assets/manifest.json").read())
        self.assertEqual(desktop["fonts"], [f"/rc-assets/{self.font_name}"])
        mobile = json.loads(
            self._get(
                "/rc-assets/manifest.json",
                {"Sec-CH-UA-Mobile": "?1"},
            ).read()
        )
        self.assertEqual(mobile["fonts"], [f"/rc-assets/{self.font_name}"])

    def test_token_is_proxied(self) -> None:
        resp = self._get("/token")
        self.assertEqual(json.loads(resp.read()), {"token": "test"})

    def test_unknown_asset_is_404(self) -> None:
        resp = self._get("/rc-assets/../tunnel.py")
        resp.read()
        self.assertEqual(resp.status, 404)

    def test_history_unknown_tab_is_empty(self) -> None:
        resp = self._get("/rc-history?tab=rcnotasession1")
        body = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertEqual(body.get("mode"), "none")
        self.assertEqual(body.get("lines"), [])

    def test_websocket_upgrade_is_spliced(self) -> None:
        raw = socket.create_connection(("127.0.0.1", self.listen_port), timeout=3)
        try:
            raw.sendall(
                b"GET /ws HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n"
                b"\r\n"
            )
            data = b""
            deadline = time.time() + 2
            while time.time() < deadline and b"ttyd-index" not in data:
                chunk = raw.recv(4096)
                if not chunk:
                    break
                data += chunk
            self.assertTrue(data.startswith(b"HTTP/1.") and b" 200" in data[:20])
            self.assertIn(b"ttyd-index", data)
        finally:
            raw.close()


if __name__ == "__main__":
    unittest.main()
