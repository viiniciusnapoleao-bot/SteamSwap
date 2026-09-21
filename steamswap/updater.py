"""Verifica, baixa e aplica atualizações a partir das Releases do GitHub.

Só faz sentido quando o SteamSwap está rodando como o .exe compilado
(Nuitka): rodando via `python run.py` a partir do código-fonte não há o
que substituir, e current_exe_path() devolve None nesse caso.

Aplicar a atualização não sobrescreve o .exe em execução (o Windows não
deixa) — em vez disso compila um pequeno relançador em C#, igual ao
launcher/seletor em stub.py, que espera este processo terminar, copia o
.exe novo por cima do antigo e reabre. A própria aplicação precisa fechar
logo depois de chamar apply().
"""
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .csc import compile_cs, cs_string
from .version import APP_VERSION, GITHUB_REPO, RELEASE_ASSET_NAME

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

RELAUNCHER_TEMPLATE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;

static class SteamSwapRelauncher
{
    const string OldExe = @"@@OLD@@";
    const string NewExe = @"@@NEW@@";
    const int OldPid = @@PID@@;

    static int Main()
    {
        try { using (Process p = Process.GetProcessById(OldPid)) p.WaitForExit(15000); } catch { }
        Exception lastError = null;
        for (int i = 0; i < 20; i++)
        {
            try { File.Copy(NewExe, OldExe, true); lastError = null; break; }
            catch (Exception ex) { lastError = ex; Thread.Sleep(500); }
        }
        try { File.Delete(NewExe); } catch { }
        if (lastError == null)
        {
            try
            {
                var psi = new ProcessStartInfo(OldExe);
                psi.WorkingDirectory = Path.GetDirectoryName(OldExe);
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch { }
        }
        return 0;
    }
}
'''


@dataclass
class UpdateInfo:
    version: str
    notes: str
    download_url: str
    size: int


def _parse_version(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return _parse_version(remote) > _parse_version(local)


def current_exe_path() -> Optional[Path]:
    """O .exe rodando agora, só quando compilado com Nuitka. None em modo dev."""
    exe = Path(sys.executable)
    if exe.stem.lower().startswith("python"):
        return None  # rodando via `python run.py`: não há .exe pra substituir
    return exe


def fetch_latest(timeout: float = 8.0) -> Optional[UpdateInfo]:
    req = urllib.request.Request(
        _API_URL, headers={"User-Agent": "SteamSwap-Updater", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except (urllib.error.URLError, ValueError, OSError):
        return None
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    asset = next((a for a in data.get("assets", []) if a.get("name") == RELEASE_ASSET_NAME), None)
    if asset is None:
        return None
    return UpdateInfo(version=tag.lstrip("vV"), notes=data.get("body") or "",
                      download_url=asset["browser_download_url"], size=asset.get("size", 0))


def check_for_update() -> Optional[UpdateInfo]:
    """None se já está atualizado, se rodando via código-fonte, ou se não deu pra checar."""
    if current_exe_path() is None:
        return None
    info = fetch_latest()
    if info and is_newer(info.version):
        return info
    return None


def download(info: UpdateInfo, on_progress: Optional[Callable[[int, int], None]] = None,
            timeout: float = 20.0) -> Path:
    dest = Path(tempfile.gettempdir()) / f"SteamSwap-{info.version}.exe"
    req = urllib.request.Request(info.download_url, headers={"User-Agent": "SteamSwap-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.size or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    return dest


def apply(new_exe_path, old_exe_path=None, current_pid: Optional[int] = None) -> None:
    """Dispara o relançador e retorna; quem chamou precisa encerrar o processo em seguida.

    old_exe_path normalmente é omitido (usa current_exe_path()); só é passado
    explicitamente pelos testes, já que rodando via `python run.py` não há
    .exe de verdade para substituir.
    """
    new_exe = Path(new_exe_path)
    old_exe = Path(old_exe_path) if old_exe_path is not None else current_exe_path()
    if old_exe is None:
        raise RuntimeError("Rodando a partir do código-fonte; não há .exe para atualizar.")
    pid = current_pid if current_pid is not None else os.getpid()
    src = (RELAUNCHER_TEMPLATE
           .replace("@@OLD@@", cs_string(old_exe))
           .replace("@@NEW@@", cs_string(new_exe))
           .replace("@@PID@@", str(pid)))
    relauncher = Path(tempfile.gettempdir()) / "steamswap_relauncher.exe"
    compile_cs(src, relauncher)
    os.startfile(str(relauncher))
