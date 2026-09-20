"""Diretório de dados do SteamSwap (estado de trocas, cache de compatibilidade)."""
import os
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("STEAMSWAP_HOME") or os.path.join(os.environ.get("APPDATA", str(Path.home())), "SteamSwap")
    return Path(base)


def marker_file(appid: int) -> Path:
    """Onde um atalho de destino específico avisa ao launcher qual destino abrir desta vez."""
    return data_dir() / "next_target" / f"{appid}.txt"


def selectors_dir() -> Path:
    """Onde ficam os pequenos .exe apontados pelos atalhos da área de trabalho."""
    return data_dir() / "selectors"
