"""API exposta ao JavaScript da interface (pywebview).

Cada método público aqui vira `pywebview.api.<nome>(...)` no lado do JS. O
próprio pywebview roda cada chamada numa thread separada, então nada aqui
precisa se preocupar em travar a janela. Erros esperados (SwapError e afins)
viram `{"ok": False, "error": "..."}` em vez de exceção, pra não depender de
como cada versão do pywebview marshala exceções pro JS.

Importante: todo estado interno é guardado com nome começando em "_"
(_window, _steam, _games...). O pywebview monta a lista de métodos
expostos ao JS varrendo com dir() os atributos públicos do objeto e, para
qualquer um que não seja um método, desce recursivamente dentro dele à
procura de mais métodos (steamswap/webapi.py:get_functions em
webview/util.py). Um atributo público guardando a própria Window (ou
qualquer objeto com referência a um controle nativo do Windows) faz essa
varredura cair em propriedades .NET tipo Bounds/Empty que devolvem um
objeto novo a cada acesso — e como a trava de recursão do pywebview é por
id() do objeto, isso nunca detecta o ciclo e estoura a pilha. Atributos
com "_" na frente são ignorados por essa varredura, então usamos "_" em
tudo que não deve virar API.
"""
import json
import threading
from typing import List

import webview

from . import compat, paths, shortcuts, swap
from .steam import find_steam_path, installed_games, find_executables
from .stub import Target, build_selector

COMPAT_PACE_SECONDS = 0.3  # intervalo entre consultas à Steam Store, para não estourar o limite de requisições


def _compat_label(c) -> str:
    if c is None:
        return "checking"
    if c.error:
        return "error"
    if c.full_controller and c.remote_play_together:
        return "full"
    if c.full_controller:
        return "controller_only"
    if c.remote_play_together:
        return "rpt_only"
    return "none"


def _compat_ok(c) -> bool:
    return c is not None and not c.error and c.full_controller and c.remote_play_together


def _target_summary(tid: str, t: dict, default_id: str) -> dict:
    detail = t["path"] if t["kind"] == "exe" else f"jogo Steam {t['steam_appid']}"
    return {
        "id": tid, "name": t["name"], "kind": t["kind"], "detail": detail,
        "isDefault": tid == default_id, "hasShortcut": bool(t.get("shortcut")),
    }


class Api:
    def __init__(self):
        self._window = None
        self._steam = find_steam_path()
        self._games = installed_games(self._steam) if self._steam else []
        self._by_id = {g.appid: g for g in self._games}
        self._compat_cache = compat.load_cache()
        self._compat_thread = None
        self._stop_compat = False

    def bind(self, window):
        self._window = window
        window.events.closing += lambda: setattr(self, "_stop_compat", True)

    # ---------- estado geral ----------
    def status(self) -> dict:
        return {"steamPath": str(self._steam) if self._steam else None, "gameCount": len(self._games)}

    def list_games(self) -> List[dict]:
        records = swap.load_records()
        out = []
        for g in self._games:
            c = self._compat_cache.get(g.appid)
            out.append({
                "appid": g.appid, "name": g.name,
                "compat": _compat_label(c), "compatOk": _compat_ok(c),
                "swapped": g.appid in records,
            })
        return out

    def steam_game_options(self) -> List[dict]:
        return [{"appid": g.appid, "name": g.name} for g in self._games]

    def game_detail(self, appid) -> dict:
        appid = int(appid)
        game = self._by_id.get(appid)
        if game is None:
            return {"error": "Jogo não encontrado."}
        rec = swap.load_records().get(appid)
        if rec:
            targets = [_target_summary(tid, t, rec.default_id) for tid, t in rec.targets.items()]
            return {
                "appid": appid, "name": game.name, "gamePath": str(game.path),
                "swapped": True, "hostExe": rec.host_exe, "hostExeOptions": [rec.host_exe],
                "backupDir": rec.backup_dir, "targets": targets,
            }
        exes = find_executables(game.path)
        return {
            "appid": appid, "name": game.name, "gamePath": str(game.path),
            "swapped": False, "hostExe": exes[0] if exes else "", "hostExeOptions": exes,
            "backupDir": None, "targets": [],
        }

    # ---------- diálogo nativo ----------
    def browse_exe(self):
        if self._window is None:
            return None
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=('Executáveis e atalhos (*.exe;*.lnk;*.bat;*.cmd)', 'Todos os arquivos (*.*)'))
        return result[0] if result else None

    # ---------- destinos ----------
    def add_target(self, appid, name: str, kind: str, path: str, args: str,
                   follow_folder: bool, steam_appid, host_exe: str, make_shortcut: bool) -> dict:
        appid = int(appid)
        game = self._by_id.get(appid)
        if game is None:
            return {"ok": False, "error": "Jogo não encontrado."}
        target = Target(kind=kind, path=(path or "").strip().strip('"'), args=(args or "").strip(),
                        follow_folder=bool(follow_folder), steam_appid=int(steam_appid or 0))
        try:
            if appid in swap.load_records():
                target = swap.add_target(appid, name, target)
            else:
                if not host_exe:
                    return {"ok": False, "error": "Escolha o executável que a Steam abre."}
                rec = swap.apply_swap(game, host_exe, name, target)
                target = Target.from_dict(rec.targets[rec.default_id])
        except swap.SwapError as e:
            return {"ok": False, "error": str(e)}

        warning = None
        if make_shortcut:
            try:
                self._create_shortcut_for(appid, game.name, target)
            except (RuntimeError, OSError) as e:
                warning = f"O destino foi adicionado, mas o atalho não pôde ser criado: {e}"
        return {"ok": True, "warning": warning}

    def _create_shortcut_for(self, appid: int, host_name: str, target: Target):
        selector_exe = paths.selectors_dir() / f"{appid}_{target.id}.exe"
        build_selector(appid, target.id, paths.marker_file(appid), selector_exe)
        link_name = f"{shortcuts.safe_filename(host_name)} - {shortcuts.safe_filename(target.name)}.lnk"
        link_path = shortcuts.desktop_dir() / link_name
        shortcuts.create_shortcut(link_path, selector_exe, description=f"SteamSwap: {host_name} -> {target.name}")
        swap.set_target_shortcut(appid, target.id, str(link_path))

    def remove_target(self, appid, target_id: str) -> dict:
        try:
            swap.remove_target(int(appid), target_id)
        except swap.SwapError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def set_default_target(self, appid, target_id: str) -> dict:
        try:
            swap.set_default_target(int(appid), target_id)
        except swap.SwapError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def recreate_shortcut(self, appid, target_id: str) -> dict:
        appid = int(appid)
        rec = swap.load_records().get(appid)
        if not rec or target_id not in rec.targets:
            return {"ok": False, "error": "Esse destino não existe mais."}
        target = Target.from_dict(rec.targets[target_id])
        try:
            self._create_shortcut_for(appid, rec.name, target)
        except (RuntimeError, OSError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def restore(self, appid, force: bool = False) -> dict:
        appid = int(appid)
        try:
            swap.restore_swap(appid, force=bool(force))
        except swap.ExtraFilesError as e:
            return {"ok": False, "needsForce": True, "extraFiles": e.files}
        except swap.SwapError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    # ---------- compatibilidade (Steam Store: controle total + Remote Play Together) ----------
    def start_compat_scan(self, force: bool = False) -> dict:
        if self._compat_thread is not None and self._compat_thread.is_alive():
            return {"ok": True, "alreadyRunning": True}
        appids = [g.appid for g in self._games]
        todo = [a for a in appids if force or a not in self._compat_cache or self._compat_cache[a].stale]
        if not todo:
            self._push("onCompatDone", {"statusText": "Compatibilidade verificada (Steam Store)."})
            return {"ok": True, "todo": 0}
        self._stop_compat = False
        total = len(todo)
        self._push("onCompatProgress", {"statusText": f"Verificando compatibilidade na Steam Store… 0/{total}"})

        def on_result(appid, result, i, n):
            self._compat_cache[appid] = result
            try:
                compat.save_cache(self._compat_cache)
            except OSError:
                pass  # não é crítico: só significa consultar de novo na próxima abertura
            status = ("Compatibilidade verificada (Steam Store)." if i >= n
                     else f"Verificando compatibilidade na Steam Store… {i}/{n}")
            self._push("onCompatResult", {
                "appid": appid, "compat": _compat_label(result), "compatOk": _compat_ok(result),
                "statusText": status,
            })

        def worker():
            compat.refresh_many(todo, self._compat_cache, force=force, pace=COMPAT_PACE_SECONDS,
                                on_result=on_result, should_stop=lambda: self._stop_compat)

        self._compat_thread = threading.Thread(target=worker, daemon=True)
        self._compat_thread.start()
        return {"ok": True, "todo": total}

    def _push(self, js_fn: str, payload: dict):
        """Notifica a página de algo que aconteceu em segundo plano."""
        if self._window is None:
            return
        try:
            self._window.evaluate_js(f"window.{js_fn} && window.{js_fn}({json.dumps(payload)})")
        except Exception:
            pass  # a janela pode já ter sido fechada nesse meio-tempo
