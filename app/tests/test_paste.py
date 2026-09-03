import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_control.paste import (
    PasteError,
    parse_paste_name,
    paste_dir,
    reserve_paste_file,
    sniff_ext,
    write_paste_file,
)

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

    def test_parse_name(self) -> None:
        self.assertEqual(parse_paste_name("paste-ab12cd34.webp"), "paste-ab12cd34.webp")
        with self.assertRaises(PasteError):
            parse_paste_name("../paste-ab12cd34.webp")
        with self.assertRaises(PasteError):
            parse_paste_name("paste-AB12CD34.webp")
        with self.assertRaises(PasteError):
            parse_paste_name("paste-ab12cd34.jpeg")

    def test_reserve_then_write(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Pictures").mkdir()
            path = reserve_paste_file("paste-ab12cd34.webp", home=home)
            self.assertEqual(path.name, "paste-ab12cd34.webp")
            self.assertEqual(path.stat().st_size, 0)
            written = write_paste_file("paste-ab12cd34.webp", MINI_PNG, home=home)
            self.assertEqual(written, path)
            self.assertEqual(path.read_bytes(), MINI_PNG)

    def test_reserve_conflict(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Pictures").mkdir()
            reserve_paste_file("paste-ab12cd34.webp", home=home)
            with self.assertRaises(PasteError):
                reserve_paste_file("paste-ab12cd34.webp", home=home)

    def test_write_without_reserve(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Pictures").mkdir()
            with self.assertRaises(PasteError) as ctx:
                write_paste_file("paste-ab12cd34.webp", MINI_PNG, home=home)
            self.assertEqual(str(ctx.exception), "not reserved")

    def test_rejects_empty_write(self) -> None:
        with self.assertRaises(PasteError):
            write_paste_file("paste-ab12cd34.webp", b"")
