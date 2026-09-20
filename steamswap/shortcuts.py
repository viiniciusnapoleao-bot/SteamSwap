"""Atalhos (.lnk) na área de trabalho, criados via COM (WScript.Shell) pelo PowerShell.

Sem dependência extra (nada de pywin32): o Windows já traz o PowerShell e o
COM do Windows Script Host.
"""
import base64
import re
import subprocess
from pathlib import Path

try:
    import winreg
except ImportError:  # fora do Windows
    winreg = None

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, max_len: int = 80) -> str:
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .")
    return (cleaned or "SteamSwap")[:max_len]


def desktop_dir() -> Path:
    """A Área de Trabalho de verdade, mesmo se redirecionada (ex.: OneDrive)."""
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
                p = Path(winreg.QueryValueEx(k, "Desktop")[0])
                if p.is_dir():
                    return p
        except OSError:
            pass
    return Path.home() / "Desktop"


def _ps_str(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def create_shortcut(link_path, target_exe, description: str = "") -> Path:
    """Cria (ou substitui) um atalho .lnk apontando para target_exe."""
    link_path = Path(link_path)
    target_exe = Path(target_exe)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s); "
        "$s.TargetPath = %s; $s.WorkingDirectory = %s; $s.Description = %s; $s.Save()"
    ) % (_ps_str(str(link_path)), _ps_str(str(target_exe)), _ps_str(str(target_exe.parent)),
         _ps_str(description))
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, errors="replace")
    if proc.returncode != 0 or not link_path.is_file():
        raise RuntimeError("Falha ao criar o atalho:\n" + (proc.stdout + proc.stderr).strip())
    return link_path
