"""Compila o SteamSwap.exe com o Nuitka (onefile, sem console).

    python build.py

O resultado fica em dist/SteamSwap.exe. A versão vem de steamswap/version.py
(fonte única também lida pelo atualizador).
"""
import shutil
import subprocess
import sys
from pathlib import Path

from steamswap.version import APP_VERSION

ROOT = Path(__file__).parent
DIST = ROOT / "dist"


def main():
    DIST.mkdir(exist_ok=True)
    cmd = [
        sys.executable, "-m", "nuitka",
        "--onefile",
        "--windows-console-mode=disable",
        "--enable-plugin=pywebview",
        "--include-data-dir=steamswap/webui=steamswap/webui",
        "--output-dir=" + str(DIST),
        "--output-filename=SteamSwap.exe",
        "--company-name=SteamSwap",
        "--product-name=SteamSwap",
        f"--file-version={APP_VERSION}",
        f"--product-version={APP_VERSION}",
        "--file-description=SteamSwap",
        "--copyright=SteamSwap (não-comercial)",
        "--assume-yes-for-downloads",
        str(ROOT / "run.py"),
    ]
    icon = ROOT / "icon.ico"
    if icon.is_file():
        cmd.insert(-1, f"--windows-icon-from-ico={icon}")

    print("Versão:", APP_VERSION)
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)

    built = DIST / "run.dist" / "SteamSwap.exe" if (DIST / "run.dist").exists() else DIST / "SteamSwap.exe"
    if built.exists() and built != DIST / "SteamSwap.exe":
        shutil.move(str(built), str(DIST / "SteamSwap.exe"))
    print("\nGerado em:", DIST / "SteamSwap.exe")


if __name__ == "__main__":
    main()
