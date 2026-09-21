"""Compilação de C# minúsculo via csc.exe (.NET Framework 4, já vem no Windows).

Usado tanto pelo launcher/seletor (stub.py) quanto pelo atualizador
(updater.py), que precisa de um pequeno relançador para substituir o próprio
.exe em execução.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for fw in ("Framework64", "Framework"):
        p = windir / "Microsoft.NET" / fw / "v4.0.30319" / "csc.exe"
        if p.is_file():
            return p
    raise RuntimeError("csc.exe (.NET Framework 4) não encontrado neste Windows")


def cs_string(s) -> str:
    """Escapa um valor para uma string verbatim do C# (@"...")."""
    return str(s).replace('"', '""')


def compile_cs(source: str, out_path, refs=()) -> Path:
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory(prefix="steamswap_") as tmp:
        cs = Path(tmp) / "src.cs"
        exe = Path(tmp) / "out.exe"
        cs.write_text(source, encoding="utf-8-sig")
        cmd = [str(find_csc()), "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu"]
        cmd += [f"/r:{r}" for r in refs]
        cmd += [f"/out:{exe}", str(cs)]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if proc.returncode != 0 or not exe.is_file():
            raise RuntimeError("Falha ao compilar:\n" + (proc.stdout + proc.stderr).strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe, out_path)
    return out_path
