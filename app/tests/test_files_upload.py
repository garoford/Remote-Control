import os
import tempfile
import unittest
from pathlib import Path

from remote_control import files as fs


class FolderUploadTests(unittest.TestCase):
    """A folder dropped from the PC keeps its tree, and nothing leaves the target."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_files_land_in_their_subfolders(self) -> None:
        made = fs.apply_op({"op": "mkdir", "dir": str(self.root), "name": "Proyecto"})
        top = Path(made["path"])
        out = fs.upload_chunk(str(top), "main.py", 0, 5, b"hola\n", "abcdef1234", "src/app")
        self.assertTrue(out["done"])
        self.assertEqual((top / "src" / "app" / "main.py").read_text(), "hola\n")
        fs.apply_op({"op": "mkdirs", "dir": str(top), "sub": "vacia/tambien"})
        self.assertTrue((top / "vacia" / "tambien").is_dir())

    def test_a_taken_name_gets_a_free_one(self) -> None:
        (self.root / "Proyecto").mkdir()
        made = fs.apply_op({"op": "mkdir", "dir": str(self.root), "name": "Proyecto"})
        self.assertNotEqual(Path(made["path"]).name, "Proyecto")

    def test_nothing_leaves_the_folder(self) -> None:
        for bad in ("../fuera", "a/../../fuera", "./x"):
            with self.assertRaises(fs.FsError, msg=bad):
                fs.upload_chunk(str(self.root), "x.txt", 0, 1, b"x", "abcdef1234", bad)
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.rmdir(outside) if outside.exists() else None)
        (self.root / "enlace").symlink_to(outside)
        with self.assertRaises(fs.FsError):
            fs.upload_chunk(str(self.root), "x.txt", 0, 1, b"x", "abcdef1234", "enlace")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
