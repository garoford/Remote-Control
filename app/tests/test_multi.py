import json
from pathlib import Path
import unittest
from unittest import mock

from remote_control import multi

PS = [
    {
        "Names": ["rc-backend"],
        "State": "running",
        "CreatedAt": "hace 2 días",
        "Labels": {multi.LABEL: "1", f"{multi.LABEL}.name": "backend", f"{multi.LABEL}.base": "fedora", f"{multi.LABEL}.port": "17700"},
        "Size": {"rootFsSize": 900_000_000, "rwSize": 2_400_000_000},
    },
    {
        "Names": ["rc-ia"],
        "State": "exited",
        "CreatedAt": "ayer",
        "Labels": {multi.LABEL: "1", f"{multi.LABEL}.name": "ia", f"{multi.LABEL}.base": "ubuntu", f"{multi.LABEL}.port": "17701"},
        "Size": {"rwSize": 1024},
    },
]


class FakePodman:
    def __init__(self, ps=PS):
        self.ps = ps
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "ps":
            return json.dumps(self.ps)
        if args[:2] == ("exec", "rc-nuevo") and "is-system-running" in args:
            return "running\n"
        return ""


class ListTests(unittest.TestCase):
    def test_reads_our_containers_only_and_sorts_them(self) -> None:
        fake = FakePodman()
        with mock.patch.object(multi, "podman", fake):
            boxes = multi.list_boxes()
        self.assertEqual([b.name for b in boxes], ["backend", "ia"])
        self.assertTrue(boxes[0].running)
        self.assertEqual((boxes[0].port, boxes[0].size, boxes[1].base), (17700, 2_400_000_000, "ubuntu"))
        self.assertIn(f"label={multi.LABEL}", fake.calls[0])

    def test_sizes_read_well(self) -> None:
        self.assertEqual(multi.human_size(1024), "1 KB")
        self.assertEqual(multi.human_size(2_400_000_000), "2.2 GB")


class CreateTests(unittest.TestCase):
    def test_names(self) -> None:
        for good in ("backend", "ia-lab", "x1"):
            self.assertTrue(multi.valid_name(good), good)
        for bad in ("", "Backend", "-x", "a b", "a/b", "x" * 32):
            self.assertFalse(multi.valid_name(bad), bad)

    def test_create_labels_publishes_a_free_port_and_installs(self) -> None:
        fake = FakePodman()
        with mock.patch.object(multi, "podman", fake), \
             mock.patch.object(multi, "has_image", lambda base: True), \
             mock.patch.object(multi, "install_rc") as install, \
             mock.patch.object(multi, "_free_port", lambda taken: 17702 if taken == {17700, 17701} else 0), \
             mock.patch.object(multi, "get", lambda name: name):
            multi.create("nuevo", "ubuntu")
        create = next(c for c in fake.calls if c[0] == "create")
        self.assertIn("rc-nuevo", create)
        self.assertIn(f"{multi.LABEL}.base=ubuntu", create)
        self.assertIn("127.0.0.1:17702:7681", create)
        self.assertEqual(create[-1], "localhost/remote-control-multi:ubuntu")
        self.assertIn(("start", "rc-nuevo"), fake.calls)
        install.assert_called_once()

    def test_refuses_duplicates_and_bad_bases(self) -> None:
        with mock.patch.object(multi, "podman", FakePodman()):
            with self.assertRaisesRegex(multi.MultiError, "ya existe"):
                multi.create("backend")
            with self.assertRaisesRegex(multi.MultiError, "base desconocida"):
                multi.create("otro", "gentoo")

    def test_every_base_has_what_remote_control_needs_inside(self) -> None:
        for base, text in multi.CONTAINERFILES.items():
            for need in ("systemd", "pam-systemd" if base == "ubuntu" else "systemd-pam", "ttyd", "tmux", "cloudflared", "pipewire", "herdr.dev", "/sbin/init", "linger"):
                self.assertIn(need, text, f"{base}: {need}")


class InsideTests(unittest.TestCase):
    def test_tunnel_status_parses_the_last_line(self) -> None:
        with mock.patch.object(multi, "run", lambda *a, **k: 'aviso\n{"running": true, "url": "https://x.trycloudflare.com", "port": 7681}\n'):
            self.assertEqual(multi.tunnel_status("backend")["url"], "https://x.trycloudflare.com")
        with mock.patch.object(multi, "run", side_effect=multi.MultiError("apagado")):
            self.assertFalse(multi.tunnel_status("backend")["running"])

    def test_tunnel_start_returns_the_url(self) -> None:
        running = multi.Box("backend", "fedora", "running", 17700, 0, "")
        with mock.patch.object(multi, "run", lambda *a, **k: "algo\nhttps://abc.trycloudflare.com\n"), \
             mock.patch.object(multi, "get", lambda name: running):
            self.assertEqual(multi.tunnel_start("backend"), "https://abc.trycloudflare.com")

    def test_tunnel_off_stops_the_whole_container_and_on_starts_it(self) -> None:
        calls = []
        box = multi.Box("backend", "fedora", "exited", 17700, 0, "")
        with mock.patch.object(multi, "run", lambda *a, **k: calls.append(("run", a[1:])) or "https://x.trycloudflare.com\n"), \
             mock.patch.object(multi, "stop", lambda name: calls.append(("stop", name))), \
             mock.patch.object(multi, "start", lambda name, log=None: calls.append(("start", name))), \
             mock.patch.object(multi, "get", lambda name: box):
            multi.tunnel_stop("backend")
            self.assertEqual(multi.tunnel_start("backend"), "https://x.trycloudflare.com")
        self.assertEqual([c[0] for c in calls], ["run", "stop", "start", "run"])
        self.assertEqual(calls[0][1][-1], "stop")

    def test_the_container_gets_the_pcs_terminal(self) -> None:
        script = (Path(multi.__file__).with_name("container_setup.sh")).read_text()
        for need in ("oh-my-posh", "night-owl.omp.json", "zsh-autosuggestions", "zsh-syntax-highlighting",
                     "zsh-history-substring-search", "zsh-completions", "fzf", "bat", "nvm", "bun.sh",
                     "claude.ai/install.sh", "@openai/codex", "cursor.com/install", ".config/remote-control/zshrc"):
            self.assertIn(need, script, need)

    def test_password_of_a_stopped_container_goes_through_its_file(self) -> None:
        import io
        import tarfile

        stopped = multi.Box("backend", "fedora", "exited", 17700, 0, "")
        sent = {}

        def fake_run(cmd, input=None, **kw):
            sent["cmd"], sent["tar"] = cmd, input
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.object(multi, "get", lambda name: stopped), mock.patch.object(multi.subprocess, "run", fake_run):
            new = multi.password("backend", "set", "Nueva123")
        self.assertEqual(new, "Nueva123")
        self.assertIn("--archive=false", sent["cmd"])
        with tarfile.open(fileobj=io.BytesIO(sent["tar"])) as tar:
            info = tar.getmembers()[0]
            self.assertEqual((info.name, info.uid, info.mode, tar.extractfile(info).read()), ("gate", 1000, 0o600, b"Nueva123\n"))

    def test_next_to_a_dropping_docker_podman_uses_its_iptables_rules(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "containers.conf.d" / "50-remote-control.conf"
            with mock.patch.object(multi, "NET_CONF", conf), \
                 mock.patch.object(multi, "docker_blocks_forwarding", lambda: True):
                multi.ensure_network()
                self.assertIn('firewall_driver = "iptables"', conf.read_text())
            with mock.patch.object(multi, "NET_CONF", Path(tmp) / "otro.conf"), \
                 mock.patch.object(multi, "docker_blocks_forwarding", lambda: False):
                multi.ensure_network()
                self.assertFalse((Path(tmp) / "otro.conf").exists())

    def test_commands_run_as_the_container_user(self) -> None:
        fake = FakePodman()
        with mock.patch.object(multi, "podman", fake):
            multi.run("backend", "ls")
        call = fake.calls[0]
        self.assertEqual(call[:3], ("exec", "--user", "rc"))
        self.assertIn("XDG_RUNTIME_DIR=/run/user/1000", call)
        self.assertEqual(call[-2:], ("rc-backend", "ls"))


if __name__ == "__main__":
    unittest.main()
