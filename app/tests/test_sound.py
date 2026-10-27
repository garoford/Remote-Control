import unittest
from unittest import mock

from remote_control import sound

HERDR = "/home/u/.local/bin/herdr"
# pid -> (parent, argv)
TREE = {
    # tmux server with: a herdr tab (its client also started the herdr server),
    # a device's own herdr view, and a plain tab.
    100: (1, ["tmux: server"]),
    110: (100, ["zsh"]),
    111: (110, [HERDR]),
    112: (111, [HERDR, "server"]),
    113: (112, ["zsh"]),
    114: (113, ["claude"]),
    115: (114, ["paplay", "x.wav"]),
    116: (111, ["paplay", "/tmp/herdr-sound-111-1.mp3"]),
    120: (100, [HERDR]),
    121: (120, ["paplay", "/tmp/herdr-sound-120-1.mp3"]),
    130: (100, ["zsh"]),
    131: (130, ["mpv", "song.mp3"]),
    # Not ours: a browser.
    200: (1, ["firefox"]),
    201: (200, ["firefox-audio"]),
}
PANES = {110: "rc54e0bf03aa9c70c5a7d3f8c6", 120: "hv-d4220ae573a8c5978-rc54e0bf03aa9c70c5a7d3f8c6", 130: "rcb48f00c710bd4481e4df7d7d"}
OPTIONS = {
    ("rc54e0bf03aa9c70c5a7d3f8c6", "@rc_dev"): "d21f23b4de315a3ce",
    ("rcb48f00c710bd4481e4df7d7d", "@rc_prof"): "pcuenta2",
}


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        for p in (
            mock.patch.object(sound, "_parent", lambda pid: TREE.get(pid, (0, []))[0]),
            mock.patch.object(sound, "_argv", lambda pid: TREE.get(pid, (0, []))[1]),
        ):
            p.start()
            self.addCleanup(p.stop)

    def route(self, pid):
        return sound.route(
            pid,
            PANES,
            lambda shell: {"prof": "cuenta2"} if shell == 113 else None,
            lambda session, name: OPTIONS.get((session, name), ""),
        )

    def test_a_herdr_views_ding_goes_to_its_device(self) -> None:
        self.assertEqual(self.route(121), {"dev": "d4220ae573a8c5978"})

    def test_the_tabs_own_herdr_client_goes_to_the_device_that_opened_it(self) -> None:
        self.assertEqual(self.route(116), {"dev": "d21f23b4de315a3ce"})

    def test_a_sound_inside_a_herdr_pane_goes_to_its_owner(self) -> None:
        self.assertEqual(self.route(115), {"prof": "cuenta2"})

    def test_a_plain_tab_goes_to_its_owner(self) -> None:
        self.assertEqual(self.route(131), {"prof": "cuenta2"})

    def test_other_audio_on_the_machine_is_left_alone(self) -> None:
        self.assertIsNone(self.route(201))


class PublishTests(unittest.TestCase):
    def tearDown(self) -> None:
        sound._listeners.clear()

    def test_each_page_gets_only_its_share(self) -> None:
        with mock.patch.object(sound, "available", lambda: True), mock.patch.object(sound._watcher, "start"):
            mine = sound.listen("d4220ae573a8c5978", "")
            other = sound.listen("d21f23b4de315a3ce", "cuenta2")
        self.assertIsNotNone(mine)
        sound.publish({"dev": "d4220ae573a8c5978"}, {"s": "1"})
        sound.publish({"prof": "cuenta2"}, {"s": "2"})
        sound.publish({"all": True}, {"s": "3"})
        got = lambda item: [item.events.get_nowait()["s"] for _ in range(item.events.qsize())]
        self.assertEqual(got(mine), ["1", "3"])
        self.assertEqual(got(other), ["2", "3"])
        sound.unlisten(mine)
        self.assertEqual(len(sound._listeners), 1)

    def test_bad_subscriptions_are_refused(self) -> None:
        with mock.patch.object(sound, "available", lambda: True), mock.patch.object(sound._watcher, "start"):
            self.assertIsNone(sound.listen("../x", ""))
            self.assertIsNone(sound.listen("d4220ae573a8c5978", "Bad Prof"))


class WebSocketFrameTests(unittest.TestCase):
    def test_handshake_and_frames(self) -> None:
        from remote_control import proxy

        # RFC 6455's own example.
        self.assertEqual(proxy._ws_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        self.assertEqual(proxy._ws_frame(b"hi", 0x1), b"\x81\x02hi")
        big = proxy._ws_frame(b"x" * 300, 0x1)
        self.assertEqual(big[:4], b"\x81\x7e\x01\x2c")
        self.assertEqual(len(big), 304)


if __name__ == "__main__":
    unittest.main()
