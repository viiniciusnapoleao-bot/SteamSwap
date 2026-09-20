"""Aplicar e desfazer a troca de launcher de um jogo da Steam, com um ou mais destinos.

A troca em si é um rename: a pasta original do jogo vira "<pasta>.steamswap-bak"
(instantâneo, sem copiar gigabytes) e uma pasta nova contém só o launcher falso.
Adicionar/remover/trocar o destino padrão depois disso só recompila e substitui
esse launcher — a pasta do jogo não é mexida de novo.
"""
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from . import paths
from .steam import Game, is_app_running
from .stub import Target, build_stub, prepared

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
    targets: Dict[str, dict]   # id -> Target.to_dict()
    default_id: str
    created: str


def state_file() -> Path:
    return paths.data_dir() / "swaps.json"


def _migrate_record(v: dict) -> dict:
    """Registros de antes de existir múltiplos destinos tinham um "target" só."""
    if "targets" in v:
        return v
    v = dict(v)
    old_target = dict(v.pop("target", {}))
    old_target.setdefault("id", "principal")
    old_target.setdefault("name", "Destino")
    old_target.setdefault("shortcut", "")
    v["targets"] = {old_target["id"]: old_target}
    v["default_id"] = old_target["id"]
    return v


def load_records() -> Dict[int, SwapRecord]:
    f = state_file()
    if not f.is_file():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return {int(k): SwapRecord(**_migrate_record(v)) for k, v in raw.items()}
    except (ValueError, TypeError) as e:
        raise SwapError(f"Arquivo de estado corrompido ({f}): {e}")


def _save_records(records: Dict[int, SwapRecord]):
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({str(k): asdict(v) for k, v in records.items()}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, f)


def apply_swap(game: Game, host_exe: str, name: str, target: Target) -> SwapRecord:
    """host_exe: caminho relativo, dentro da pasta do jogo, do exe que a Steam abre."""
    records = load_records()
    game_dir = game.path
    backup = game_dir.with_name(game_dir.name + BACKUP_SUFFIX)

    if game.appid in records:
        raise SwapError("Este jogo já está trocado. Use \"Adicionar destino\" para incluir outro programa.")
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
    target = prepared(target, name)
    try:
        target.validate()
    except ValueError as e:
        raise SwapError(str(e))

    marker = paths.marker_file(game.appid)
    # Compila antes de mexer em qualquer arquivo: se falhar, nada mudou.
    with tempfile.TemporaryDirectory(prefix="steamswap_stage_") as stage:
        stub = Path(stage) / Path(host_exe).name
        try:
            build_stub([target], target.id, marker, stub)
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
                        {target.id: target.to_dict()}, target.id,
                        datetime.now().isoformat(timespec="seconds"))
    records[game.appid] = record
    try:
        _save_records(records)
    except OSError as e:
        shutil.rmtree(game_dir, ignore_errors=True)
        backup.rename(game_dir)
        raise SwapError(f"Não consegui gravar o estado; a troca foi desfeita: {e}")
    return record


def _rebuild_stub(appid: int, rec: SwapRecord, targets: Dict[str, dict], default_id: str):
    """Recompila o launcher com o conjunto de destinos dado e o substitui no lugar."""
    if is_app_running(appid):
        raise SwapError("A Steam diz que este jogo está em execução. Feche-o primeiro.")
    exe_path = Path(rec.game_dir) / rec.host_exe
    if not exe_path.is_file():
        raise SwapError(f"O launcher não foi encontrado em {exe_path}; a troca pode estar corrompida.")
    target_objs = [Target.from_dict(v) for v in targets.values()]
    marker = paths.marker_file(appid)
    with tempfile.TemporaryDirectory(prefix="steamswap_stage_") as stage:
        stub = Path(stage) / exe_path.name
        try:
            build_stub(target_objs, default_id, marker, stub)
        except RuntimeError as e:
            raise SwapError(str(e))
        try:
            shutil.copy2(stub, exe_path)
        except OSError as e:
            raise SwapError(f"Não consegui atualizar o launcher (algum programa está usando o arquivo?): {e}")


def add_target(appid: int, name: str, target: Target) -> Target:
    """Adiciona mais um destino a um jogo já trocado, sem mexer na pasta do jogo."""
    records = load_records()
    rec = records.get(appid)
    if rec is None:
        raise SwapError("Este jogo ainda não foi trocado.")
    if target.kind == "steam" and target.steam_appid == appid:
        raise SwapError("O destino não pode ser o próprio jogo hospedeiro.")
    target = prepared(target, name)
    try:
        target.validate()
    except ValueError as e:
        raise SwapError(str(e))

    new_targets = dict(rec.targets)
    new_targets[target.id] = target.to_dict()
    _rebuild_stub(appid, rec, new_targets, rec.default_id)
    rec.targets = new_targets
    _save_records(records)
    return target


def remove_target(appid: int, target_id: str):
    records = load_records()
    rec = records.get(appid)
    if rec is None:
        raise SwapError("Este jogo não está trocado.")
    if target_id not in rec.targets:
        raise SwapError("Esse destino não existe mais.")
    if len(rec.targets) <= 1:
        raise SwapError("Não dá para remover o único destino; restaure o jogo em vez disso.")

    new_targets = dict(rec.targets)
    removed = new_targets.pop(target_id)
    new_default = rec.default_id if rec.default_id != target_id else next(iter(new_targets))
    _rebuild_stub(appid, rec, new_targets, new_default)
    rec.targets = new_targets
    rec.default_id = new_default
    _save_records(records)

    shortcut = removed.get("shortcut") or ""
    if shortcut:
        try:
            Path(shortcut).unlink(missing_ok=True)
        except OSError:
            pass  # atalho já removido/movido: não é crítico


def set_default_target(appid: int, target_id: str):
    records = load_records()
    rec = records.get(appid)
    if rec is None:
        raise SwapError("Este jogo não está trocado.")
    if target_id not in rec.targets:
        raise SwapError("Esse destino não existe mais.")
    if rec.default_id == target_id:
        return
    _rebuild_stub(appid, rec, rec.targets, target_id)
    rec.default_id = target_id
    _save_records(records)


def set_target_shortcut(appid: int, target_id: str, shortcut_path: str):
    """Só bookkeeping (o caminho do atalho não faz parte do launcher compilado)."""
    records = load_records()
    rec = records.get(appid)
    if rec is None or target_id not in rec.targets:
        return
    rec.targets[target_id]["shortcut"] = shortcut_path
    _save_records(records)


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

    for t in rec.targets.values():
        shortcut = t.get("shortcut") or ""
        if shortcut:
            try:
                Path(shortcut).unlink(missing_ok=True)
            except OSError:
                pass
    try:
        paths.marker_file(appid).unlink(missing_ok=True)
    except OSError:
        pass

    del records[appid]
    _save_records(records)
