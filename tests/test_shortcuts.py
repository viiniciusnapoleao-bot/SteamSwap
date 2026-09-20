import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steamswap import shortcuts


class SafeFilenameTests(unittest.TestCase):
    def test_strips_invalid_chars(self):
        self.assertEqual(shortcuts.safe_filename('Portal 2: <RTX>'), "Portal 2_ _RTX_")

    def test_empty_falls_back(self):
        self.assertEqual(shortcuts.safe_filename('   '), "SteamSwap")

    def test_truncates(self):
        self.assertEqual(len(shortcuts.safe_filename("x" * 200, max_len=10)), 10)


class DesktopDirTests(unittest.TestCase):
    def test_returns_a_directory(self):
        d = shortcuts.desktop_dir()
        self.assertIsInstance(d, Path)
        self.assertTrue(d.is_dir())  # a Área de Trabalho de verdade sempre existe


class CreateShortcutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_success_returns_link_path(self):
        link = Path(self._tmp.name) / "sub" / "Jogo - Destino.lnk"
        target = Path(self._tmp.name) / "selector.exe"
        target.write_bytes(b"MZ")

        def fake_run(cmd, **kwargs):
            link.write_bytes(b"fake-lnk")  # simula o que o WScript.Shell faria
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("steamswap.shortcuts.subprocess.run", side_effect=fake_run) as m:
            result = shortcuts.create_shortcut(link, target, description="teste")
        self.assertEqual(result, link)
        self.assertTrue(link.is_file())
        args = m.call_args[0][0]
        self.assertEqual(args[0], "powershell.exe")
        self.assertIn("-EncodedCommand", args)

    def test_failure_raises(self):
        link = Path(self._tmp.name) / "x.lnk"
        target = Path(self._tmp.name) / "selector.exe"

        def fake_run(cmd, **kwargs):
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "erro do WScript"})()

        with patch("steamswap.shortcuts.subprocess.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                shortcuts.create_shortcut(link, target)

    def test_no_real_desktop_touched(self):
        # nenhum teste desta classe deve tocar a Área de Trabalho de verdade
        with patch("steamswap.shortcuts.subprocess.run") as m:
            m.return_value = type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
            with self.assertRaises(RuntimeError):
                shortcuts.create_shortcut(shortcuts.desktop_dir() / "nao-deveria-existir.lnk",
                                          Path(self._tmp.name) / "x.exe")
        self.assertFalse((shortcuts.desktop_dir() / "nao-deveria-existir.lnk").exists())


if __name__ == "__main__":
    unittest.main()
