import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steamswap import compat, swap, webapi
from steamswap.steam import Game

CMD = Path(os.environ["WINDIR"]) / "System32" / "cmd.exe"


class FakeSteamApi(unittest.TestCase):
    """Mesmo padrão de Steam falsa dos outros testes, mas exercitando a Api
    (a camada que a interface web chama), não swap.py diretamente."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.steam = root / "Steam"
        (self.steam / "steamapps" / "common").mkdir(parents=True)
        self.host = self.steam / "steamapps" / "common" / "HostGame"
        self.host.mkdir(parents=True)
        (self.host / "HostGame.exe").write_bytes(b"MZ" + b"0" * 500)
        (self.host / "data.bin").write_bytes(b"orig-data")
        os.environ["STEAMSWAP_HOME"] = str(root / "state")

        self.fake_game = Game(appid=555, name="Host Game", installdir="HostGame", library=self.steam)
        self._patches = [
            patch.object(webapi, "find_steam_path", return_value=self.steam),
            patch.object(webapi, "installed_games", return_value=[self.fake_game]),
        ]
        for p in self._patches:
            p.start()
        self.api = webapi.Api()  # window fica None: _push()/browse_exe() viram no-op

    def tearDown(self):
        for p in self._patches:
            p.stop()
        os.environ.pop("STEAMSWAP_HOME", None)
        self._tmp.cleanup()

    def _fake_shortcut(self, calls):
        def create_shortcut(link_path, target_exe, description=""):
            calls.append(str(link_path))
            Path(link_path).parent.mkdir(parents=True, exist_ok=True)
            Path(link_path).write_bytes(b"fake-lnk")
            return Path(link_path)
        return create_shortcut

    def _fake_build_selector(self):
        def build_selector(appid, target_id, marker, out):
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"MZ")
            return Path(out)
        return build_selector

    # ---------- status / listagem ----------
    def test_status_and_list_games(self):
        self.assertEqual(self.api.status(), {"steamPath": str(self.steam), "gameCount": 1})
        games = self.api.list_games()
        self.assertEqual(games, [{"appid": 555, "name": "Host Game", "compat": "checking",
                                  "compatOk": False, "swapped": False}])

    def test_game_detail_unswapped_suggests_host_exe(self):
        d = self.api.game_detail(555)
        self.assertFalse(d["swapped"])
        self.assertIn("HostGame.exe", d["hostExeOptions"])
        self.assertEqual(d["hostExe"], "HostGame.exe")
        self.assertEqual(d["targets"], [])

    def test_game_detail_unknown_appid(self):
        self.assertIn("error", self.api.game_detail(999))

    # ---------- adicionar destino ----------
    def test_add_target_first_swaps_and_creates_shortcut(self):
        calls = []
        with patch.object(webapi, "build_selector", side_effect=self._fake_build_selector()), \
             patch("steamswap.shortcuts.create_shortcut", side_effect=self._fake_shortcut(calls)):
            r = self.api.add_target(555, "Principal", "exe", str(CMD), "/c exit 0", True, 0, "HostGame.exe", True)
        self.assertEqual(r, {"ok": True, "warning": None})
        self.assertEqual(len(calls), 1)
        rec = swap.load_records()[555]
        self.assertEqual(len(rec.targets), 1)
        t = next(iter(rec.targets.values()))
        self.assertEqual(t["name"], "Principal")
        self.assertEqual(t["shortcut"], calls[0])

        detail = self.api.game_detail(555)
        self.assertTrue(detail["swapped"])
        self.assertEqual(len(detail["targets"]), 1)
        self.assertTrue(detail["targets"][0]["isDefault"])
        self.assertTrue(detail["targets"][0]["hasShortcut"])

        games = self.api.list_games()
        self.assertTrue(games[0]["swapped"])

    def test_add_target_without_host_exe_fails(self):
        r = self.api.add_target(555, "P", "exe", str(CMD), "", True, 0, "", True)
        self.assertFalse(r["ok"])
        self.assertIn("executável", r["error"])

    def test_add_target_shortcut_failure_does_not_undo_target(self):
        with patch.object(webapi, "build_selector", side_effect=self._fake_build_selector()), \
             patch("steamswap.shortcuts.create_shortcut", side_effect=RuntimeError("PowerShell bloqueado")):
            r = self.api.add_target(555, "Principal", "exe", str(CMD), "", True, 0, "HostGame.exe", True)
        self.assertTrue(r["ok"])
        self.assertIn("PowerShell bloqueado", r["warning"])
        self.assertEqual(len(swap.load_records()[555].targets), 1)

    def test_add_target_second_reuses_swap(self):
        calls = []
        with patch.object(webapi, "build_selector", side_effect=self._fake_build_selector()), \
             patch("steamswap.shortcuts.create_shortcut", side_effect=self._fake_shortcut(calls)):
            self.api.add_target(555, "Primeiro", "exe", str(CMD), "", True, 0, "HostGame.exe", False)
            r = self.api.add_target(555, "Segundo", "exe", str(CMD), "", True, 0, "", True)
        self.assertTrue(r["ok"])
        self.assertEqual(len(calls), 1)  # só o segundo pediu atalho
        self.assertEqual(len(swap.load_records()[555].targets), 2)

    def test_add_target_steam_kind_cannot_target_host(self):
        r = self.api.add_target(555, "Loop", "steam", "", "", True, 555, "HostGame.exe", False)
        self.assertFalse(r["ok"])

    # ---------- gerenciar destinos ----------
    def _add_two_targets(self):
        with patch.object(webapi, "build_selector", side_effect=self._fake_build_selector()), \
             patch("steamswap.shortcuts.create_shortcut", side_effect=self._fake_shortcut([])):
            self.api.add_target(555, "Primeiro", "exe", str(CMD), "", True, 0, "HostGame.exe", True)
            self.api.add_target(555, "Segundo", "exe", str(CMD), "", True, 0, "", True)
        rec = swap.load_records()[555]
        by_name = {t["name"]: tid for tid, t in rec.targets.items()}
        return by_name["Primeiro"], by_name["Segundo"]

    def test_set_default_and_remove_target(self):
        first_id, second_id = self._add_two_targets()
        self.assertTrue(self.api.set_default_target(555, second_id)["ok"])
        self.assertEqual(swap.load_records()[555].default_id, second_id)

        r = self.api.remove_target(555, first_id)
        self.assertTrue(r["ok"])
        self.assertEqual(list(swap.load_records()[555].targets.keys()), [second_id])

        r = self.api.remove_target(555, second_id)  # último: recusa
        self.assertFalse(r["ok"])

    def test_recreate_shortcut(self):
        first_id, _ = self._add_two_targets()
        calls = []
        with patch("steamswap.shortcuts.create_shortcut", side_effect=self._fake_shortcut(calls)):
            r = self.api.recreate_shortcut(555, first_id)
        self.assertTrue(r["ok"])
        self.assertEqual(len(calls), 1)

    def test_recreate_shortcut_unknown_target(self):
        r = self.api.recreate_shortcut(555, "nao-existe")
        self.assertFalse(r["ok"])

    # ---------- restaurar ----------
    def test_restore_extra_files_needs_force(self):
        self._add_two_targets()
        (self.host / "update.pak").write_bytes(b"steam wrote this")
        r = self.api.restore(555, False)
        self.assertFalse(r["ok"])
        self.assertTrue(r["needsForce"])
        self.assertEqual(r["extraFiles"], ["update.pak"])

        r = self.api.restore(555, True)
        self.assertTrue(r["ok"])
        self.assertEqual(swap.load_records(), {})
        self.assertEqual((self.host / "data.bin").read_bytes(), b"orig-data")

    def test_restore_not_swapped(self):
        r = self.api.restore(555, False)
        self.assertFalse(r["ok"])

    # ---------- compatibilidade ----------
    def test_start_compat_scan_updates_cache_without_window(self):
        def fake_fetch(appid, retries=2):
            return compat.Compat(frozenset({28, 44}), time.time())
        with patch.object(compat, "fetch_categories", side_effect=fake_fetch):
            r = self.api.start_compat_scan(False)
            self.assertTrue(r["ok"])
            self.api._compat_thread.join(timeout=5)
        self.assertIn(555, self.api._compat_cache)
        self.assertTrue(compat.cache_file().is_file())
        games = self.api.list_games()
        self.assertEqual(games[0]["compat"], "full")
        self.assertTrue(games[0]["compatOk"])


class ApiExposureShapeTests(unittest.TestCase):
    """Regressão para um bug real do pywebview: ao montar a lista de métodos
    expostos ao JS, ele varre com dir() os atributos PÚBLICOS de Api e, para
    qualquer um que não seja método, desce recursivamente dentro dele. Um
    atributo público guardando a Window (ou qualquer objeto de sistema, como
    um Path cujo .parent pode ciclar) faz essa varredura nunca terminar,
    porque a trava de recursão do pywebview é por id() do objeto e algumas
    propriedades .NET devolvem um objeto novo a cada acesso. Por isso todo
    estado interno de Api tem que começar com "_"."""

    def test_only_underscore_or_callable_public_attributes(self):
        with patch.object(webapi, "find_steam_path", return_value=None), \
             patch.object(webapi, "installed_games", return_value=[]):
            api = webapi.Api()
        for name in dir(api):
            if name.startswith("_"):
                continue
            attr = getattr(api, name)
            self.assertTrue(callable(attr), f"Api.{name} é público e não é um método "
                            f"({type(attr)}) — o pywebview vai tentar descer nele, prefixe com _")


if __name__ == "__main__":
    unittest.main()
