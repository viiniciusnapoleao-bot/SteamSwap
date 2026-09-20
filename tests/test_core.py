import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steamswap import paths, vdf, swap
from steamswap.steam import installed_games, find_executables, library_folders
from steamswap.stub import Target, render_source, render_selector_source, build_selector

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
        t = Target(id="t1", name="Alvo", kind="exe", path=r"C:\g\a.exe", args='--x "y z"')
        src = render_source([t], "t1", r"C:\marker.txt")
        self.assertIn('@"--x ""y z"""', src)
        self.assertNotIn("@@", src)

    def test_selector_source_has_no_placeholders(self):
        src = render_selector_source(620, "t1", r"C:\marker.txt")
        self.assertNotIn("@@", src)
        self.assertIn("620", src)

    def test_render_requires_at_least_one_target(self):
        with self.assertRaises(ValueError):
            render_source([], "t1", r"C:\marker.txt")


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
        rec = swap.apply_swap(game, "HostGame.exe", "Principal", target)

        self.assertEqual(rec.default_id, next(iter(rec.targets)))
        self.assertTrue(Path(rec.backup_dir, "data.bin").is_file())
        self.assertFalse((self.host / "data.bin").exists())
        stub = self.host / "HostGame.exe"
        t0 = time.time()
        self.assertEqual(subprocess.run([str(stub)]).returncode, 0)
        self.assertGreaterEqual(time.time() - t0, 2.0, "o launcher deve esperar o destino terminar")

        with self.assertRaises(swap.SwapError):  # trocar duas vezes
            swap.apply_swap(game, "HostGame.exe", "Outro", target)

        swap.restore_swap(111)
        self.assertEqual((self.host / "data.bin").read_bytes(), b"orig-data")
        self.assertEqual((self.host / "HostGame.exe").stat().st_size, 502)
        self.assertFalse(Path(rec.backup_dir).exists())
        self.assertEqual(swap.load_records(), {})

    def test_restore_refuses_foreign_files(self):
        game = installed_games(self.steam)[0]
        swap.apply_swap(game, "HostGame.exe", "P", Target(kind="exe", path=str(CMD), follow_folder=False))
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
            swap.apply_swap(game, "HostGame.exe", "P", Target(kind="exe", path=str(self.host / "nao_existe.exe")))
        self.assertTrue((self.host / "data.bin").exists())
        self.assertFalse(self.host.with_name("HostGame.steamswap-bak").exists())

    def test_nested_host_exe(self):
        game = installed_games(self.steam)[0]
        swap.apply_swap(game, r"Bin\big.exe", "P", Target(kind="exe", path=str(CMD), follow_folder=False))
        self.assertTrue((self.host / "Bin" / "big.exe").is_file())
        self.assertFalse((self.host / "HostGame.exe").exists())
        swap.restore_swap(111)
        self.assertTrue((self.host / "HostGame.exe").exists())

    # ---------- múltiplos destinos ----------
    def test_add_remove_and_default_target(self):
        game = installed_games(self.steam)[0]
        rec = swap.apply_swap(game, "HostGame.exe", "Primeiro",
                              Target(kind="exe", path=str(CMD), follow_folder=False))
        first_id = rec.default_id

        second = swap.add_target(111, "Segundo", Target(kind="exe", path=str(CMD), follow_folder=False))
        rec = swap.load_records()[111]
        self.assertEqual(set(rec.targets), {first_id, second.id})
        self.assertEqual(rec.default_id, first_id)  # continua o primeiro

        swap.set_default_target(111, second.id)
        rec = swap.load_records()[111]
        self.assertEqual(rec.default_id, second.id)

        with self.assertRaises(swap.SwapError):  # não pode ser o próprio hospedeiro
            swap.add_target(111, "Loop", Target(kind="steam", steam_appid=111))

        swap.remove_target(111, first_id)
        rec = swap.load_records()[111]
        self.assertEqual(set(rec.targets), {second.id})
        self.assertEqual(rec.default_id, second.id)  # promovido, já que era o único restante certo

        with self.assertRaises(swap.SwapError):  # não pode remover o último
            swap.remove_target(111, second.id)

    def test_remove_target_cleans_up_shortcut(self):
        game = installed_games(self.steam)[0]
        rec = swap.apply_swap(game, "HostGame.exe", "Primeiro",
                              Target(kind="exe", path=str(CMD), follow_folder=False))
        second = swap.add_target(111, "Segundo", Target(kind="exe", path=str(CMD), follow_folder=False))
        shortcut = Path(self._tmp.name) / "atalho.lnk"
        shortcut.write_text("fake lnk")
        swap.set_target_shortcut(111, second.id, str(shortcut))

        swap.remove_target(111, second.id)
        self.assertFalse(shortcut.exists())

    def test_restore_cleans_shortcuts_and_marker(self):
        game = installed_games(self.steam)[0]
        target = Target(kind="exe", path=str(CMD), follow_folder=False)
        rec = swap.apply_swap(game, "HostGame.exe", "Principal", target)
        shortcut = Path(self._tmp.name) / "atalho.lnk"
        shortcut.write_text("fake lnk")
        swap.set_target_shortcut(111, rec.default_id, str(shortcut))
        marker = paths.marker_file(111)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(rec.default_id)

        swap.restore_swap(111)
        self.assertFalse(shortcut.exists())
        self.assertFalse(marker.exists())

    def test_legacy_single_target_record_is_migrated(self):
        legacy = {
            "111": {
                "appid": 111, "name": "Host Game", "game_dir": str(self.host),
                "backup_dir": str(self.host) + swap.BACKUP_SUFFIX, "host_exe": "HostGame.exe",
                "target": {"kind": "exe", "path": str(CMD), "args": "", "workdir": "",
                           "steam_appid": 0, "follow_folder": True},
                "created": "2026-01-01T00:00:00",
            }
        }
        f = swap.state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(legacy), encoding="utf-8")

        records = swap.load_records()
        rec = records[111]
        self.assertEqual(len(rec.targets), 1)
        self.assertEqual(rec.default_id, next(iter(rec.targets)))
        self.assertEqual(rec.targets[rec.default_id]["path"], str(CMD))


class StubSelectorTests(unittest.TestCase):
    """O launcher tem que escolher o destino certo: pelo marcador, ou pelo
    padrão quando ninguém decide a tempo (com um limite curto pro teste)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["STEAMSWAP_HOME"] = str(Path(self._tmp.name) / "state")
        self.marker = paths.marker_file(999)

    def tearDown(self):
        os.environ.pop("STEAMSWAP_HOME", None)
        self._tmp.cleanup()

    def _build(self, targets, default_id):
        from steamswap.stub import build_stub
        out = Path(self._tmp.name) / "stub.exe"
        build_stub(targets, default_id, self.marker, out, picker_timeout_ms=1200)
        return out

    def test_single_target_ignores_marker_and_runs_immediately(self):
        out_a = Path(self._tmp.name) / "a.txt"
        target = Target(id="a", name="A", kind="exe",
                        path=str(CMD), args=f'/c echo hi > "{out_a}"', follow_folder=False)
        stub = self._build([target], "a")
        t0 = time.time()
        self.assertEqual(subprocess.run([str(stub)], timeout=10).returncode, 0)
        self.assertLess(time.time() - t0, 5.0)  # não deveria aparecer o seletor com um destino só
        self.assertTrue(out_a.is_file())

    def test_marker_selects_target_without_showing_picker(self):
        out_a = Path(self._tmp.name) / "a.txt"
        out_b = Path(self._tmp.name) / "b.txt"
        ta = Target(id="a", name="A", kind="exe", path=str(CMD), args=f'/c echo a > "{out_a}"', follow_folder=False)
        tb = Target(id="b", name="B", kind="exe", path=str(CMD), args=f'/c echo b > "{out_b}"', follow_folder=False)
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_text("b")
        stub = self._build([ta, tb], "a")

        t0 = time.time()
        self.assertEqual(subprocess.run([str(stub)], timeout=10).returncode, 0)
        self.assertLess(time.time() - t0, 5.0)
        self.assertFalse(out_a.exists())
        self.assertTrue(out_b.is_file())
        self.assertFalse(self.marker.exists())  # consumido

    def test_no_marker_falls_back_to_default_after_timeout(self):
        out_a = Path(self._tmp.name) / "a.txt"
        out_b = Path(self._tmp.name) / "b.txt"
        ta = Target(id="a", name="A", kind="exe", path=str(CMD), args=f'/c echo a > "{out_a}"', follow_folder=False)
        tb = Target(id="b", name="B", kind="exe", path=str(CMD), args=f'/c echo b > "{out_b}"', follow_folder=False)
        stub = self._build([ta, tb], "b")  # padrão é "b", sem marcador

        t0 = time.time()
        self.assertEqual(subprocess.run([str(stub)], timeout=15).returncode, 0)
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 1.0)  # esperou o timeout do seletor (1.2s)
        self.assertTrue(out_b.is_file())
        self.assertFalse(out_a.exists())


class BuildSelectorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_selector_writes_marker_and_returns(self):
        marker = Path(self._tmp.name) / "next_target" / "999.txt"
        out = Path(self._tmp.name) / "selector.exe"
        build_selector(999, "abc123", marker, out)
        # AppID 999 não existe de verdade; o Process.Start do steam:// pode falhar
        # silenciosamente (try/catch no template), mas a marcação sempre acontece antes.
        self.assertEqual(subprocess.run([str(out)], timeout=10).returncode, 0)
        self.assertEqual(marker.read_text().strip(), "abc123")


if __name__ == "__main__":
    unittest.main()
