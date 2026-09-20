"""Diretório de dados do SteamSwap (estado de trocas, cache de compatibilidade)."""
import os
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("STEAMSWAP_HOME") or os.path.join(os.environ.get("APPDATA", str(Path.home())), "SteamSwap")
    return Path(base)
