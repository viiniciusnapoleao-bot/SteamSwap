import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steamswap import vdf, swap
from steamswap.steam import installed_games, find_executables, library_folders
from steamswap.stub import Target, render_source

CMD = Path(os.environ["WINDIR"]) / "System32" / "cmd.exe"


class VdfTests(unittest.TestCase):
    def test_nested_and_escapes(self):
        d = vdf.loads('"a"\n{\n\t"path"\t"C:\\\\Steam Lib"\n\t"n"\n\t{\n\t\t"1"\t"x \\"y\\""\n\t}\n}')
        self.assertEqual(d["a"]["path"], "C:\\Steam Lib")
        self.assertEqual(d["a"]["n"]["1"], 'x "y"')

    def test_truncated_raises(self):
        with self.assertRaises(ValueError):
            vdf.loads('"a"')


class StubSourceTests(unittest.TestCase):
    def test_quotes_are_escaped(self):
        src = render_source(Target(kind="exe", path=r"C:\g\a.exe", args='--x "y z"'))
        self.assertIn('@"--x ""y z"""', src)
        self.assertNotIn("@@", src)


class FakeSteam(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.steam = root / "Steam"
        self.lib2 = root / "Lib2"
        for lib in (self.steam, self.lib2):
            (lib / "steamapps" / "common").mkdir(parents=True)
        esc = str(self.lib2).replace("\\", "\\\\")
        (self.steam / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n"0"\n{\n"path" "%s"\n}\n"1"\n{\n"path" "%s"\n}\n}\n'
            % (str(self.steam).replace("\\", "\\\\"), esc))
        (self.lib2 / "steamapps" / "appmanifest_111.acf").write_text(
            '"AppState"\n{\n"appid" "111"\n"name" "Host Game"\n"installdir" "HostGame"\n}\n')
        host = self.lib2 / "steamapps" / "common" / "HostGame"
        (host / "Bin").mkdir(parents=True)
        (host / "HostGame.exe").write_bytes(b"MZ" + b"0" * 500)
        (host / "unins000.exe").write_bytes(b"MZ")
        (host / "Bin" / "big.exe").write_bytes(b"MZ" + b"0" * 5000)
        (host / "data.bin").write_bytes(b"orig-data")
        os.environ["STEAMSWAP_HOME"] = str(root / "state")
        self.host = host

    def tearDown(self):
        os.environ.pop("STEAMSWAP_HOME", None)
        self._tmp.cleanup()

    def test_discovery(self):
        self.assertEqual(len(library_folders(self.steam)), 2)
        games = installed_games(self.steam)
        self.assertEqual([(g.appid, g.name) for g in games], [(111, "Host Game")])
        exes = find_executables(games[0].path)
        self.assertEqual(exes[0], "HostGame.exe")  # nome bate com a pasta
        self.assertNotIn("unins000.exe", exes)

    def test_swap_run_restore(self):
        game = installed_games(self.steam)[0]
        target = Target(kind="exe", path=str(CMD), args="/c ping -n 4 127.0.0.1 >nul", follow_folder=False)
        rec = swap.apply_swap(game, "HostGame.exe", target)

        self.assertTrue(Path(rec.backup_dir, "data.bin").is_file())
        self.assertFalse((self.host / "data.bin").exists())
        stub = self.host / "HostGame.exe"
        t0 = time.time()
        self.assertEqual(subprocess.run([str(stub)]).returncode, 0)
        self.assertGreaterEqual(time.time() - t0, 2.0, "o launcher deve esperar o destino terminar")

        with self.assertRaises(swap.SwapError):  # trocar duas vezes
            swap.apply_swap(game, "HostGame.exe", target)

        swap.restore_swap(111)
        self.assertEqual((self.host / "data.bin").read_bytes(), b"orig-data")
        self.assertEqual((self.host / "HostGame.exe").stat().st_size, 502)
        self.assertFalse(Path(rec.backup_dir).exists())
        self.assertEqual(swap.load_records(), {})

    def test_restore_refuses_foreign_files(self):
        game = installed_games(self.steam)[0]
        swap.apply_swap(game, "HostGame.exe", Target(kind="exe", path=str(CMD), follow_folder=False))
        (self.host / "update.pak").write_bytes(b"steam wrote this")
        with self.assertRaises(swap.ExtraFilesError) as cm:
            swap.restore_swap(111)
        self.assertEqual(cm.exception.files, ["update.pak"])
        self.assertTrue((self.host / "update.pak").exists())  # nada foi apagado
        swap.restore_swap(111, force=True)
        self.assertFalse((self.host / "update.pak").exists())
        self.assertEqual((self.host / "data.bin").read_bytes(), b"orig-data")

    def test_failed_apply_changes_nothing(self):
        game = installed_games(self.steam)[0]
        with self.assertRaises(swap.SwapError):
            swap.apply_swap(game, "HostGame.exe", Target(kind="exe", path=str(self.host / "nao_existe.exe")))
        self.assertTrue((self.host / "data.bin").exists())
        self.assertFalse(self.host.with_name("HostGame.steamswap-bak").exists())

    def test_nested_host_exe(self):
        game = installed_games(self.steam)[0]
        swap.apply_swap(game, r"Bin\big.exe", Target(kind="exe", path=str(CMD), follow_folder=False))
        self.assertTrue((self.host / "Bin" / "big.exe").is_file())
        self.assertFalse((self.host / "HostGame.exe").exists())
        swap.restore_swap(111)
        self.assertTrue((self.host / "HostGame.exe").exists())


if __name__ == "__main__":
    unittest.main()
