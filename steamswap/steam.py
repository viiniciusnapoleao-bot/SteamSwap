"""Descoberta da Steam: bibliotecas, jogos instalados e executáveis."""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import vdf

try:
    import winreg
except ImportError:  # fora do Windows
    winreg = None

# Não são jogos: ferramentas/runtimes que a Steam instala como "apps".
_NOT_GAMES = {228980}  # Steamworks Common Redistributables
_NOT_GAME_NAMES = re.compile(r"^(Proton|Steam Linux Runtime|Steamworks)", re.I)

_IGNORED_EXE = re.compile(
    r"(unins|uninst|crashhandler|crashreport|crashpad|vc_?redist|dxsetup|dotnet|"
    r"vcredist|oalinst|installer|setup|helper|notification_helper|UnityCrash)", re.I)


@dataclass
class Game:
    appid: int
    name: str
    installdir: str
    library: Path

    @property
    def path(self) -> Path:
        return self.library / "steamapps" / "common" / self.installdir


def find_steam_path() -> Optional[Path]:
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                p = Path(winreg.QueryValueEx(k, "SteamPath")[0])
                if p.is_dir():
                    return p
        except OSError:
            pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base and (Path(base) / "Steam").is_dir():
            return Path(base) / "Steam"
    return None


def library_folders(steam: Path) -> List[Path]:
    libs = [steam]
    vdf_path = steam / "steamapps" / "libraryfolders.vdf"
    if vdf_path.is_file():
        data = vdf.load(vdf_path).get("libraryfolders", {})
        for entry in data.values():
            if isinstance(entry, dict) and "path" in entry:
                p = Path(entry["path"])
                if p not in libs and p.is_dir():
                    libs.append(p)
    return libs


def installed_games(steam: Path) -> List[Game]:
    games: List[Game] = []
    for lib in library_folders(steam):
        for manifest in sorted((lib / "steamapps").glob("appmanifest_*.acf")):
            try:
                state = vdf.load(manifest).get("AppState", {})
                appid = int(state["appid"])
                game = Game(appid, state.get("name", str(appid)), state["installdir"], lib)
            except (KeyError, ValueError, OSError):
                continue
            if appid in _NOT_GAMES or _NOT_GAME_NAMES.match(game.name):
                continue
            if game.path.is_dir():
                games.append(game)
    games.sort(key=lambda g: g.name.lower())
    return games


def find_executables(game_dir: Path, max_depth: int = 3) -> List[str]:
    """Executáveis (caminhos relativos) candidatos a serem o que a Steam abre.

    O mais provável vem primeiro: nome parecido com a pasta do jogo, senão o
    maior arquivo mais próximo da raiz.
    """
    found = []
    root_depth = len(game_dir.parts)
    for dirpath, dirnames, filenames in os.walk(game_dir):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []
        for fn in filenames:
            if fn.lower().endswith(".exe") and not _IGNORED_EXE.search(fn):
                full = Path(dirpath) / fn
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                found.append((str(full.relative_to(game_dir)), depth, size))

    key = re.sub(r"[\W_]+", "", game_dir.name).lower()

    def score(item):
        rel, depth, size = item
        stem = re.sub(r"[\W_]+", "", Path(rel).stem).lower()
        name_match = bool(key) and (stem == key or stem in key or key in stem)
        return (not name_match, depth, -size)

    return [rel for rel, _, _ in sorted(found, key=score)]


def is_app_running(appid: int) -> bool:
    """A Steam mantém HKCU\\Software\\Valve\\Steam\\Apps\\<id>\\Running = 1 enquanto o jogo roda."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Valve\Steam\Apps\{appid}") as k:
            return winreg.QueryValueEx(k, "Running")[0] == 1
    except OSError:
        return False
