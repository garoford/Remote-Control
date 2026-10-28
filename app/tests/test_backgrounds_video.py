import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from remote_control import backgrounds, workspaces

MP4_HEAD = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 52
MOV_HEAD = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 52
WEBM_HEAD = b"\x1a\x45\xdf\xa3" + b"\x00" * 60


class VideoBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.folder = backgrounds.folder()
        self.folder.mkdir(parents=True)

    def wait_ready(self, vid: str, timeout: float = 60) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = backgrounds.video_state(vid)
            if state.get("state") != "processing":
                return state
            time.sleep(0.1)
        self.fail("still processing")


class SniffAndNamesTests(VideoBase):
    def test_sniff(self) -> None:
        self.assertEqual(backgrounds.sniff_video(MP4_HEAD), "mp4")
        self.assertEqual(backgrounds.sniff_video(MOV_HEAD), "mp4")
        self.assertEqual(backgrounds.sniff_video(WEBM_HEAD), "webm")
        self.assertEqual(backgrounds.sniff_video(b"hello world" * 8), "")

    def test_video_files_stay_out_of_the_image_list(self) -> None:
        for name in ("0123456789abcdef.mp4", "0123456789abcdef.poster.jpg", "thumb-olas.jpg"):
            (self.folder / name).write_bytes(b"x")
        self.assertEqual(backgrounds.list_images(), [])
        self.assertIsNone(backgrounds.path_of("0123456789abcdef.poster.jpg"))

    def test_video_file_lookup(self) -> None:
        vid = "0123456789abcdef"
        (self.folder / f"{vid}.mp4").write_bytes(b"x")
        self.assertEqual(backgrounds.video_file(vid, "full").name, f"{vid}.mp4")
        # No light version yet: phones get the full one.
        self.assertEqual(backgrounds.video_file(vid, "light").name, f"{vid}.mp4")
        self.assertIsNone(backgrounds.video_file(vid, "poster"))
        self.assertIsNone(backgrounds.video_file("../etc", "full"))
        self.assertIsNone(backgrounds.video_file(vid, "../../x"))

    def test_scale_never_upscales_and_keeps_even_sizes(self) -> None:
        f = backgrounds.scale_filter(1280)
        self.assertIn("min(1280,iw)", f)
        self.assertIn("-2", f)


class UploadTests(VideoBase):
    def test_chunks_must_come_in_order(self) -> None:
        with mock.patch.object(backgrounds, "_ingest", lambda src, source: "x"):
            r = backgrounds.upload_chunk("abcdefgh", 0, 200, MP4_HEAD + b"a" * 36)
            self.assertFalse(r["done"])
            with self.assertRaises(backgrounds.BackgroundError):
                backgrounds.upload_chunk("abcdefgh", 50, 200, b"b" * 10)

    def test_rejects_too_big_bad_token_and_non_video(self) -> None:
        with self.assertRaises(backgrounds.BackgroundError):
            backgrounds.upload_chunk("abcdefgh", 0, backgrounds.VIDEO_MAX_BYTES + 1, MP4_HEAD)
        with self.assertRaises(backgrounds.BackgroundError):
            backgrounds.upload_chunk("../x", 0, 100, MP4_HEAD)
        with self.assertRaises(backgrounds.BackgroundError):
            backgrounds.upload_chunk("abcdefgh", 0, 100, b"<html>" + b"x" * 90)

    def test_last_chunk_hands_the_file_over(self) -> None:
        seen = {}

        def ingest(src, source):
            seen["data"] = Path(src).read_bytes()
            return "0123456789abcdef"

        with mock.patch.object(backgrounds, "_ingest", ingest):
            backgrounds.upload_chunk("abcdefgh", 0, 128, MP4_HEAD)
            r = backgrounds.upload_chunk("abcdefgh", 64, 128, b"z" * 64)
        self.assertEqual(r, {"ok": True, "done": True, "video": "0123456789abcdef"})
        self.assertEqual(seen["data"], MP4_HEAD + b"z" * 64)


class CatalogTests(VideoBase):
    def test_download_over_the_limit_leaves_nothing(self) -> None:
        class Resp(io.BytesIO):
            headers = {"Content-Length": "0"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with (
            mock.patch.object(backgrounds, "VIDEO_MAX_BYTES", 1024),
            mock.patch.object(backgrounds.urllib.request, "urlopen", lambda *a, **k: Resp(MP4_HEAD + b"x" * 4096)),
        ):
            backgrounds._downloads["olas"] = 0.0
            backgrounds._download(backgrounds._catalog_item("olas"))
        self.assertEqual([p.name for p in self.folder.iterdir()], [])
        self.assertNotIn("olas", backgrounds._downloads)

    def test_cached_clip_is_not_downloaded_again(self) -> None:
        vid = "0123456789abcdef"
        (self.folder / f"{vid}.mp4").write_bytes(b"x")
        backgrounds._write_state(vid, state="ready", progress=1, source="olas")
        backgrounds._save_catalog_map({"olas": vid})
        with mock.patch.object(backgrounds.threading, "Thread") as thread:
            r = backgrounds.fetch_catalog("olas")
        thread.assert_not_called()
        self.assertEqual(r, {"ok": True, "video": vid, "state": "ready"})
        entry = next(e for e in backgrounds.catalog() if e["id"] == "olas")
        self.assertEqual((entry["video"], entry["state"]), (vid, "ready"))

    def test_unknown_clip(self) -> None:
        with self.assertRaises(backgrounds.BackgroundError):
            backgrounds.fetch_catalog("nope")

    def test_interrupted_job_reads_as_error(self) -> None:
        backgrounds._write_state("0123456789abcdef", state="processing", progress=0.3)
        self.assertEqual(backgrounds.video_state("0123456789abcdef")["state"], "error")


class CatalogShapeTests(unittest.TestCase):
    def test_entries_are_well_formed(self) -> None:
        ids = [item[0] for item in backgrounds.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        for cid, title, group, url, thumb in backgrounds.CATALOG:
            self.assertRegex(cid, r"^[a-z0-9-]+$")
            self.assertTrue(title)
            self.assertIn(group, ("lofi", "anime", "naturaleza"))
            self.assertTrue(url.startswith("https://") and url.endswith(".mp4"), url)
            self.assertTrue(thumb.startswith("https://") and thumb.endswith((".mp4", ".jpg")), thumb)
        self.assertTrue(any(item[2] == "anime" for item in backgrounds.CATALOG))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class ThumbTests(VideoBase):
    def test_thumb_from_a_still_is_downloaded_then_scaled(self) -> None:
        still = Path(self.tmp.name) / "still.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=960x540", "-frames:v", "1", str(still)],
            check=True,
            capture_output=True,
        )
        cid = next(item[0] for item in backgrounds.CATALOG if item[4].endswith(".jpg"))
        with mock.patch.object(backgrounds, "_fetch", lambda url, dest, limit: shutil.copy(still, dest)):
            path = backgrounds.catalog_thumb(cid)
        self.assertIsNotNone(path)
        self.assertGreater(path.stat().st_size, 1000)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [f"thumb-{cid}.jpg"])

    def test_failed_download_leaves_nothing(self) -> None:
        def boom(url, dest, limit):
            raise OSError("offline")

        with mock.patch.object(backgrounds, "_fetch", boom):
            self.assertIsNone(backgrounds.catalog_thumb("olas"))
        self.assertEqual(list(self.folder.iterdir()), [])


def mixkit_page(*cards):
    out = []
    for vid, title, w, h in cards:
        out.append(
            f'<div data-item-grid--video-player-item-id-value="{vid}"><img src="x" width="{w}" height="{h}">'
            f'<span class="item-grid-video-player__overlay-video-title">\n  {title}\n</span></div>'
        )
    return "".join(out)


class DiscoverTests(VideoBase):
    def test_reads_titles_from_grid_pages_too(self) -> None:
        html = (
            '<div data-item-grid--video-player-item-id-value="28085"><img alt="Heavy rain from an open window." width="460" height="259">'
            '<a href="/x" class="item-grid-video-player__overlay-link">\n Heavy rain from an open window\n</a></div>'
            '<div data-item-grid--video-player-item-id-value="9"><img alt="Only alt text." width="460" height="259"></div>'
        )
        self.assertEqual([c[1] for c in backgrounds.mixkit_cards(html)], ["Heavy rain from an open window", "Only alt text"])

    def test_reads_mixkit_cards(self) -> None:
        html = mixkit_page(("4331", "Traffic on a rainy night", 460, 259), ("77", "Tall &amp; thin", 259, 460))
        self.assertEqual(backgrounds.mixkit_cards(html), [("4331", "Traffic on a rainy night", True), ("77", "Tall & thin", False)])

    def test_anime_comes_in_blocks_until_the_bank_runs_out(self) -> None:
        seen = []
        while backgrounds.more_available()["anime"]:
            added = backgrounds.discover_more("anime")["added"]
            self.assertLessEqual(len(added), backgrounds.MORE_BLOCK)
            self.assertTrue(added)
            seen += added
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), len(backgrounds.ANIME_POOL))
        self.assertEqual(backgrounds.discover_more("anime")["added"], [])
        ids = {c["id"] for c in backgrounds.catalog()}
        self.assertTrue(set(seen) <= ids)
        # Discovered clips work like the built-in ones.
        self.assertIsNotNone(backgrounds._catalog_item(seen[0]))

    def test_mixkit_pages_skip_known_and_portrait_clips(self) -> None:
        pages = {
            "https://mixkit.co/free-stock-video/rain-window/?page=1": mixkit_page(
                ("28085", "Already in the catalog", 460, 259), ("1", "One", 460, 259), ("2", "Portrait", 259, 460)
            ),
            "https://mixkit.co/free-stock-video/rain-window/?page=2": mixkit_page(*[(str(i), f"Clip {i}", 460, 259) for i in range(10, 30)]),
        }
        urls = {"28085": "https://assets.mixkit.co/videos/28085/28085-720.mp4"}
        with (
            mock.patch.object(backgrounds, "_fetch_text", lambda url: pages.get(url, "")),
            mock.patch.object(backgrounds, "_mixkit_video_url", lambda vid: urls.get(vid, f"https://assets.mixkit.co/videos/{vid}/{vid}-1080.mp4")),
        ):
            first = backgrounds.discover_more("lofi")["added"]
            second = backgrounds.discover_more("lofi")["added"]
        self.assertEqual(first, ["mk-1"] + [f"mk-{i}" for i in range(10, 18)])
        self.assertEqual(second[:3], ["mk-18", "mk-19", "mk-20"])
        self.assertNotIn("mk-2", first + second)
        item = backgrounds._catalog_item("mk-1")
        self.assertEqual(item[2:], ("lofi", "https://assets.mixkit.co/videos/1/1-1080.mp4", "https://assets.mixkit.co/videos/1/1-thumb-360-0.jpg"))

    def test_unknown_group(self) -> None:
        with self.assertRaises(backgrounds.BackgroundError):
            backgrounds.discover_more("nope")


class EncoderTests(unittest.TestCase):
    def test_preference_order(self) -> None:
        listing = " V....D libopenh264  OpenH264\n V....D h264_nvenc  NVENC\n V....D libvpx-vp9  VP9\n"
        with (
            mock.patch.object(backgrounds, "_encoders_seen", None),
            mock.patch.object(backgrounds.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=listing)),
        ):
            names = [e[0] for e in backgrounds.encoders("ffmpeg")]
        self.assertEqual(names, ["libopenh264", "h264_nvenc", "libvpx-vp9"])


class LookTests(VideoBase):
    def test_clean_look_keeps_the_explorer_glass_in_range(self) -> None:
        self.assertEqual(workspaces._clean_look({"kind": "default"})["glass"], 10)
        self.assertEqual(workspaces._clean_look({"kind": "default", "glass": 45})["glass"], 45)
        self.assertEqual(workspaces._clean_look({"kind": "default", "glass": 500})["glass"], 70)
        self.assertEqual(workspaces._clean_look({"kind": "default", "glass": "x"})["glass"], 10)
        self.assertFalse(workspaces._clean_look({"kind": "default"})["adaptive_text"])
        self.assertTrue(workspaces._clean_look({"kind": "default", "adaptive_text": True})["adaptive_text"])
        self.assertTrue(workspaces._clean_look({"kind": "default", "adaptive_all": True})["adaptive_all"])
        self.assertFalse(workspaces._clean_look({"kind": "default", "adaptive_all": "yes"})["adaptive_all"])

    def test_clean_look_accepts_video_ids_only(self) -> None:
        self.assertEqual(workspaces._clean_look({"kind": "video", "value": "0123456789abcdef"})["kind"], "video")
        self.assertIsNone(workspaces._clean_look({"kind": "video", "value": "../x"}))

    def test_delete_video_resets_profiles_and_drops_files(self) -> None:
        vid = "0123456789abcdef"
        for name in (f"{vid}.mp4", f"{vid}.light.mp4", f"{vid}.poster.jpg"):
            (self.folder / name).write_bytes(b"x")
        backgrounds._write_state(vid, state="ready", progress=1)
        base = Path(self.tmp.name) / "state"
        base.mkdir()
        state = workspaces.load_state(base)
        state["looks"] = {"": {"kind": "video", "value": vid, "dim": 40, "blur": 0}}
        workspaces.save_state(state, base)
        self.assertTrue(workspaces.delete_background(vid, base))
        self.assertEqual(workspaces.load_state(base)["looks"][""]["kind"], "default")
        self.assertEqual(list(self.folder.iterdir()), [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class ProcessTests(VideoBase):
    def make_clip(self, path: Path, size: str = "640x360") -> None:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration=2:size={size}:rate=25", "-c:v", "libopenh264", str(path)],
            check=True,
            capture_output=True,
        )

    def test_upload_ends_as_three_files(self) -> None:
        clip = Path(self.tmp.name) / "clip.mp4"
        self.make_clip(clip, "360x640")
        data = clip.read_bytes()
        backgrounds.upload_chunk("abcdefgh", 0, len(data), data[: len(data) // 2])
        r = backgrounds.upload_chunk("abcdefgh", len(data) // 2, len(data), data[len(data) // 2 :])
        vid = r["video"]
        state = self.wait_ready(vid)
        self.assertEqual(state["state"], "ready", state)
        for which in ("full", "light", "poster"):
            self.assertIsNotNone(backgrounds.video_file(vid, which), which)
        self.assertFalse(any(p.name.startswith(".") for p in self.folder.iterdir()))
        # The same bytes again: no second copy.
        backgrounds.upload_chunk("ijklmnop", 0, len(data), data)
        self.assertEqual(len(backgrounds.list_videos()), 1)

    def test_not_a_video_ends_in_error(self) -> None:
        part = self.folder / ".up-x.part"
        part.write_bytes(MP4_HEAD + b"garbage" * 100)
        vid = backgrounds._ingest(part, "upload")
        self.assertEqual(self.wait_ready(vid)["state"], "error")
        self.assertIsNone(backgrounds.video_file(vid, "full"))


class StillSourceTests(unittest.TestCase):
    def test_previews_have_a_still_without_ffmpeg(self) -> None:
        from remote_control import backgrounds

        self.assertEqual(
            backgrounds.still_source("https://assets.mixkit.co/videos/28085/28085-360.mp4"),
            "https://assets.mixkit.co/videos/28085/28085-thumb-360-0.jpg",
        )
        self.assertEqual(backgrounds.still_source("https://x.com/a.jpg?w=1"), "https://x.com/a.jpg?w=1")
        self.assertIsNone(backgrounds.still_source("https://x.com/clip.mp4"))



class CompleteTests(unittest.TestCase):
    """A video kept as it came (no ffmpeg then) gets its still and light version later."""

    def test_missing_poster_and_light_are_made_when_asked(self) -> None:
        import shutil
        import subprocess
        import tempfile
        import time
        from pathlib import Path
        from unittest import mock

        from remote_control import backgrounds

        if not shutil.which("ffmpeg"):
            self.skipTest("sin ffmpeg")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backgrounds, "folder", lambda: Path(tmp)):
            vid = "0123456789abcdef"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "2",
                            "-pix_fmt", "yuv420p", str(Path(tmp) / f"{vid}.mp4")], check=True)
            poster = backgrounds.video_file(vid, "poster")
            self.assertTrue(poster and poster.name == f"{vid}.poster.jpg" and poster.stat().st_size > 0)
            self.assertEqual(backgrounds.video_file(vid, "light").name, f"{vid}.mp4")  # meanwhile
            for _ in range(200):
                if backgrounds.video_file(vid, "light").name.startswith(f"{vid}.light."):
                    break
                time.sleep(0.1)
            self.assertTrue(backgrounds.video_file(vid, "light").name.startswith(f"{vid}.light."))


if __name__ == "__main__":
    unittest.main()
