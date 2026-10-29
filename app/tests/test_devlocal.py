import http.client
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from remote_control import devlocal
from remote_control.proxy import Sidecar

PAGE = b"""<!doctype html><html><head><title>Mi app</title>
<script type="module" src="/@vite/client"></script></head>
<body><a href="/">inicio</a><img src="http://localhost:5173/logo.png"></body></html>"""


class DevHandler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        DevHandler.seen.append((self.path, self.headers.get("Host"), self.headers.get("Cookie")))
        if self.path == "/go":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/")
            self.end_headers()
            return
        if self.path.startswith("/assets/"):
            body, ctype = b"console.log(1)", "text/javascript"
        else:
            body, ctype = PAGE, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "a=1")
        self.send_header("Set-Cookie", "b=2")
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        shares = {"app": {**devlocal.FIELDS, "port": 5173, "label": "app"}}
        patch = mock.patch.object(devlocal, "load_shares", lambda home=None: shares)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_prefix_enters_and_strips(self) -> None:
        self.assertEqual(devlocal.route("/p/app/src/x.ts?v=1", {})[2:], ("/src/x.ts?v=1", True))
        self.assertIsNone(devlocal.route("/p/otra/", {}))

    def test_root_paths_follow_the_cookie_but_never_remote_controls_own(self) -> None:
        cookie = {"cookie": "rc_gate=x; rc_dev=app"}
        self.assertEqual(devlocal.route("/@vite/client", cookie)[2], "/@vite/client")
        for own in ("/", "/index.html", "/token", "/rc-sessions"):
            self.assertIsNone(devlocal.route(own, cookie), own)
        self.assertIsNone(devlocal.route("/@vite/client", {}))

    def test_its_home_and_its_websockets(self) -> None:
        self.assertEqual(devlocal.route("/?rc-dev=app&x=1", {})[2], "/?x=1")
        hmr = {"cookie": "rc_dev=app", "connection": "Upgrade", "sec-websocket-protocol": "vite-hmr"}
        self.assertEqual(devlocal.route("/?token=abc", hmr)[2], "/?token=abc")
        tty = {"cookie": "rc_dev=app", "connection": "Upgrade", "sec-websocket-protocol": "tty"}
        self.assertIsNone(devlocal.route("/ws", tty))

    def test_a_page_still_under_the_prefix_takes_its_requests(self) -> None:
        self.assertEqual(devlocal.route("/assets/a.js", {"referer": "https://x.trycloudflare.com/p/app/"})[2], "/assets/a.js")

    def test_redirects_to_its_localhost_stay_on_the_tunnel(self) -> None:
        self.assertEqual(devlocal.fix_location("http://localhost:5173/", "app"), "/?rc-dev=app")
        self.assertEqual(devlocal.fix_location("http://127.0.0.1:5173/login?n=1", "app"), "/login?n=1")


class ShareTests(unittest.TestCase):
    def test_slugs_are_readable_and_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            devlocal.share(3000, True, "Mi App", home=home)
            shares = devlocal.share(4000, True, "mi app", home=home)
            self.assertEqual(set(shares), {"mi-app", "mi-app-4000"})
            shares = devlocal.share(3000, False, home=home)
            self.assertEqual(set(shares), {"mi-app-4000"})
            devlocal.clear_shares(home)
            self.assertEqual(devlocal.load_shares(home), {})


class PickTests(unittest.TestCase):
    def test_a_domain_with_its_wildcard_gives_subdomains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            devlocal.set_subdomain_base("rc.a.com:8443", home)
            item = devlocal.share(3000, True, "Tienda", home=home, proxy_port=7681)["tienda"]
        self.assertEqual((item["kind"], item["host"], item["url"]), ("sub", "tienda.rc.a.com", "https://tienda.rc.a.com:8443/"))
        self.assertEqual(devlocal.link("tienda", item), "https://tienda.rc.a.com:8443/")

    def test_without_a_domain_its_own_tunnel_and_else_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(devlocal, "start_tunnel", lambda slug, port, home=None: ("https://a-b-c.trycloudflare.com/", 4321)):
                item = devlocal.share(3000, True, "api", home=home, proxy_port=7681)["api"]
            self.assertEqual((item["kind"], item["host"], item["pid"]), ("tunnel", "a-b-c.trycloudflare.com", 4321))
            with mock.patch.object(devlocal, "start_tunnel", mock.Mock(side_effect=OSError("sin cloudflared"))):
                item = devlocal.share(4000, True, "web", home=home, proxy_port=7681)["web"]
            self.assertEqual(item["kind"], "path")
            self.assertIn("/p/web/", item["note"])

    def test_on_a_subdomain_a_cloudflare_tunnel_as_well(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            devlocal.set_subdomain_base("rc.a.com", home)
            with mock.patch.object(devlocal, "start_tunnel", lambda slug, port, home=None: ("https://x-y.trycloudflare.com/", 4321)):
                # Not published yet: it is, on its subdomain, and gets the tunnel too.
                self.assertEqual(devlocal.share_tunnel(3000, True, "tienda", home=home, proxy_port=7681), "")
            item = devlocal.load_shares(home)["tienda"]
            self.assertEqual((item["kind"], item["host"], item["cf_host"]), ("sub", "tienda.rc.a.com", "x-y.trycloudflare.com"))
            killed = []
            with mock.patch.object(devlocal, "_stop_tunnel", killed.append):
                self.assertEqual(devlocal.share_tunnel(3000, False, home=home), "")
            self.assertEqual(killed, [{"cf_pid": 4321}])
            item = devlocal.load_shares(home)["tienda"]
            self.assertEqual((item["kind"], item["cf_host"]), ("sub", ""))
            with mock.patch.object(devlocal, "start_tunnel", mock.Mock(side_effect=OSError("sin cloudflared"))):
                self.assertIn("sin cloudflared", devlocal.share_tunnel(3000, True, home=home, proxy_port=7681))


class HostedTests(unittest.TestCase):
    """An environment on its own name: public, and only it."""

    def setUp(self) -> None:
        DevHandler.seen = []
        self.dev = ThreadingHTTPServer(("127.0.0.1", 0), DevHandler)
        threading.Thread(target=self.dev.serve_forever, daemon=True).start()
        self.addCleanup(self.dev.shutdown)
        self.share = {**devlocal.FIELDS, "port": self.dev.server_port, "label": "app", "kind": "sub",
                      "host": "app.rc.a.com", "url": "https://app.rc.a.com/"}
        patch = mock.patch.object(devlocal, "load_shares", lambda home=None: {"app": self.share})
        patch.start()
        self.addCleanup(patch.stop)
        gate = Path(tempfile.mkdtemp()) / "gate.json"
        from remote_control.gate import write_runtime_gate

        write_runtime_gate(gate, "clave")
        self.port = _free_port()
        side = Sidecar("127.0.0.1", self.port, "127.0.0.1", _free_port(), Path(tempfile.mkdtemp()), gate_path=gate)
        threading.Thread(target=side.serve_forever, daemon=True).start()
        self.addCleanup(side.stop)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    def req(self, method: str, path: str, headers: dict | None = None, body: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers={"Host": "app.rc.a.com", **(headers or {})})
        resp = conn.getresponse()
        return resp, resp.read()

    def test_anyone_with_the_address_sees_it_without_the_password(self) -> None:
        resp, body = self.req("GET", "/", {"Accept": "text/html"})
        self.assertEqual(resp.status, 200)
        self.assertIn(b"Mi app", body)
        self.assertNotIn(b"password", body)
        self.assertNotIn(b"history", body)  # at the root already: no moving around

    def test_remote_control_is_not_reachable_through_it(self) -> None:
        # Its own paths there are the server's, and its cookies never get through.
        self.req("GET", "/rc-sessions", {"Cookie": "rc_gate=x; a=1"})
        self.assertEqual(DevHandler.seen[-1][0], "/rc-sessions")
        self.assertNotIn("rc_gate", DevHandler.seen[-1][2] or "")


class ServeTests(unittest.TestCase):
    def setUp(self) -> None:
        DevHandler.seen = []
        self.dev = ThreadingHTTPServer(("127.0.0.1", 0), DevHandler)
        threading.Thread(target=self.dev.serve_forever, daemon=True).start()
        self.addCleanup(self.dev.shutdown)
        port = self.dev.server_port
        shares = {"app": {**devlocal.FIELDS, "port": port, "label": "app"}}
        patch = mock.patch.object(devlocal, "load_shares", lambda home=None: shares)
        patch.start()
        self.addCleanup(patch.stop)
        self.port = _free_port()
        side = Sidecar("127.0.0.1", self.port, "127.0.0.1", _free_port(), Path(tempfile.mkdtemp()))
        threading.Thread(target=side.serve_forever, daemon=True).start()
        self.addCleanup(side.stop)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    def get(self, path: str, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp, resp.read()

    def test_the_page_comes_rewritten_with_its_cookie(self) -> None:
        resp, body = self.get("/p/app/", {"Accept": "text/html", "Cookie": "rc_gate=secreto"})
        self.assertEqual(resp.status, 200)
        self.assertIn(b"history", body)
        self.assertIn(b'href="/?rc-dev=app"', body)
        self.assertNotIn(b"localhost:5173", body)
        cookies = resp.headers.get_all("Set-Cookie")
        self.assertIn("a=1", cookies)
        self.assertIn("b=2", cookies)
        self.assertTrue(any(c.startswith("rc_dev=app") for c in cookies))
        path, host, cookie = DevHandler.seen[-1]
        self.assertEqual((path, host), ("/", f"localhost:{self.dev.server_port}"))
        self.assertIsNone(cookie)  # the password cookie never reaches it

    def test_its_files_from_the_root_and_its_redirects(self) -> None:
        resp, body = self.get("/assets/app.js", {"Cookie": "rc_dev=app"})
        self.assertEqual((resp.status, body), (200, b"console.log(1)"))
        resp, _ = self.get("/p/app/go", {"Accept": "text/html"})
        self.assertEqual((resp.status, resp.headers["Location"]), (302, "/?rc-dev=app"))
        resp, _ = self.get("/p/app")
        self.assertEqual((resp.status, resp.headers["Location"]), (302, "/p/app/"))

    def test_the_scan_finds_it_and_knows_vite(self) -> None:
        found = devlocal.probe("127.0.0.1", self.dev.server_port)
        self.assertEqual((found["title"], found["framework"], found["status"]), ("Mi app", "Vite", 200))
        items = devlocal.scan({self.port}, use_cache=False)
        mine = [e for e in items if e["port"] == self.dev.server_port]
        if mine:  # ss shows it when available here
            self.assertEqual(mine[0]["web"]["framework"], "Vite")
            self.assertIn("localhost", mine[0]["kinds"])
        self.assertFalse(any(e["port"] == self.port for e in items))


class StopTests(unittest.TestCase):
    def test_it_stops_the_process_on_that_port_and_its_publication(self) -> None:
        import subprocess
        import sys

        if not devlocal.listening():
            self.skipTest("sin ss")
        port = _free_port()
        proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        for _ in range(50):
            if any(i["port"] == port for i in devlocal.listening()):
                break
            time.sleep(0.1)
        dropped = []
        with mock.patch.object(devlocal, "share", lambda p, on, *a, **k: dropped.append((p, on))):
            self.assertEqual(devlocal.stop(port), "")
        self.assertEqual(dropped, [(port, False)])
        self.assertIsNotNone(proc.wait(timeout=5))
        self.assertFalse(any(i["port"] == port for i in devlocal.listening()))

    def test_never_remote_controls_own_nor_someone_elses(self) -> None:
        with mock.patch.object(devlocal, "share", lambda *a, **k: None), \
             mock.patch.object(devlocal, "listening", lambda: [{"port": 80, "addr": "0.0.0.0", "process": "nginx", "pid": 1}]):
            self.assertIn("otro usuario", devlocal.stop(80))
            self.assertIn("Remote Control", devlocal.stop(80, {80}))
        with mock.patch.object(devlocal, "share", lambda *a, **k: None), \
             mock.patch.object(devlocal, "listening", lambda: [{"port": 5432, "addr": "127.0.0.1", "process": "", "pid": 0}]):
            self.assertIn("No veo", devlocal.stop(5432))


if __name__ == "__main__":
    unittest.main()
