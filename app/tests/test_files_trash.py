import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_control import files as fs


class Home(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        for patch in (mock.patch.object(fs, "_home", lambda: self.home), mock.patch.object(fs, "_mount_points", lambda: [])):
            patch.start()
            self.addCleanup(patch.stop)


class TrashTests(Home):
    """Without gvfs (a container, a server) gio moved things to the trash but
    could not list it: the page showed it empty."""

    def test_trash_list_restore_and_forever(self) -> None:
        work = self.home / "proyecto"
        work.mkdir()
        (work / "notas.txt").write_text("a")
        (work / "carpeta").mkdir()
        (work / "carpeta" / "x").write_text("x")
        out = fs.apply_op({"op": "trash", "paths": [str(work / "notas.txt"), str(work / "carpeta")]})
        self.assertTrue(out["ok"])
        self.assertEqual(sorted(os.listdir(work)), [])
        listed = {e["name"]: e for e in fs.trash_list()["entries"]}
        self.assertEqual(set(listed), {"notas.txt", "carpeta"})
        self.assertEqual((listed["carpeta"]["kind"], listed["notas.txt"]["orig"]), ("dir", str(work / "notas.txt")))
        self.assertEqual(sorted(out["trashed"]), sorted(e["path"] for e in listed.values()))
        # The same name again does not overwrite the first.
        (work / "notas.txt").write_text("b")
        fs.apply_op({"op": "trash", "paths": [str(work / "notas.txt")]})
        self.assertEqual(len(fs.trash_list()["entries"]), 3)
        fs.apply_op({"op": "restore", "uris": [listed["notas.txt"]["path"]]})
        self.assertEqual((work / "notas.txt").read_text(), "a")
        with self.assertRaises(fs.FsError):  # already back there
            fs.apply_op({"op": "restore", "uris": [e["path"] for e in fs.trash_list()["entries"] if e["name"] == "notas.txt"]})
        fs.apply_op({"op": "deleteForever", "uris": [listed["carpeta"]["path"]]})
        self.assertEqual([e["name"] for e in fs.trash_list()["entries"]], ["notas.txt"])
        fs.apply_op({"op": "emptyTrash"})
        self.assertEqual(fs.trash_list()["entries"], [])
        self.assertEqual(os.listdir(self.home / ".local" / "share" / "Trash" / "info"), [])

    def test_only_trash_addresses(self) -> None:
        (self.home / "secreto").write_text("s")
        for uri in ("trash:///" + str(self.home / "secreto"), "trash:///..%2F..%2Fsecreto", "/etc/passwd"):
            with self.assertRaises(fs.FsError, msg=uri):
                fs.apply_op({"op": "deleteForever", "uris": [uri]})
        self.assertTrue((self.home / "secreto").exists())

    def test_a_disk_without_trash_asks_first_and_moves_nothing(self) -> None:
        (self.home / "a").write_text("a")
        with mock.patch.object(fs, "_trash_for", mock.Mock(side_effect=fs.NoTrash("x"))):
            out = fs.apply_op({"op": "trash", "paths": [str(self.home / "a")]})
        self.assertTrue(out["noTrash"])
        self.assertTrue((self.home / "a").exists())


class RecentTests(Home):
    def test_what_is_used_here_shows_up_newest_first(self) -> None:
        self.assertEqual(fs.recent()["entries"], [])
        made = fs.apply_op({"op": "newfile", "dir": str(self.home), "name": "nuevo.txt"})
        (self.home / "viejo.txt").write_text("v")
        with mock.patch.object(fs.time, "time", lambda: 1.0):
            fs.note_recent(self.home / "viejo.txt")
        fs.note_recent(self.home / "no-existe.txt")
        self.assertEqual([e["path"] for e in fs.recent()["entries"]], [made["path"], str(self.home / "viejo.txt")])

    def test_gnome_list_is_read_too(self) -> None:
        (self.home / "doc.pdf").write_text("d")
        xbel = self.home / ".local" / "share" / "recently-used.xbel"
        xbel.parent.mkdir(parents=True)
        xbel.write_text(f'<bookmark href="file://{self.home}/doc.pdf" added="2026-01-01T00:00:00Z" modified="2026-01-02T00:00:00.5Z" visited="x">')
        self.assertEqual([e["name"] for e in fs.recent()["entries"]], ["doc.pdf"])


class PinTests(Home):
    """Folders pinned to the sidebar are GTK's bookmarks, Nautilus's too."""

    def test_pin_unpin_and_order(self) -> None:
        a, b, c = (self.home / n for n in ("a", "b c", "c"))
        for d in (a, b, c):
            d.mkdir()
        marks = self.home / ".config" / "gtk-3.0" / "bookmarks"
        marks.parent.mkdir(parents=True)
        marks.write_text("sftp://server/x Servidor\nfile:///no/existe\n")
        fs.apply_op({"op": "pin", "paths": [str(a), str(b)]})
        fs.apply_op({"op": "pin", "paths": [str(a)]})  # twice: still once
        fs.apply_op({"op": "pin", "paths": [str(c)], "before": str(a)})
        self.assertEqual([m["path"] for m in fs.places()["bookmarks"]], [str(c), str(a), str(b)])
        self.assertIn("file://" + str(self.home) + "/b%20c", marks.read_text())
        fs.apply_op({"op": "pin", "paths": [str(a)], "on": False})
        b.rmdir()
        fs.apply_op({"op": "pin", "paths": [str(b)], "on": False})  # gone already: fine
        self.assertEqual([m["path"] for m in fs.places()["bookmarks"]], [str(c)])
        self.assertTrue(marks.read_text().startswith("sftp://server/x Servidor\n"))  # others kept
        (self.home / "f.txt").write_text("x")
        with self.assertRaises(fs.FsError):
            fs.apply_op({"op": "pin", "paths": [str(self.home / "f.txt")]})


if __name__ == "__main__":
    unittest.main()
