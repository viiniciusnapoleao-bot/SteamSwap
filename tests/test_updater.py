import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steamswap import updater

CMD = Path(os.environ["WINDIR"]) / "System32" / "cmd.exe"


class VersionCompareTests(unittest.TestCase):
    def test_parses_v_prefix_and_extra_dots(self):
        self.assertEqual(updater._parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater._parse_version("1.2"), (1, 2))

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("1.1.0", local="1.0.0"))
        self.assertFalse(updater.is_newer("1.0.0", local="1.0.0"))
        self.assertFalse(updater.is_newer("0.9.0", local="1.0.0"))
        self.assertTrue(updater.is_newer("1.0.10", local="1.0.9"))


class CurrentExePathTests(unittest.TestCase):
    def test_python_interpreter_is_not_updatable(self):
        with patch.object(updater.sys, "executable", r"C:\Python314\python.exe"):
            self.assertIsNone(updater.current_exe_path())
        with patch.object(updater.sys, "executable", r"C:\Python314\pythonw.exe"):
            self.assertIsNone(updater.current_exe_path())

    def test_compiled_exe_is_updatable(self):
        with patch.object(updater.sys, "executable", r"C:\Games\SteamSwap.exe"):
            self.assertEqual(updater.current_exe_path(), Path(r"C:\Games\SteamSwap.exe"))


def _fake_release(tag, asset_name="SteamSwap.exe", size=123):
    return {
        "tag_name": tag, "body": "notas da versão",
        "assets": [{"name": asset_name, "browser_download_url": "https://example.invalid/x.exe", "size": size}],
    }


class FetchLatestTests(unittest.TestCase):
    def test_parses_release(self):
        with patch("steamswap.updater.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value = _FakeResponse(_fake_release("v2.0.0"))
            info = updater.fetch_latest()
        self.assertEqual(info.version, "2.0.0")
        self.assertEqual(info.notes, "notas da versão")
        self.assertEqual(info.size, 123)

    def test_missing_asset_returns_none(self):
        with patch("steamswap.updater.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value = _FakeResponse(_fake_release("v2.0.0", asset_name="outro.exe"))
            self.assertIsNone(updater.fetch_latest())

    def test_network_error_returns_none(self):
        with patch("steamswap.updater.urllib.request.urlopen", side_effect=urllib.error.URLError("sem rede")):
            self.assertIsNone(updater.fetch_latest())

    def test_no_releases_yet_returns_none(self):
        with patch("steamswap.updater.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value = _FakeResponse({"message": "Not Found"})
            self.assertIsNone(updater.fetch_latest())


class _FakeResponse:
    def __init__(self, data):
        self._data = json.dumps(data).encode("utf-8")
        self.headers = {}

    def read(self):
        return self._data


class CheckForUpdateTests(unittest.TestCase):
    def test_skips_when_running_from_source(self):
        with patch.object(updater, "current_exe_path", return_value=None):
            self.assertIsNone(updater.check_for_update())

    def test_none_when_already_current(self):
        with patch.object(updater, "current_exe_path", return_value=Path(r"C:\Games\SteamSwap.exe")), \
             patch.object(updater, "fetch_latest",
                          return_value=updater.UpdateInfo("1.0.0", "", "https://x", 1)):
            self.assertIsNone(updater.check_for_update())

    def test_returns_info_when_newer(self):
        with patch.object(updater, "current_exe_path", return_value=Path(r"C:\Games\SteamSwap.exe")), \
             patch.object(updater, "fetch_latest",
                          return_value=updater.UpdateInfo("9.9.9", "novidades", "https://x", 1)):
            info = updater.check_for_update()
        self.assertEqual(info.version, "9.9.9")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_tempdir = tempfile.gettempdir
        tempfile.gettempdir = lambda: self._tmp.name

    def tearDown(self):
        tempfile.gettempdir = self._orig_tempdir
        self._tmp.cleanup()

    def test_downloads_and_reports_progress(self):
        payload = b"conteudo-fake-do-exe" * 1000
        progress = []

        class Resp:
            headers = {"Content-Length": str(len(payload))}

            def __init__(self):
                self._buf = payload

            def read(self, n):
                chunk, self._buf = self._buf[:n], self._buf[n:]
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("steamswap.updater.urllib.request.urlopen", return_value=Resp()):
            info = updater.UpdateInfo("1.2.3", "", "https://example.invalid/x.exe", len(payload))
            path = updater.download(info, on_progress=lambda d, t: progress.append((d, t)))
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(progress[-1], (len(payload), len(payload)))
        self.assertIn("1.2.3", path.name)


class ApplyRelauncherTests(unittest.TestCase):
    """Testa o relançador de verdade: compila e roda o .exe gerado."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_tempdir = tempfile.gettempdir
        tempfile.gettempdir = lambda: self._tmp.name

    def tearDown(self):
        tempfile.gettempdir = self._orig_tempdir
        self._tmp.cleanup()

    def test_waits_for_pid_then_replaces_and_relaunches(self):
        # old_exe/new_exe precisam ser .exe válidos de verdade: o relançador
        # tenta ABRIR old_exe de fato no final, e um arquivo inválido faz o
        # Windows mostrar um diálogo nativo ("Aplicativo de 16 bits sem
        # suporte") que trava o processo esperando alguém clicar OK.
        from steamswap.csc import compile_cs
        root = Path(self._tmp.name)
        old_exe = root / "SteamSwap.exe"
        new_exe = root / "SteamSwap-2.0.0.exe"
        marker = root / "relaunched.txt"
        compile_cs(
            'using System.IO; class P { static void Main() { File.WriteAllText(@"%s", "ok"); } }' % marker,
            new_exe)
        old_exe.write_bytes(b"versao-antiga-qualquer-coisa")  # será substituído; nunca chega a rodar assim

        proc = subprocess.Popen([str(CMD), "/c", "ping", "-n", "3", "127.0.0.1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            updater.apply(new_exe, old_exe_path=old_exe, current_pid=proc.pid)
            deadline = time.time() + 20
            while time.time() < deadline and not marker.is_file():
                time.sleep(0.3)
        finally:
            proc.wait(timeout=10)

        self.assertTrue(marker.is_file(), "old_exe deveria ter sido substituído e relançado de verdade")
        self.assertFalse(new_exe.exists(), "o .exe novo deveria ter sido apagado após copiar")

        relauncher = root / "steamswap_relauncher.exe"
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                relauncher.unlink()
                break
            except (PermissionError, FileNotFoundError):
                time.sleep(0.3)

    def test_raises_when_running_from_source(self):
        with patch.object(updater, "current_exe_path", return_value=None):
            with self.assertRaises(RuntimeError):
                updater.apply(Path(self._tmp.name) / "novo.exe")


if __name__ == "__main__":
    unittest.main()
