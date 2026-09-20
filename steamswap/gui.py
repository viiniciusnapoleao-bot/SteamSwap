"""Interface Tkinter do SteamSwap."""
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import compat, paths, shortcuts, swap
from .steam import find_steam_path, installed_games, find_executables
from .stub import Target, build_selector

COMPAT_PACE_SECONDS = 0.3  # intervalo entre consultas à Steam Store, para não estourar o limite de requisições


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SteamSwap")
        self.geometry("980x660")
        self.minsize(860, 560)

        self.steam = find_steam_path()
        self.games = installed_games(self.steam) if self.steam else []
        self.by_id = {g.appid: g for g in self.games}
        self.records = swap.load_records()
        self.compat_cache = compat.load_cache()
        self._stop_compat = False
        self._closed = False
        self._compat_thread = None
        self._compat_queue: "queue.Queue" = queue.Queue()

        self.search = tk.StringVar()
        self.host_exe = tk.StringVar()
        self.target_name = tk.StringVar()
        self.kind = tk.StringVar(value="exe")
        self.exe_path = tk.StringVar()
        self.steam_target = tk.StringVar()
        self.args = tk.StringVar()
        self.follow = tk.BooleanVar(value=True)
        self.make_shortcut = tk.BooleanVar(value=True)
        self.filter_compat = tk.BooleanVar(value=True)
        self.compat_status = tk.StringVar(value="")
        self.status = tk.StringVar(
            value="Steam não encontrada." if not self.steam else f"Steam: {self.steam}  ·  {len(self.games)} jogos")

        self._build()
        self._refresh_list()
        self.search.trace_add("write", lambda *_: self._refresh_list())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_compat_scan()
        self._poll_compat_queue()

    # ---------- layout ----------
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True, padx=(10, 5), pady=10)
        ttk.Label(left, text="1. Jogo hospedeiro (o que a Steam vai abrir)").pack(anchor="w")
        ttk.Entry(left, textvariable=self.search).pack(fill="x", pady=4)

        filt_row = ttk.Frame(left)
        filt_row.pack(fill="x", pady=(0, 4))
        ttk.Checkbutton(filt_row, text="Só controle total + Remote Play Together", variable=self.filter_compat,
                        command=self._refresh_list).pack(side="left")
        ttk.Button(filt_row, text="Verificar de novo",
                   command=lambda: self._start_compat_scan(force=True)).pack(side="right")
        ttk.Label(left, textvariable=self.compat_status, foreground="#666").pack(anchor="w")

        self.tree = ttk.Treeview(left, columns=("name", "appid", "compat", "state"), show="headings",
                                 selectmode="browse")
        for col, text, w in (("name", "Jogo", 220), ("appid", "AppID", 60), ("compat", "Controle/RPT", 110),
                            ("state", "Estado", 90)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        right = ttk.Frame(self)
        right.pack(side="right", fill="both", expand=False, padx=(5, 10), pady=10)

        ttk.Label(right, text="Executável que a Steam abre neste jogo").pack(anchor="w")
        self.exe_combo = ttk.Combobox(right, textvariable=self.host_exe, width=52, state="readonly")
        self.exe_combo.pack(fill="x", **pad)

        ttk.Separator(right).pack(fill="x", pady=8)
        ttk.Label(right, text="2. Destinos deste hospedeiro").pack(anchor="w")
        self.targets_tree = ttk.Treeview(right, columns=("name", "detail", "default", "shortcut"),
                                         show="headings", selectmode="browse", height=5)
        for col, text, w in (("name", "Nome", 110), ("detail", "Abre", 170), ("default", "Padrão", 55),
                            ("shortcut", "Atalho", 55)):
            self.targets_tree.heading(col, text=text)
            self.targets_tree.column(col, width=w, anchor="w")
        self.targets_tree.pack(fill="x", pady=4)
        self.targets_tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_target_buttons())

        tbtns = ttk.Frame(right)
        tbtns.pack(fill="x")
        self.btn_default = ttk.Button(tbtns, text="Definir padrão", command=self._set_default_target)
        self.btn_default.pack(side="left")
        self.btn_remove_target = ttk.Button(tbtns, text="Remover", command=self._remove_target)
        self.btn_remove_target.pack(side="left", padx=6)
        self.btn_reshortcut = ttk.Button(tbtns, text="Recriar atalho", command=self._recreate_shortcut)
        self.btn_reshortcut.pack(side="left")

        ttk.Separator(right).pack(fill="x", pady=8)
        ttk.Label(right, text="Adicionar destino").pack(anchor="w")
        name_row = ttk.Frame(right)
        name_row.pack(fill="x", **pad)
        ttk.Label(name_row, text="Nome:").pack(side="left")
        ttk.Entry(name_row, textvariable=self.target_name, width=40).pack(side="left", padx=6, fill="x", expand=True)

        row = ttk.Frame(right)
        row.pack(fill="x", **pad)
        ttk.Radiobutton(row, text="Programa / atalho", variable=self.kind, value="exe",
                        command=self._sync_kind).pack(side="left")
        ttk.Radiobutton(row, text="Outro jogo da Steam", variable=self.kind, value="steam",
                        command=self._sync_kind).pack(side="left", padx=12)

        self.exe_row = ttk.Frame(right)
        ttk.Entry(self.exe_row, textvariable=self.exe_path, width=44).pack(side="left", fill="x", expand=True)
        ttk.Button(self.exe_row, text="Procurar…", command=self._browse).pack(side="left", padx=(6, 0))

        self.steam_row = ttk.Frame(right)
        self.steam_combo = ttk.Combobox(self.steam_row, textvariable=self.steam_target, width=52)
        self.steam_combo.pack(fill="x")
        ttk.Label(self.steam_row, text="Escolha da lista ou digite um AppID.",
                  foreground="#666").pack(anchor="w")

        self.args_row = ttk.Frame(right)
        ttk.Label(self.args_row, text="Argumentos:").pack(side="left")
        ttk.Entry(self.args_row, textvariable=self.args, width=40).pack(side="left", padx=6)
        self.follow_chk = ttk.Checkbutton(
            right, text="Continuar rodando enquanto houver processos da pasta do destino\n"
                        "(necessário se o destino abre um launcher e fecha)",
            variable=self.follow)
        self.shortcut_chk = ttk.Checkbutton(
            right, text="Criar atalho na Área de Trabalho para este destino", variable=self.make_shortcut)

        self.btn_add_target = ttk.Button(right, text="+ Adicionar destino", command=self._add_target)
        self.btn_add_target.pack(side="bottom", pady=(4, 0))
        self.shortcut_chk.pack(side="bottom", anchor="w", padx=8)

        ttk.Separator(right).pack(fill="x", pady=8, side="bottom")
        self.btn_restore = ttk.Button(right, text="Restaurar original (remove todos os destinos)",
                                      command=self._restore)
        self.btn_restore.pack(side="bottom", pady=(4, 0))
        self.info = ttk.Label(right, text="", wraplength=430, justify="left", foreground="#444")
        self.info.pack(side="bottom", fill="x", pady=8)

        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w").pack(side="bottom", fill="x")
        self._sync_kind()
        self._sync_target_buttons()

    def _sync_kind(self):
        for w in (self.exe_row, self.steam_row, self.args_row, self.follow_chk):
            w.pack_forget()
        if self.kind.get() == "exe":
            self.exe_row.pack(fill="x", padx=8, pady=4)
            self.args_row.pack(fill="x", padx=8, pady=4)
            self.follow_chk.pack(anchor="w", padx=8, pady=4)
        else:
            self.steam_row.pack(fill="x", padx=8, pady=4)

    def _sync_target_buttons(self):
        has_sel = bool(self.targets_tree.selection())
        appid = self._selected_id()
        rec = self.records.get(appid) if appid is not None else None
        can_remove = has_sel and rec is not None and len(rec.targets) > 1
        for btn, ok in ((self.btn_default, has_sel), (self.btn_remove_target, can_remove),
                        (self.btn_reshortcut, has_sel)):
            btn.state(["!disabled"] if ok else ["disabled"])

    # ---------- compatibilidade (Steam Store: controle total + Remote Play Together) ----------
    def _compat_label(self, appid: int) -> str:
        c = self.compat_cache.get(appid)
        if c is None:
            return "verificando…"
        if c.error:
            return "erro na consulta"
        if c.full_controller and c.remote_play_together:
            return "Controle + RPT"
        if c.full_controller:
            return "Só controle"
        if c.remote_play_together:
            return "Só RPT"
        return "Não"

    def _compat_ok(self, appid: int) -> bool:
        c = self.compat_cache.get(appid)
        return c is not None and not c.error and c.full_controller and c.remote_play_together

    def _start_compat_scan(self, force: bool = False):
        if self._compat_thread is not None and self._compat_thread.is_alive():
            return  # já tem uma varredura rodando; evita duas threads mexendo no mesmo cache
        appids = [g.appid for g in self.games]
        todo = [a for a in appids if force or a not in self.compat_cache or self.compat_cache[a].stale]
        if not todo:
            self.compat_status.set("Compatibilidade verificada (Steam Store).")
            return
        self._stop_compat = False
        total = len(todo)
        self.compat_status.set(f"Verificando compatibilidade na Steam Store… 0/{total}")

        # A thread só empilha resultados (thread-safe); nada aqui toca o Tk diretamente —
        # chamar self.after()/widgets fora da thread principal trava o Tkinter.
        def worker():
            compat.refresh_many(
                todo, self.compat_cache, force=force, pace=COMPAT_PACE_SECONDS,
                on_result=lambda appid, result, i, n: self._compat_queue.put((appid, result, i, n)),
                should_stop=lambda: self._stop_compat)

        self._compat_thread = threading.Thread(target=worker, daemon=True)
        self._compat_thread.start()

    def _poll_compat_queue(self):
        if self._closed:
            return
        while True:
            try:
                appid, result, i, total = self._compat_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_compat(appid, result, i, total)
        self.after(150, self._poll_compat_queue)

    def _apply_compat(self, appid, result, i, total):
        self.compat_cache[appid] = result
        try:
            compat.save_cache(self.compat_cache)
        except OSError:
            pass  # não é crítico: só significa consultar de novo na próxima abertura
        self.compat_status.set(
            "Compatibilidade verificada (Steam Store)." if i >= total
            else f"Verificando compatibilidade na Steam Store… {i}/{total}")
        self._refresh_list()
        if self._selected_id() == appid:
            self._on_select()

    def _on_close(self):
        self._stop_compat = True
        self._closed = True
        self.destroy()

    # ---------- dados ----------
    def _refresh_list(self):
        q = self.search.get().strip().lower()
        only_compat = self.filter_compat.get()
        sel = self._selected_id()
        self.tree.delete(*self.tree.get_children())
        for g in self.games:
            if q and q not in g.name.lower() and q not in str(g.appid):
                continue
            if only_compat and not self._compat_ok(g.appid):
                continue
            state = "TROCADO" if g.appid in self.records else ""
            self.tree.insert("", "end", iid=str(g.appid),
                             values=(g.name, g.appid, self._compat_label(g.appid), state))
        if sel is not None and self.tree.exists(str(sel)):
            self.tree.selection_set(str(sel))
        self.steam_combo["values"] = [f"{g.name} ({g.appid})" for g in self.games]

    def _selected_id(self):
        s = self.tree.selection()
        return int(s[0]) if s else None

    def _target_detail(self, t: dict) -> str:
        return t["path"] if t["kind"] == "exe" else f"jogo Steam {t['steam_appid']}"

    def _refresh_targets_tree(self, rec):
        self.targets_tree.delete(*self.targets_tree.get_children())
        if not rec:
            return
        for tid, t in rec.targets.items():
            self.targets_tree.insert(
                "", "end", iid=tid,
                values=(t["name"], self._target_detail(t), "sim" if tid == rec.default_id else "",
                       "sim" if t.get("shortcut") else ""))

    def _on_select(self):
        appid = self._selected_id()
        if appid is None:
            return
        game = self.by_id[appid]
        rec = self.records.get(appid)
        self._refresh_targets_tree(rec)
        n_existing = len(rec.targets) if rec else 0
        self.target_name.set(f"Destino {n_existing + 1}")
        if rec:
            self.exe_combo["values"] = [rec.host_exe]
            self.host_exe.set(rec.host_exe)
            self.info.config(text=f"{game.path}\nOriginal em: {rec.backup_dir}")
        else:
            exes = find_executables(game.path)
            self.exe_combo["values"] = exes
            self.host_exe.set(exes[0] if exes else "")
            self.info.config(text=f"{game.path}" + ("" if exes else "\nNenhum .exe encontrado."))
        self.btn_restore.state(["!disabled"] if rec else ["disabled"])
        self._sync_target_buttons()

    # ---------- ações ----------
    def _browse(self):
        p = filedialog.askopenfilename(
            title="Programa ou atalho de destino",
            filetypes=[("Executáveis e atalhos", "*.exe *.lnk *.bat *.cmd"), ("Todos", "*.*")])
        if p:
            self.exe_path.set(p.replace("/", "\\"))

    def _build_target(self) -> Target:
        if self.kind.get() == "exe":
            return Target(kind="exe", path=self.exe_path.get().strip().strip('"'),
                          args=self.args.get().strip(), follow_folder=self.follow.get())
        m = re.search(r"(\d+)\)?\s*$", self.steam_target.get())
        if not m:
            raise swap.SwapError("Informe o AppID do jogo de destino.")
        return Target(kind="steam", steam_appid=int(m.group(1)))

    def _create_shortcut_for(self, appid: int, host_name: str, target: Target):
        """Compila o seletor e cria o .lnk; falha aqui não desfaz o destino já adicionado."""
        selector_exe = paths.selectors_dir() / f"{appid}_{target.id}.exe"
        build_selector(appid, target.id, paths.marker_file(appid), selector_exe)
        link_name = f"{shortcuts.safe_filename(host_name)} - {shortcuts.safe_filename(target.name)}.lnk"
        link_path = shortcuts.desktop_dir() / link_name
        shortcuts.create_shortcut(link_path, selector_exe, description=f"SteamSwap: {host_name} → {target.name}")
        swap.set_target_shortcut(appid, target.id, str(link_path))

    def _add_target(self):
        appid = self._selected_id()
        if appid is None:
            return messagebox.showinfo("SteamSwap", "Selecione o jogo hospedeiro.")
        game = self.by_id[appid]
        already_swapped = appid in self.records
        if not already_swapped and not self.host_exe.get():
            return messagebox.showerror("SteamSwap", "Escolha o executável que a Steam abre.")
        try:
            target = self._build_target()
        except swap.SwapError as e:
            return messagebox.showerror("SteamSwap", str(e))
        name = self.target_name.get().strip() or "Destino"

        if not already_swapped:
            if not messagebox.askyesno(
                    "Confirmar troca",
                    f"A pasta de '{game.name}' será renomeada para '{game.path.name}{swap.BACKUP_SUFFIX}' "
                    f"e substituída por um launcher que abre o destino.\n\n"
                    f"Se a Steam atualizar ou verificar esse jogo, a troca é desfeita "
                    f"(desative a atualização automática dele nas propriedades).\n\nContinuar?"):
                return

        self.config(cursor="watch")
        self.update_idletasks()
        try:
            if already_swapped:
                target = swap.add_target(appid, name, target)
            else:
                rec = swap.apply_swap(game, self.host_exe.get(), name, target)
                target = Target.from_dict(rec.targets[rec.default_id])
        except swap.SwapError as e:
            self.config(cursor="")
            return messagebox.showerror("SteamSwap", str(e))
        self.config(cursor="")

        shortcut_warning = ""
        if self.make_shortcut.get():
            try:
                self._create_shortcut_for(appid, game.name, target)
            except (RuntimeError, OSError) as e:
                shortcut_warning = f"\n\nO destino foi adicionado, mas o atalho não pôde ser criado:\n{e}"

        self._after_change(f"'{name}' adicionado a '{game.name}'.{shortcut_warning}")
        if shortcut_warning:
            messagebox.showwarning("SteamSwap", shortcut_warning.strip())

    def _selected_target_id(self):
        s = self.targets_tree.selection()
        return s[0] if s else None

    def _set_default_target(self):
        appid = self._selected_id()
        tid = self._selected_target_id()
        if appid is None or tid is None:
            return
        try:
            swap.set_default_target(appid, tid)
        except swap.SwapError as e:
            return messagebox.showerror("SteamSwap", str(e))
        self._after_change("Destino padrão atualizado.")

    def _remove_target(self):
        appid = self._selected_id()
        tid = self._selected_target_id()
        if appid is None or tid is None:
            return
        rec = self.records.get(appid)
        name = rec.targets[tid]["name"] if rec and tid in rec.targets else tid
        if not messagebox.askyesno("SteamSwap", f"Remover o destino '{name}'? O atalho dele, se houver, também é apagado."):
            return
        try:
            swap.remove_target(appid, tid)
        except swap.SwapError as e:
            return messagebox.showerror("SteamSwap", str(e))
        self._after_change(f"Destino '{name}' removido.")

    def _recreate_shortcut(self):
        appid = self._selected_id()
        tid = self._selected_target_id()
        if appid is None or tid is None:
            return
        rec = self.records.get(appid)
        if not rec or tid not in rec.targets:
            return
        target = Target.from_dict(rec.targets[tid])
        try:
            self._create_shortcut_for(appid, rec.name, target)
        except (RuntimeError, OSError) as e:
            return messagebox.showerror("SteamSwap", f"Não consegui criar o atalho:\n{e}")
        self._after_change(f"Atalho de '{target.name}' recriado na Área de Trabalho.")

    def _restore(self):
        appid = self._selected_id()
        if appid is None or appid not in self.records:
            return
        name = self.by_id[appid].name
        force = False
        while True:
            try:
                swap.restore_swap(appid, force=force)
                break
            except swap.ExtraFilesError as e:
                shown = "\n".join(e.files[:10]) + ("\n…" if len(e.files) > 10 else "")
                if not messagebox.askyesno(
                        "Arquivos inesperados",
                        f"A pasta do jogo tem {len(e.files)} arquivo(s) além do launcher falso "
                        f"(a Steam pode ter atualizado o jogo):\n\n{shown}\n\n"
                        f"Apagar tudo isso e restaurar o original?"):
                    return
                force = True
            except swap.SwapError as e:
                messagebox.showerror("SteamSwap", str(e))
                return
        self._after_change(f"'{name}' restaurado ao original.")

    def _after_change(self, msg: str):
        self.records = swap.load_records()
        self._refresh_list()
        self._on_select()
        self.status.set(msg)


def main():
    App().mainloop()
