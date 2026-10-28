import json
import socket
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from remote_control import domains, router

from tests.test_domains import _free_port, _self_signed


def _backend(reply: bytes) -> int:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def serve() -> None:
        while True:
            conn, _ = srv.accept()
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(reply), reply))
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


class RouterTests(unittest.TestCase):
    def test_each_name_reaches_its_container_and_others_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            certs = {}
            for host in ("backend.a.com", "ia.a.com"):
                cert, key = Path(tmp) / f"{host}.pem", Path(tmp) / f"{host}.key"
                _self_signed(host, cert, key, days=60)
                certs[host] = (cert, key)
            ports = {"backend": _backend(b"soy backend"), "ia": _backend(b"soy ia")}
            routes = {"backend": "backend.a.com", "ia": "ia.a.com"}
            port = _free_port()
            with mock.patch.object(router, "load_routes", lambda: routes), \
                 mock.patch.object(router.acme, "cert_paths", lambda host: certs.get(host, (Path(tmp) / "none", Path(tmp) / "none"))):
                r = router.Router(port)
                r.reload(ports)
            threading.Thread(target=r.serve, args=("127.0.0.1",), daemon=True).start()

            def get(host: str) -> bytes:
                ctx = ssl.create_default_context(cafile=str(certs.get(host, certs["ia.a.com"])[0]))
                with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
                    with ctx.wrap_socket(raw, server_hostname=host) as tls:
                        tls.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
                        out = b""
                        while chunk := tls.recv(4096):
                            out += chunk
                        return out

            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    pass
            self.assertTrue(get("backend.a.com").endswith(b"soy backend"))
            self.assertTrue(get("ia.a.com").endswith(b"soy ia"))
            with self.assertRaises(ssl.SSLError):
                get("otro.a.com")


class EdgeTests(unittest.TestCase):
    def test_coolify_traefik_is_read_from_its_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "dynamic").mkdir()
            inspect = [{
                "Args": ["--entrypoints.http.address=:80", "--entrypoints.https.address=:443",
                         "--providers.file.directory=/traefik/dynamic/",
                         "--certificatesresolvers.letsencrypt.acme.httpchallenge=true"],
                "Mounts": [{"Source": tmp, "Destination": "/traefik"}],
                "NetworkSettings": {"Networks": {"coolify": {"Gateway": "10.0.1.1"}}},
            }]
            outs = iter(["coolify-proxy\tghcr.io/traefik:v3\n", json.dumps(inspect)])
            with mock.patch.object(domains.shutil, "which", lambda name: "/usr/bin/" + name), \
                 mock.patch.object(domains.subprocess, "run", lambda *a, **k: mock.Mock(stdout=next(outs))):
                edge = domains.detect_traefik()
        self.assertEqual((edge.https, edge.http, edge.resolver, edge.gateway), ("https", "http", "letsencrypt", "10.0.1.1"))
        self.assertEqual(edge.dynamic_dir, Path(tmp) / "dynamic")

    def test_routes_are_written_in_its_style_and_stale_ones_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edge = domains.Edge("coolify-proxy", Path(tmp), "https", "http", "letsencrypt", "10.0.1.1")
            (Path(tmp) / "remote-control-viejo.yaml").write_text("x")
            (Path(tmp) / "w5-https.yml").write_text("ajeno")
            router.sync_routes(edge, {"backend": "cr.bizz.page"}, {"backend": 17701})
            files = sorted(p.name for p in Path(tmp).iterdir())
            text = (Path(tmp) / "remote-control-backend.yaml").read_text()
        self.assertEqual(files, ["remote-control-backend.yaml", "w5-https.yml"])
        for need in ("Host(`cr.bizz.page`)", "entryPoints: [https]", "certResolver: letsencrypt",
                     "entryPoints: [http]", "redirectScheme", 'url: "http://10.0.1.1:17701"'):
            self.assertIn(need, text)

    def test_the_bridge_reaches_the_containers_port(self) -> None:
        backend = _backend(b"soy el contenedor")
        bridge = router.Bridge("127.0.0.2")
        # Same port as the container's, on another address (the proxy's network).
        bridge.sync({backend})
        with socket.create_connection(("127.0.0.2", backend), timeout=5) as conn:
            conn.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            out = b""
            while chunk := conn.recv(4096):
                out += chunk
        bridge.sync(set())
        self.assertTrue(out.endswith(b"soy el contenedor"))


class HttpRedirectTests(unittest.TestCase):
    def test_port_80_sends_our_names_to_https(self) -> None:
        r = router.Router(443)
        r.table = {"rc.bizz.page": 17700}
        reply = r.redirect_target(b"GET /x?y=1 HTTP/1.1\r\nHost: rc.bizz.page\r\n\r\n", 443)
        self.assertIn(b"301", reply)
        self.assertIn(b"Location: https://rc.bizz.page/x?y=1\r\n", reply)
        self.assertIn(b"Location: https://rc.bizz.page:8443/", r.redirect_target(b"GET / HTTP/1.1\r\nHost: rc.bizz.page:80\r\n\r\n", 8443))
        self.assertIsNone(r.redirect_target(b"GET / HTTP/1.1\r\nHost: otro.com\r\n\r\n", 443))


class AssignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = domains.DomainConfig(provider="namecom", host="rc.a.com", zone="a.com", creds={"user": "u", "token": "t"})
        self.prov = mock.Mock()
        self.saved: dict = {}
        for p in (
            mock.patch.object(router, "_account", lambda: (self.cfg, self.prov)),
            mock.patch.object(router.domains, "public_ipv4", lambda *a: "203.0.113.7"),
            mock.patch.object(router.domains, "direct_reach", lambda public: domains.Reach(True, "", 8443)),
            mock.patch.object(router.acme, "ensure_cert", lambda *a, **k: None),
            mock.patch.object(router, "load_routes", lambda: dict(self.saved)),
            mock.patch.object(router, "save_routes", lambda routes: self.saved.update(routes) or [self.saved.pop(k) for k in set(self.saved) - set(routes)]),
            mock.patch.object(router, "_tell_container"),
            mock.patch.object(router, "ensure_service"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_a_free_name_is_pointed_and_routed(self) -> None:
        self.prov.records_at.return_value = []
        url = router.assign("backend", "backend")
        self.assertEqual(url, "https://backend.a.com/")
        self.prov.point.assert_called_once_with("a.com", "backend", "203.0.113.7")
        self.assertEqual(self.saved, {"backend": "backend.a.com"})

    def test_a_name_in_use_is_refused(self) -> None:
        self.prov.records_at.return_value = [("CNAME", "shop.example.net")]
        with self.assertRaisesRegex(domains.DomainError, "ya se usa"):
            router.assign("backend", "www")
        self.prov.point.assert_not_called()

    def test_our_own_a_record_does_not_count_as_used(self) -> None:
        self.prov.records_at.return_value = [("A", "203.0.113.7")]
        self.assertEqual(router.assign("backend", "backend"), "https://backend.a.com/")

    def test_one_name_per_container(self) -> None:
        self.saved.update({"ia": "ia.a.com"})
        self.prov.records_at.return_value = []
        with self.assertRaisesRegex(domains.DomainError, "ya es del contenedor ia"):
            router.assign("backend", "ia")

    def test_unreachable_machine_is_refused(self) -> None:
        with mock.patch.object(router.domains, "direct_reach", lambda public: domains.Reach(False, "Abre el puerto 443", 8443)):
            with self.assertRaisesRegex(domains.DomainError, "Abre el puerto 443"):
                router.assign("backend", "backend")


if __name__ == "__main__":
    unittest.main()
