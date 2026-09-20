"""Compatibilidade de controle e Remote Play Together, via Steam Store API.

Os arquivos locais da Steam (appmanifest, libraryfolders) não trazem essa
informação — só a loja sabe. Os resultados são cacheados em disco porque
isso quase nunca muda para um jogo já lançado e a API tem limite de uso.

IDs de categoria confirmados contra a API em 2026-09-20 (Portal 2 e
Team Fortress 2 têm as duas: id 28 = Full controller support,
id 44 = Remote Play Together).
"""
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional

from .paths import data_dir

CATEGORY_FULL_CONTROLLER = 28
CATEGORY_REMOTE_PLAY_TOGETHER = 44

CACHE_TTL_DAYS = 30
_API_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&filters=categories&cc=us&l=english"


@dataclass
class Compat:
    categories: FrozenSet[int]
    checked: float  # time.time() da última consulta
    error: bool = False  # falha ao consultar (rede, 429...); diferente de "não tem essas categorias"

    @property
    def full_controller(self) -> bool:
        return CATEGORY_FULL_CONTROLLER in self.categories

    @property
    def remote_play_together(self) -> bool:
        return CATEGORY_REMOTE_PLAY_TOGETHER in self.categories

    @property
    def stale(self) -> bool:
        return time.time() - self.checked > CACHE_TTL_DAYS * 86400

    def to_json(self) -> dict:
        return {"categories": sorted(self.categories), "checked": self.checked, "error": self.error}

    @staticmethod
    def from_json(d: dict) -> "Compat":
        return Compat(frozenset(d.get("categories", [])), float(d.get("checked", 0)), bool(d.get("error", False)))


def cache_file() -> Path:
    return data_dir() / "compat_cache.json"


def load_cache() -> Dict[int, Compat]:
    f = cache_file()
    if not f.is_file():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return {int(k): Compat.from_json(v) for k, v in raw.items()}
    except (ValueError, TypeError, OSError):
        return {}


def save_cache(cache: Dict[int, Compat]):
    f = cache_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({str(k): v.to_json() for k, v in cache.items()}, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def _fetch_raw(appid: int, timeout: float = 8.0) -> dict:
    """Requisição HTTP crua, isolada à parte para os testes substituírem."""
    req = urllib.request.Request(_API_URL.format(appid=appid), headers={"User-Agent": "SteamSwap/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_categories(appid: int, retries: int = 2) -> Compat:
    """Consulta a Steam Store. Em erro (rede, 429, resposta inesperada) devolve Compat(error=True)."""
    delay = 1.5
    for attempt in range(retries + 1):
        try:
            data = _fetch_raw(appid)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            return Compat(frozenset(), time.time(), error=True)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return Compat(frozenset(), time.time(), error=True)

        try:
            entry = data[str(appid)]
            if not entry.get("success"):
                return Compat(frozenset(), time.time())  # sem página na loja (região, removido...): trata como incompatível
            cats = {int(c["id"]) for c in entry["data"].get("categories", [])}
        except (KeyError, TypeError, ValueError):
            return Compat(frozenset(), time.time(), error=True)
        return Compat(frozenset(cats), time.time())
    return Compat(frozenset(), time.time(), error=True)


def refresh_many(appids: List[int], cache: Dict[int, Compat], force: bool = False, pace: float = 0.3,
                  on_result: Optional[Callable[[int, Compat, int, int], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None) -> Dict[int, Compat]:
    """Consulta os appids que faltam ou estão vencidos no cache (mutado em memória).

    on_result(appid, compat, feitos, total) é chamado após cada consulta.
    should_stop() é checado entre uma consulta e outra, para permitir cancelar.
    """
    todo = [a for a in appids if force or a not in cache or cache[a].stale]
    for i, appid in enumerate(todo, 1):
        if should_stop and should_stop():
            break
        cache[appid] = fetch_categories(appid)
        if on_result:
            on_result(appid, cache[appid], i, len(todo))
        if i < len(todo):
            time.sleep(pace)
    return cache
