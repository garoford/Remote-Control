import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_control.paste import PasteError, paste_dir, save_clipboard_image, sniff_ext

MINI_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class PasteImageTests(unittest.TestCase):
    def test_sniff_png_magic(self) -> None:
        self.assertEqual(sniff_ext(MINI_PNG, "application/octet-stream"), ".png")

    def test_sniff_rejects_text(self) -> None:
        self.assertIsNone(sniff_ext(b"hello", "text/plain"))

    def test_prefers_pictures(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Pictures").mkdir()
            (home / "Downloads").mkdir()
            self.assertEqual(paste_dir(home), home / "Pictures" / "Remote Control")

    def test_saves_png(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Pictures").mkdir()
            path = save_clipboard_image(
                MINI_PNG,
                "image/png",
                home=home,
                now=datetime(2026, 9, 3, 17, 55, 1),
            )
            self.assertEqual(path.name, "paste-20260903-175501.png")
            self.assertEqual(path.read_bytes(), MINI_PNG)
            self.assertTrue(path.is_relative_to(home / "Pictures" / "Remote Control"))

    def test_rejects_empty(self) -> None:
        with self.assertRaises(PasteError):
            save_clipboard_image(b"")

    def test_rejects_non_image(self) -> None:
        with self.assertRaises(PasteError):
            save_clipboard_image(b"not-an-image", "text/plain")
