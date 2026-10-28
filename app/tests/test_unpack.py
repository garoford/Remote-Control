import gzip
import io
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from remote_control import unpack


def run(path: Path, password: str = "") -> dict:
    token = unpack.start(str(path), password)["token"]
    deadline = time.monotonic() + 30
    while (s := unpack.status(token))["state"] == "working":
        assert time.monotonic() < deadline, s
        time.sleep(0.02)
    return s


class UnpackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def leftovers(self) -> list[str]:
        return [p.name for p in self.dir.iterdir() if ".rc-extract-" in p.name]

    def make_zip(self, name: str, files: dict) -> Path:
        path = self.dir / name
        with zipfile.ZipFile(path, "w") as zf:
            for arc, data in files.items():
                zf.writestr(arc, data)
        return path

    def test_one_folder_at_the_top_comes_out_as_that_folder(self) -> None:
        for seven in ("", unpack.seven_zip()):
            if seven is None:
                continue
            with self.subTest(seven=bool(seven)), mock.patch.object(unpack, "seven_zip", lambda s=seven: s):
                src = self.make_zip("proyecto.zip", {"proyecto/a.txt": "hola", "proyecto/sub/b.txt": "b"})
                s = run(src)
                self.assertEqual(s["state"], "done", s)
                out = Path(s["path"])
                self.assertEqual((out.parent, (out / "sub" / "b.txt").read_text()), (self.dir, "b"))
                self.assertEqual(self.leftovers(), [])
                shutil.rmtree(out)

    def test_loose_files_get_a_folder_and_nothing_is_overwritten(self) -> None:
        (self.dir / "fotos").mkdir()
        src = self.make_zip("fotos.zip", {"a.txt": "1", "b.txt": "2"})
        s = run(src)
        out = Path(s["path"])
        self.assertNotEqual(out, self.dir / "fotos")
        self.assertTrue(out.name.startswith("fotos"))
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["a.txt", "b.txt"])
        self.assertEqual(list((self.dir / "fotos").iterdir()), [])

    def test_paths_never_leave_the_folder(self) -> None:
        with mock.patch.object(unpack, "seven_zip", lambda: ""):
            src = self.make_zip("malo.zip", {"../fuera.txt": "x", "/abs.txt": "y", "ok.txt": "z"})
            s = run(src)
        self.assertEqual(s["state"], "done", s)
        self.assertFalse((self.dir.parent / "fuera.txt").exists())
        self.assertFalse(Path("/abs.txt").exists())
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in ("../fuera.txt", "bien/ok.txt"):
                info = tarfile.TarInfo(name)
                info.size = 1
                tf.addfile(info, io.BytesIO(b"x"))
        (self.dir / "malo.tar.gz").write_bytes(buf.getvalue())
        s = run(self.dir / "malo.tar.gz")
        self.assertEqual(s["state"], "done", s)
        self.assertEqual(s["skipped"], 1)
        self.assertFalse((self.dir.parent / "fuera.txt").exists())
        self.assertTrue((Path(s["path"]) / "ok.txt").is_file())

    def test_a_single_gz_file(self) -> None:
        (self.dir / "datos.csv.gz").write_bytes(gzip.compress(b"a,b\n"))
        s = run(self.dir / "datos.csv.gz")
        self.assertEqual(Path(s["path"]).read_bytes(), b"a,b\n")
        self.assertEqual(Path(s["path"]).name, "datos.csv")

    @unittest.skipUnless(unpack.seven_zip(), "sin 7-Zip")
    def test_7z_and_a_password(self) -> None:
        (self.dir / "in").mkdir()
        (self.dir / "in" / "secreto.txt").write_text("42")
        made = subprocess.run([unpack.seven_zip(), "a", "-pclave", "-mhe=on", str(self.dir / "caja.7z"), str(self.dir / "in" / "secreto.txt")],
                              capture_output=True, check=False)
        self.assertEqual(made.returncode, 0, made.stdout)
        s = run(self.dir / "caja.7z")
        self.assertEqual((s["state"], s["error"]), ("password", "Este archivo tiene contraseña"))
        self.assertEqual(run(self.dir / "caja.7z", "otra")["error"], "Contraseña incorrecta")
        s = run(self.dir / "caja.7z", "clave")
        self.assertEqual(s["state"], "done", s)
        self.assertEqual(Path(s["path"]).read_text(), "42")
        self.assertEqual(self.leftovers(), [])

    def test_a_broken_one_leaves_nothing(self) -> None:
        (self.dir / "roto.zip").write_bytes(b"PK\x03\x04 esto no es un zip")
        with mock.patch.object(unpack, "seven_zip", lambda: ""):
            s = run(self.dir / "roto.zip")
        self.assertEqual(s["state"], "error")
        self.assertEqual(self.leftovers(), [])

    def test_names(self) -> None:
        self.assertEqual(unpack.kind_of("x.TAR.GZ"), "tar")
        self.assertEqual(unpack.kind_of("x.part1.rar"), "seven")
        self.assertEqual(unpack.stem_of("x.part1.rar"), "x")
        self.assertEqual(unpack.kind_of("notas.txt"), "")


if __name__ == "__main__":
    unittest.main()
