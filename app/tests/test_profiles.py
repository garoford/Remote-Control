import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_control import profiles


class RenameTests(unittest.TestCase):
    """Only the shown name changes; never the real profiles (a temp folder)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(profiles, "base_dir", lambda: Path(tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_profile_keeps_its_id_and_folder(self) -> None:
        pid = profiles.create("Luis")["id"]
        home = profiles.home_of(pid)
        self.assertEqual(profiles.rename(pid, "  Luis   Pérez "), {"ok": True})
        self.assertEqual(profiles.list_profiles(), [{"id": pid, "label": "Luis Pérez"}])
        self.assertEqual(profiles.home_of(pid), home)

    def test_the_primary_gets_a_name_too(self) -> None:
        self.assertEqual(profiles.public()["primary"], profiles.primary_name())
        self.assertTrue(profiles.rename("", "Álvaro")["ok"])
        self.assertEqual(profiles.public()["primary"], "Álvaro")

    def test_no_two_with_the_same_name(self) -> None:
        a = profiles.create("Ana")["id"]
        b = profiles.create("Beto")["id"]
        self.assertFalse(profiles.rename(b, "ana")["ok"])
        profiles.rename("", "Jefe")
        self.assertFalse(profiles.rename(a, "jefe")["ok"])
        self.assertFalse(profiles.rename(a, "   ")["ok"])
        self.assertFalse(profiles.rename("noexiste", "X")["ok"])


if __name__ == "__main__":
    unittest.main()
