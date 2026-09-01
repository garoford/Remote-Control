import json
import unittest
from unittest import mock

from remote_control import tunnel


class PublicDnsTests(unittest.TestCase):
    def test_doh_true_when_answer_present(self) -> None:
        payload = json.dumps(
            {"Answer": [{"name": "x.trycloudflare.com", "type": 1, "data": "1.2.3.4"}]}
        ).encode()

        class _Resp:
            def read(self) -> bytes:
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        with mock.patch("remote_control.tunnel.urllib.request.urlopen", return_value=_Resp()):
            self.assertTrue(tunnel.dns_via_doh("x.trycloudflare.com"))

    def test_doh_false_when_empty(self) -> None:
        payload = json.dumps({"Status": 3, "Answer": []}).encode()

        class _Resp:
            def read(self) -> bytes:
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

        with mock.patch("remote_control.tunnel.urllib.request.urlopen", return_value=_Resp()):
            self.assertFalse(tunnel.dns_via_doh("missing.trycloudflare.com"))

    def test_host_resolves_localhost(self) -> None:
        self.assertTrue(tunnel.host_resolves("localhost"))

    def test_flush_does_not_raise(self) -> None:
        tunnel.flush_resolved_cache()
