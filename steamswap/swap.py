"""Aplicar e desfazer a troca de launcher de um jogo da Steam.

A troca é um rename: a pasta original do jogo vira "<pasta>.steamswap-bak"
(instantâneo, sem copiar gigabytes) e uma pasta nova contém só o launcher falso.
"""
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .steam import Game, is_app_running
from .stub import Target, build_stub

BACKUP_SUFFIX = ".steamswap-bak"


class SwapError(Exception):
    pass


class ExtraFilesError(SwapError):
    """A pasta do jogo tem arquivos além do launcher falso (ex.: a Steam atualizou o jogo)."""

    def __init__(self, files: List[str]):
        super().__init__(f"{len(files)} arquivo(s) além do launcher falso na pasta do jogo")
        self.files = files


@dataclass
class SwapRecord:
    appid: int
    name: str
    game_dir: str
    backup_dir: str
    host_exe: str
    target: dict
    created: str


def state_file() -> Path:
    base = os.environ.get("STEAMSWAP_HOME") or os.path.join(os.environ.get("APPDATA", str(Path.home())), "SteamSwap")
    return Path(base) / "swaps.json"


def load_records() -> Dict[int, SwapRecord]:
    f = state_file()
    if not f.is_file():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return {int(k): SwapRecord(**v) for k, v in raw.items()}
    except (ValueError, TypeError) as e:
        raise SwapError(f"Arquivo de estado corrompido ({f}): {e}")


def _save_records(records: Dict[int, SwapRecord]):
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({str(k): asdict(v) for k, v in records.items()}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, f)


def apply_swap(game: Game, host_exe: str, target: Target) -> SwapRecord:
    """host_exe: caminho relativo, dentro da pasta do jogo, do exe que a Steam abre."""
    records = load_records()
    game_dir = game.path
    backup = game_dir.with_name(game_dir.name + BACKUP_SUFFIX)

    if game.appid in records:
        raise SwapError("Este jogo já está trocado. Restaure antes de trocar de novo.")
    if backup.exists():
        raise SwapError(f"Já existe {backup}. Resolva isso manualmente antes de continuar.")
    if not game_dir.is_dir():
        raise SwapError(f"Pasta do jogo não encontrada: {game_dir}")
    if not (game_dir / host_exe).is_file():
        raise SwapError(f"Executável hospedeiro não encontrado: {host_exe}")
    if is_app_running(game.appid):
        raise SwapError("A Steam diz que este jogo está em execução. Feche-o primeiro.")
    if target.kind == "steam" and target.steam_appid == game.appid:
        raise SwapError("O destino não pode ser o próprio jogo hospedeiro.")
    try:
        target.validate()
    except ValueError as e:
        raise SwapError(str(e))

    # Compila antes de mexer em qualquer arquivo: se falhar, nada mudou.
    with tempfile.TemporaryDirectory(prefix="steamswap_stage_") as stage:
        stub = Path(stage) / Path(host_exe).name
        try:
            build_stub(target, stub)
        except RuntimeError as e:
            raise SwapError(str(e))

        try:
            game_dir.rename(backup)
        except OSError as e:
            raise SwapError(f"Não consegui renomear a pasta do jogo (algum programa está usando arquivos dela?): {e}")
        try:
            dest = game_dir / host_exe
            dest.parent.mkdir(parents=True)
            shutil.copy2(stub, dest)
        except OSError as e:
            shutil.rmtree(game_dir, ignore_errors=True)
            backup.rename(game_dir)
            raise SwapError(f"Falha ao instalar o launcher; a pasta original foi restaurada: {e}")

    record = SwapRecord(game.appid, game.name, str(game_dir), str(backup), host_exe,
                        target.to_dict(), datetime.now().isoformat(timespec="seconds"))
    records[game.appid] = record
    try:
        _save_records(records)
    except OSError as e:
        shutil.rmtree(game_dir, ignore_errors=True)
        backup.rename(game_dir)
        raise SwapError(f"Não consegui gravar o estado; a troca foi desfeita: {e}")
    return record


def _extra_files(game_dir: Path, host_exe: str) -> List[str]:
    keep = (game_dir / host_exe).resolve()
    extras = []
    for dirpath, _, filenames in os.walk(game_dir):
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.resolve() != keep:
                extras.append(str(p.relative_to(game_dir)))
    return extras


def restore_swap(appid: int, force: bool = False):
    """Devolve a pasta original. Sem force, recusa apagar arquivos que não são nossos."""
    records = load_records()
    rec = records.get(appid)
    if rec is None:
        raise SwapError("Este jogo não está trocado.")
    game_dir, backup = Path(rec.game_dir), Path(rec.backup_dir)
    if not backup.is_dir():
        raise SwapError(f"A pasta de backup sumiu: {backup}")
    if is_app_running(appid):
        raise SwapError("A Steam diz que este jogo está em execução. Feche-o primeiro.")

    if game_dir.exists():
        extras = _extra_files(game_dir, rec.host_exe)
        if extras and not force:
            raise ExtraFilesError(extras)
        try:
            shutil.rmtree(game_dir)
        except OSError as e:
            raise SwapError(f"Não consegui remover a pasta com o launcher falso: {e}")
    try:
        backup.rename(game_dir)
    except OSError as e:
        raise SwapError(f"Não consegui devolver a pasta original: {e}")
    del records[appid]
    _save_records(records)
