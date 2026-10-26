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


if __name__ == "__main__":
    unittest.main()
