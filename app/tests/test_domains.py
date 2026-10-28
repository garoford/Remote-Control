import base64
import datetime as dt
import json
import socket
import ssl
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import NameOID

from remote_control import acme, domains
from remote_control.tls_front import TlsFront


class FakeHttp:
    """Stands in for domains._http: records each call, answers from a script."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, url, headers=None, body=None):
        self.calls.append((method, url, headers or {}, body))
        for (m, part), answer in self.answers.items():
            if m == method and part in url:
                return answer(body) if callable(answer) else answer
        return 404, b"{}"


def fake(answers):
    http = FakeHttp(answers)
    patch = mock.patch.object(domains, "_http", http)
    return http, patch


class HostTests(unittest.TestCase):
    def test_split_uses_the_longest_own_zone(self) -> None:
        zones = ["midominio.com", "otro.com.pe", "x.midominio.com"]
        self.assertEqual(domains.split_host("rc.midominio.com", zones), ("midominio.com", "rc"))
        self.assertEqual(domains.split_host("a.b.otro.com.pe", zones), ("otro.com.pe", "a.b"))
        self.assertEqual(domains.split_host("rc.x.midominio.com", zones), ("x.midominio.com", "rc"))
        self.assertEqual(domains.split_host("otro.com.pe", zones), ("otro.com.pe", ""))
        with self.assertRaises(domains.DomainError):
            domains.split_host("rc.ajeno.com", zones)

    def test_picked_domain_and_subdomain(self) -> None:
        self.assertEqual(domains.compose_host(" RC. ", "MiDominio.com."), "rc.midominio.com")
        self.assertEqual(domains.compose_host("", "midominio.com"), "midominio.com")
        cfg = domains.DomainConfig(provider="namecom", host="rc.midominio.com", zone="midominio.com")
        prov = mock.Mock()
        self.assertEqual(domains.locate(cfg, prov), ("midominio.com", "rc"))
        prov.domains.assert_not_called()

    def test_an_account_without_a_name_is_checked_by_its_domain(self) -> None:
        # The multi window/CLI saves just the account: no name for this machine.
        cfg = domains.DomainConfig(provider="namecom", host="", zone="bizz.page", creds={"user": "u", "token": "t"})
        _http, patch = fake({("GET", "/domains"): (200, b'{"domains": [{"domainName": "bizz.page"}, {"domainName": "a.pe"}]}')})
        with patch:
            self.assertIn("bizz.page", domains.check(cfg))
            cfg.zone = "ajeno.com"
            with self.assertRaisesRegex(domains.DomainError, "no está en esta cuenta"):
                domains.check(cfg)

    def test_account_domains_needs_the_key_first(self) -> None:
        with self.assertRaisesRegex(domains.DomainError, "API token"):
            domains.account_domains("namecom", {"user": "me"})
        http, patch = fake({("GET", "/domains"): (200, b'{"domains": [{"domainName": "B.com"}, {"domainName": "a.pe"}]}')})
        with patch:
            self.assertEqual(domains.account_domains("namecom", {"user": "me", "token": "t"}), ["a.pe", "b.com"])

    def test_valid_host(self) -> None:
        self.assertTrue(domains.valid_host("rc.midominio.com"))
        for bad in ("midominio", "-a.com", "a..com", "1.2.3.4", "a b.com", "ñ.com"):
            self.assertFalse(domains.valid_host(bad), bad)

    def test_a_server_whose_443_is_taken_by_another_web_server_cannot(self) -> None:
        with mock.patch.object(domains, "local_addresses", lambda: {"203.0.113.7"}), \
             mock.patch.object(domains, "local_https_port", lambda: 8443), \
             mock.patch.object(domains, "port_holder", lambda port: "traefik"), \
             mock.patch.object(domains, "port_open_outside", lambda port, public: False):
            reach = domains.direct_reach("203.0.113.7")
        self.assertFalse(reach.ok)
        self.assertIn("traefik", reach.why)

    def test_a_server_whose_443_is_taken_uses_8443_when_it_reaches_us(self) -> None:
        port = _free_port()

        def outside(ext, _public):
            if ext != domains.FALLBACK_PORT:
                return False
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
            time.sleep(0.8)
            return True

        with mock.patch.object(domains, "local_addresses", lambda: {"203.0.113.7"}), \
             mock.patch.object(domains, "local_https_port", lambda: port), \
             mock.patch.object(domains, "port_holder", lambda p: "traefik"), \
             mock.patch.object(domains, "port_open_outside", outside):
            reach = domains.direct_reach("203.0.113.7")
        self.assertEqual((reach.ok, reach.public_port), (True, domains.FALLBACK_PORT))
        self.assertIn("traefik", reach.why)

    def test_a_server_already_serving_443_with_our_router_can(self) -> None:
        with mock.patch.object(domains, "local_addresses", lambda: {"203.0.113.7"}), \
             mock.patch.object(domains, "local_https_port", lambda: 8443), \
             mock.patch.object(domains, "_ours_on", lambda port: True):
            reach = domains.direct_reach("203.0.113.7")
        self.assertEqual((reach.ok, reach.public_port), (True, 443))

    def test_a_proxy_behind_a_gateway_vps_is_used_when_443_reaches_it(self) -> None:
        # main has no public IP of its own: another VPS forwards 80/443 to it.
        edge = domains.Edge("coolify-proxy", Path("/tmp"), "https", "http", "letsencrypt", "10.0.0.1")
        with mock.patch.object(domains, "local_addresses", lambda: {"192.168.122.48"}), \
             mock.patch.object(domains, "detect_traefik", lambda: edge), \
             mock.patch.object(domains, "port_open_outside", lambda port, public: port == 443):
            reach = domains.direct_reach("161.132.48.51")
        self.assertEqual((reach.ok, reach.public_port), (True, 443))
        self.assertIn("detrás de 161.132.48.51", reach.why)
        with mock.patch.object(domains, "local_addresses", lambda: {"192.168.122.48"}), \
             mock.patch.object(domains, "detect_traefik", lambda: edge), \
             mock.patch.object(domains, "port_open_outside", lambda port, public: False):
            self.assertFalse(domains.direct_reach("161.132.48.51").ok)

    def test_a_server_with_its_own_public_ip_can(self) -> None:
        with mock.patch.object(domains, "local_addresses", lambda: {"127.0.0.1", "203.0.113.7"}), \
             mock.patch.object(domains, "local_https_port", lambda: 443), \
             mock.patch.object(domains, "port_open_outside", side_effect=AssertionError("no check needed")):
            reach = domains.direct_reach("203.0.113.7")
        self.assertTrue(reach.ok)

    def test_a_pc_can_when_its_router_forwards_443_to_us(self) -> None:
        port = _free_port()

        def outside(ext, _public):
            # The checker reaches our listener only through "443".
            if ext != 443:
                return False
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
            time.sleep(0.8)
            return True

        with mock.patch.object(domains, "local_addresses", lambda: {"192.168.1.5"}), \
             mock.patch.object(domains, "local_https_port", lambda: port), \
             mock.patch.object(domains, "port_open_outside", outside):
            reach = domains.direct_reach("203.0.113.7")
        self.assertEqual((reach.ok, reach.local_port, reach.public_port), (True, port, 443))

    def test_an_open_port_that_is_not_us_does_not_count(self) -> None:
        port = _free_port()
        with mock.patch.object(domains, "local_addresses", lambda: {"192.168.1.5"}), \
             mock.patch.object(domains, "local_https_port", lambda: port), \
             mock.patch.object(domains, "port_open_outside", lambda ext, public: True), \
             mock.patch.object(domains, "lan_route", lambda: ("192.168.1.5", "192.168.1.1")):
            reach = domains.direct_reach("203.0.113.7")
        self.assertFalse(reach.ok)
        self.assertIn(f"192.168.1.5:{port}", reach.why)
        self.assertIn("192.168.1.1", reach.why)

    def test_config_is_private_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = domains.DomainConfig(provider="godaddy", host="RC.A.com.", creds={"key": " k ", "secret": ""})
            domains.save_config(cfg, home)
            path = domains.config_path(home)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            back = domains.load_config(home)
            self.assertEqual((back.host, back.creds), ("rc.a.com", {"key": "k"}))
            self.assertEqual(back.problems(), [])
            domains.clear_config(home)
            self.assertIsNone(domains.load_config(home))


class NameComTests(unittest.TestCase):
    def test_point_updates_the_existing_record_and_drops_the_forward(self) -> None:
        records = {"records": [{"id": 7, "host": "rc", "type": "A", "answer": "1.1.1.1"}, {"id": 8, "host": "www", "type": "A"}]}
        http, patch = fake({
            ("GET", "/url/forwarding"): (200, json.dumps({"urlForwarding": [{"id": 3, "host": "rc.a.com", "forwardsTo": "x"}]}).encode()),
            ("GET", "/records"): (200, json.dumps(records).encode()),
            ("DELETE", "/urlforwarding/a.com/3"): (200, b"{}"),
            ("PUT", "/records/7"): (200, b"{}"),
        })
        with patch:
            domains.NameCom("me", "tok").point("a.com", "rc", "203.0.113.7")
        methods = [(m, u.split("/core/v1")[1].split("?")[0]) for m, u, _h, _b in http.calls]
        self.assertIn(("DELETE", "/urlforwarding/a.com/3"), methods)
        put = next(c for c in http.calls if c[0] == "PUT")
        self.assertEqual(json.loads(put[3]), {"host": "rc", "type": "A", "answer": "203.0.113.7", "ttl": 300, "id": 7})
        self.assertEqual(put[2]["Authorization"], "Basic " + base64.b64encode(b"me:tok").decode())
        self.assertTrue(put[1].startswith("https://api.name.com/core/v1/domains/a.com/records/7"))

    def test_redirect_keeps_name_coms_own_a_record(self) -> None:
        fwd = {"urlForwarding": [{"id": 3, "host": "rc.a.com", "forwardsTo": "https://old/"}]}
        http, patch = fake({
            ("GET", "/url/forwarding"): (200, json.dumps(fwd).encode()),
            ("GET", "/records"): (200, b'{"records": [{"id": 9, "host": "rc", "type": "A", "answer": "75.126.100.14"}]}'),
            ("PATCH", "/urlforwarding/a.com/3"): (200, b"{}"),
        })
        with patch:
            domains.NameCom("me", "tok").redirect("a.com", "rc", "https://new/")
        self.assertEqual([c[0] for c in http.calls if c[0] not in ("GET",)], ["PATCH"])

    def test_redirect_without_its_a_record_starts_over(self) -> None:
        state = {"a": False}

        def records(_body):
            rows = [{"id": 9, "host": "rc", "type": "A", "answer": "75.126.100.14"}] if state["a"] else []
            return 200, json.dumps({"records": rows}).encode()

        def create(_body):
            state["a"] = True
            return 200, b"{}"

        http, patch = fake({
            ("GET", "/url/forwarding"): (200, b'{"urlForwarding": [{"id": 3, "host": "rc"}]}'),
            ("GET", "/records"): records,
            ("DELETE", "/urlforwarding/a.com/3"): (200, b"{}"),
            ("POST", "/url/forwarding"): create,
        })
        with patch:
            domains.NameCom("me", "tok").redirect("a.com", "rc", "https://new/")
        self.assertEqual([c[0] for c in http.calls if c[0] != "GET"], ["DELETE", "POST"])

    def test_forward_creates_a_302(self) -> None:
        http, patch = fake({
            ("GET", "/url/forwarding"): (200, b'{"urlForwarding": []}'),
            ("GET", "/records"): (200, b'{"records": []}'),
            ("POST", "/url/forwarding"): (200, b"{}"),
        })
        with patch:
            domains.NameCom("me", "tok").forward("a.com", "rc", "https://x.trycloudflare.com/")
        post = next(c for c in http.calls if c[0] == "POST")
        self.assertEqual(json.loads(post[3]), {"forwardsTo": "https://x.trycloudflare.com/", "type": "302", "host": "rc"})

    def test_bad_key_says_so(self) -> None:
        _http, patch = fake({("GET", "/domains"): (401, b'{"message": "Unauthorized"}')})
        with patch, self.assertRaisesRegex(domains.DomainError, "rechazó la clave"):
            domains.NameCom("me", "bad").domains()


class GoDaddyTests(unittest.TestCase):
    def test_records_and_auth(self) -> None:
        http, patch = fake({("PUT", "/v1/domains/a.com/records/A/rc"): (200, b"")})
        with patch:
            domains.GoDaddy("pat").set_record("a.com", "rc", "A", "203.0.113.7")
            domains.GoDaddy("k", "s").set_record("a.com", "rc", "A", "203.0.113.7")
        self.assertEqual(http.calls[0][2]["Authorization"], "Bearer pat")
        self.assertEqual(http.calls[1][2]["Authorization"], "sso-key k:s")
        self.assertEqual(json.loads(http.calls[0][3]), [{"data": "203.0.113.7", "ttl": 600}])

    def test_apex_is_at_sign(self) -> None:
        http, patch = fake({})
        with patch:
            domains.GoDaddy("pat").delete_record("a.com", "", "TXT")
        self.assertTrue(http.calls[0][1].endswith("/v1/domains/a.com/records/TXT/@"))

    def test_forward_needs_the_customer_number(self) -> None:
        _http, patch = fake({})
        with patch, self.assertRaisesRegex(domains.DomainError, "número de cliente"):
            domains.GoDaddy("pat").forward("a.com", "rc", "https://x/")

    def test_forward_creates_when_missing(self) -> None:
        http, patch = fake({("POST", "/forwards/"): (200, b"")})
        with patch:
            domains.GoDaddy("pat", customer="123").forward("a.com", "rc", "https://x/")
        self.assertEqual([c[0] for c in http.calls], ["PUT", "POST"])
        self.assertIn("/v2/customers/123/domains/forwards/rc.a.com", http.calls[1][1])
        self.assertEqual(json.loads(http.calls[1][3]), {"type": "REDIRECT_TEMPORARY", "url": "https://x/"})


NC_HOSTS = b"""<?xml version="1.0" encoding="utf-8"?>
<ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
  <CommandResponse Type="namecheap.domains.dns.getHosts">
    <DomainDNSGetHostsResult Domain="a.com.pe" EmailType="MX" IsUsingOurDNS="true">
      <host HostId="1" Name="@" Type="A" Address="1.2.3.4" MXPref="10" TTL="1800" />
      <host HostId="2" Name="rc" Type="URL" Address="https://old.trycloudflare.com/" MXPref="10" TTL="300" />
      <host HostId="3" Name="@" Type="MX" Address="mx.a.com.pe." MXPref="5" TTL="1800" />
    </DomainDNSGetHostsResult>
  </CommandResponse>
</ApiResponse>"""
NC_OK = b"""<?xml version="1.0"?><ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response">
<CommandResponse><DomainDNSSetHostsResult Domain="a.com.pe" IsSuccess="true" /></CommandResponse></ApiResponse>"""
NC_IP_ERROR = b"""<?xml version="1.0"?><ApiResponse Status="ERROR" xmlns="http://api.namecheap.com/xml.response">
<Errors><Error Number="1011150">Invalid request IP: 203.0.113.7</Error></Errors></ApiResponse>"""


class NamecheapTests(unittest.TestCase):
    def answer(self, body):
        form = urllib.parse.parse_qs(body.decode())
        return 200, NC_HOSTS if form["Command"] == ["namecheap.domains.dns.getHosts"] else NC_OK

    def test_point_keeps_every_other_record_and_the_mail_setting(self) -> None:
        http, patch = fake({("POST", "xml.response"): self.answer})
        with patch:
            domains.Namecheap("me", "key", client_ip="203.0.113.7").point("a.com.pe", "rc", "203.0.113.7")
        forms = [urllib.parse.parse_qs(c[3].decode()) for c in http.calls]
        self.assertEqual([f["Command"][0] for f in forms], ["namecheap.domains.dns.getHosts", "namecheap.domains.dns.setHosts"])
        sent = forms[1]
        self.assertEqual((sent["SLD"], sent["TLD"], sent["ClientIp"]), (["a"], ["com.pe"], ["203.0.113.7"]))
        self.assertEqual(sent["EmailType"], ["MX"])
        rows = sorted((sent[f"HostName{i}"][0], sent[f"RecordType{i}"][0], sent[f"Address{i}"][0]) for i in range(1, 4))
        self.assertEqual(rows, [("@", "A", "1.2.3.4"), ("@", "MX", "mx.a.com.pe."), ("rc", "A", "203.0.113.7")])
        self.assertEqual(sent["MXPref2"], ["5"])
        self.assertNotIn("HostName4", sent)

    def test_redirect_is_a_url_record(self) -> None:
        http, patch = fake({("POST", "xml.response"): self.answer})
        with patch:
            domains.Namecheap("me", "key", client_ip="203.0.113.7").redirect("a.com.pe", "rc", "https://new.trycloudflare.com/")
        sent = urllib.parse.parse_qs(http.calls[-1][3].decode())
        rc = [i for i in range(1, 4) if sent[f"HostName{i}"] == ["rc"]]
        self.assertEqual(len(rc), 1)
        self.assertEqual((sent[f"RecordType{rc[0]}"], sent[f"Address{rc[0]}"]), (["URL"], ["https://new.trycloudflare.com/"]))

    def test_ip_not_whitelisted_says_where_to_fix_it(self) -> None:
        _http, patch = fake({("POST", "xml.response"): (200, NC_IP_ERROR)})
        with patch, self.assertRaisesRegex(domains.DomainError, "lista blanca"):
            domains.Namecheap("me", "key", client_ip="203.0.113.7").domains()


class AcmeTests(unittest.TestCase):
    def test_jws_signature_is_raw_es256(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        sig = acme.sign_es256(key, b"data")
        self.assertEqual(len(sig), 64)
        der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        key.public_key().verify(der, b"data", ec.ECDSA(hashes.SHA256()))
        with self.assertRaises(InvalidSignature):
            key.public_key().verify(der, b"other", ec.ECDSA(hashes.SHA256()))

    def test_thumbprint_and_txt_value(self) -> None:
        jwk = {"kty": "EC", "y": "b", "x": "a", "crv": "P-256"}
        canonical = b'{"crv":"P-256","kty":"EC","x":"a","y":"b"}'
        import hashlib

        self.assertEqual(acme.thumbprint(jwk), acme.b64(hashlib.sha256(canonical).digest()))
        expected = acme.b64(hashlib.sha256(f"tok.{acme.thumbprint(jwk)}".encode()).digest())
        self.assertEqual(acme.txt_value("tok", jwk), expected)

    def test_expiry_reads_on_cryptography_41_too(self) -> None:
        # Ubuntu 24.04's cryptography has no not_valid_after_utc.
        later = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(days=40)
        old_style = type("Cert", (), {"not_valid_after": later})()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(acme.x509, "load_pem_x509_certificate", lambda data: old_style):
            path = Path(tmp) / "c.pem"
            path.write_text("x")
            self.assertAlmostEqual(acme.days_left(path), 40, delta=0.1)

    def test_a_fresh_certificate_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = acme.cert_paths("rc.a.com", Path(tmp))
            _self_signed("rc.a.com", cert, key, days=60)
            got = acme.ensure_cert("rc.a.com", "a.com", lambda v: self.fail("no TXT"), lambda: None, home=Path(tmp))
            self.assertEqual(got, (cert, key))


def _self_signed(host: str, cert: Path, key_path: Path, days: int) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = dt.datetime.now(dt.timezone.utc)
    crt = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TlsFrontTests(unittest.TestCase):
    def test_https_reaches_the_local_port_with_the_host_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = Path(tmp) / "c.pem", Path(tmp) / "k.pem"
            _self_signed("rc.a.com", cert, key, days=60)
            backend = socket.socket()
            backend.bind(("127.0.0.1", 0))
            backend.listen(1)
            seen = {}

            def serve() -> None:
                conn, _ = backend.accept()
                seen["req"] = conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                conn.close()

            threading.Thread(target=serve, daemon=True).start()
            port = _free_port()
            TlsFront(("127.0.0.1", port), backend.getsockname(), cert, key).start()
            ctx = ssl.create_default_context(cafile=str(cert))
            with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname="rc.a.com") as tls:
                    tls.sendall(b"GET / HTTP/1.1\r\nHost: rc.a.com\r\n\r\n")
                    reply = b""
                    while chunk := tls.recv(4096):
                        reply += chunk
            backend.close()
            self.assertTrue(reply.endswith(b"ok"))
            self.assertIn(b"Host: rc.a.com", seen["req"])


class TunnelCommandTests(unittest.TestCase):
    def test_direct_mode_adds_the_public_listener(self) -> None:
        from remote_control.tunnel import TunnelService

        svc = TunnelService()
        svc._gate_path = None
        svc._tls = (Path("/c.pem"), Path("/k.pem"), 443)
        cmd = svc._proxy_cmd()
        self.assertEqual(cmd[cmd.index("--public") + 1], "0.0.0.0:443")
        self.assertEqual(cmd[cmd.index("--tls-cert") + 1], "/c.pem")
        svc._tls = None
        self.assertNotIn("--public", svc._proxy_cmd())

    def test_the_domain_is_used_only_when_reachable(self) -> None:
        from remote_control.tunnel import TunnelService

        svc = TunnelService()
        cfg = domains.DomainConfig(provider="namecom", host="rc.a.com", zone="a.com", creds={"user": "u", "token": "t"})
        with mock.patch.object(domains, "load_config", lambda: cfg), \
             mock.patch.object(domains, "public_ipv4", lambda: "203.0.113.7"), \
             mock.patch.object(domains, "direct_reach", lambda public: domains.Reach(False, "Abre el puerto 443", 8443)):
            self.assertEqual(svc._domain_plan(), (None, "203.0.113.7", "tunnel"))
        self.assertIn("rc.a.com en pausa", svc.domain_warning)
        with mock.patch.object(domains, "load_config", lambda: cfg), \
             mock.patch.object(domains, "public_ipv4", lambda: "203.0.113.7"), \
             mock.patch.object(domains, "direct_reach", lambda public: domains.Reach(True, "", 8443)):
            self.assertEqual(svc._domain_plan(), (cfg, "203.0.113.7", "direct"))


class CoversTests(unittest.TestCase):
    def test_a_wildcard_added_later_needs_a_new_certificate(self) -> None:
        from remote_control import acme

        with tempfile.TemporaryDirectory() as tmp:
            cert, key = Path(tmp) / "c.pem", Path(tmp) / "k.pem"
            _self_signed("rc.a.com", cert, key, days=60)
            self.assertTrue(acme.covers(cert, ["rc.a.com"]))
            self.assertFalse(acme.covers(cert, ["rc.a.com", "*.rc.a.com"]))


if __name__ == "__main__":
    unittest.main()
