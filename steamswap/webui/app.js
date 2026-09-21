"use strict";

const state = {
  games: [],
  steamOptions: [],
  filterText: "",
  filterCompat: true,
  selectedAppid: null,
  detail: null,
};

function api(name, ...args) {
  return window.pywebview.api[name](...args);
}

// ---------- compat: rótulo/classe a partir do código vindo do Python ----------
const COMPAT_TEXT = {
  checking: "verificando…", error: "erro na consulta", full: "Controle + RPT",
  controller_only: "Só controle", rpt_only: "Só RPT", none: "Não",
};
const COMPAT_CLASS = {
  checking: "pill-muted", error: "pill-red", full: "pill-green",
  controller_only: "pill-amber", rpt_only: "pill-amber", none: "pill-muted",
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------- toast ----------
let toastTimer = null;
function toast(message, isError) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.toggle("error", !!isError);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 5000);
}

// ---------- modal genérico ----------
function openModal(title, bodyHtml, buttons) {
  const backdrop = document.getElementById("modalBackdrop");
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalBody").innerHTML = bodyHtml;
  const actions = document.getElementById("modalActions");
  actions.innerHTML = "";
  return new Promise((resolve) => {
    function close(value) {
      backdrop.classList.add("hidden");
      backdrop.removeEventListener("click", onBackdropClick);
      resolve(value);
    }
    function onBackdropClick(e) {
      if (e.target === backdrop) close(null);
    }
    for (const b of buttons) {
      const btn = document.createElement("button");
      btn.className = `btn ${b.variant === "primary" ? "btn-primary" : b.variant === "danger" ? "btn-outline-danger" : "btn-ghost"}`;
      btn.textContent = b.label;
      btn.addEventListener("click", () => close(b.value));
      actions.appendChild(btn);
    }
    backdrop.addEventListener("click", onBackdropClick);
    backdrop.classList.remove("hidden");
  });
}

function confirmModal(title, message, confirmLabel, danger) {
  return openModal(title, escapeHtml(message).replace(/\n/g, "<br>"), [
    { label: "Cancelar", value: false, variant: "ghost" },
    { label: confirmLabel || "Confirmar", value: true, variant: danger ? "danger" : "primary" },
  ]);
}

// ---------- lista de jogos ----------
function matchesFilter(g) {
  if (state.filterCompat && !g.compatOk) return false;
  if (state.filterText) {
    const q = state.filterText.toLowerCase();
    if (!g.name.toLowerCase().includes(q) && !String(g.appid).includes(q)) return false;
  }
  return true;
}

function renderGameList() {
  const list = document.getElementById("gameList");
  const visible = state.games.filter(matchesFilter);
  if (visible.length === 0) {
    list.innerHTML = `<div class="empty-list-msg">Nenhum jogo encontrado com esse filtro.</div>`;
    return;
  }
  list.innerHTML = visible.map((g) => `
    <div class="game-row ${g.appid === state.selectedAppid ? "selected" : ""}" data-appid="${g.appid}">
      <div class="game-main">
        <div class="game-name">${escapeHtml(g.name)} ${g.swapped ? '<span class="pill pill-blue">trocado</span>' : ""}</div>
        <div class="game-meta">AppID ${g.appid}</div>
      </div>
      <span class="pill ${COMPAT_CLASS[g.compat]}">${COMPAT_TEXT[g.compat]}</span>
    </div>`).join("");
  list.querySelectorAll(".game-row").forEach((row) => {
    row.addEventListener("click", () => selectGame(Number(row.dataset.appid)));
  });
}

async function refreshGames() {
  state.games = await api("list_games");
  renderGameList();
}

// ---------- painel de detalhe ----------
function targetRowHtml(t) {
  return `
    <div class="target-row" data-id="${t.id}">
      <div>
        <div class="target-name">${escapeHtml(t.name)}
          ${t.isDefault ? '<span class="pill pill-blue">padrão</span>' : ""}
          ${t.hasShortcut ? '<span class="pill pill-muted">atalho</span>' : ""}
        </div>
        <div class="muted small">${escapeHtml(t.detail)}</div>
      </div>
      <div class="target-actions">
        <button class="btn btn-ghost btn-tiny" data-action="default" ${t.isDefault ? "disabled" : ""}>Padrão</button>
        <button class="btn btn-ghost btn-tiny" data-action="shortcut">Atalho</button>
        <button class="btn btn-ghost btn-tiny danger" data-action="remove" ${state.detail.targets.length <= 1 ? "disabled" : ""}>Remover</button>
      </div>
    </div>`;
}

function renderDetail() {
  const d = state.detail;
  document.getElementById("emptyState").classList.toggle("hidden", !!d);
  const content = document.getElementById("detailContent");
  if (!d) { content.innerHTML = ""; return; }

  const nextName = `Destino ${d.targets.length + 1}`;
  const hostExeField = d.swapped
    ? `<div class="static-value">${escapeHtml(d.hostExe)}</div>`
    : `<select id="hostExeSelect">${d.hostExeOptions.map((o) =>
        `<option value="${escapeHtml(o)}" ${o === d.hostExe ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")
      || '<option value="">(nenhum .exe encontrado)</option>'}</select>`;

  const targetsHtml = d.targets.length
    ? d.targets.map(targetRowHtml).join("")
    : '<div class="muted small">Nenhum destino ainda — adicione o primeiro abaixo.</div>';

  content.innerHTML = `
    <div class="detail-header">
      <h2>${escapeHtml(d.name)}</h2>
      <span class="muted">AppID ${d.appid}</span>
    </div>

    <div class="section">
      <div class="section-head">Executável que a Steam abre</div>
      ${hostExeField}
    </div>

    <div class="section">
      <div class="section-head">Destinos deste hospedeiro</div>
      <div class="targets-list">${targetsHtml}</div>
    </div>

    <div class="section">
      <div class="section-head">Adicionar destino</div>
      <div class="field-row">
        <label>Nome</label>
        <input type="text" id="targetName" value="${escapeHtml(nextName)}">
      </div>
      <div class="kind-toggle">
        <label><input type="radio" name="kind" value="exe" checked> Programa / atalho</label>
        <label><input type="radio" name="kind" value="steam"> Outro jogo da Steam</label>
      </div>
      <div id="exeFields">
        <div class="field-row path-row">
          <input type="text" id="exePath" placeholder="Caminho do programa ou atalho">
          <button class="btn btn-ghost btn-small" id="btnBrowse" type="button">Procurar…</button>
        </div>
        <div class="field-row">
          <label>Argumentos</label>
          <input type="text" id="exeArgs">
        </div>
        <label class="checkbox">
          <input type="checkbox" id="followFolder" checked>
          <span>Continuar rodando enquanto houver processos da pasta do destino (necessário se o destino abre um launcher e fecha)</span>
        </label>
      </div>
      <div id="steamFields" class="hidden field-row">
        <label>Jogo de destino</label>
        <input type="text" id="steamTarget" list="steamGamesList" placeholder="Nome do jogo ou AppID">
        <datalist id="steamGamesList">
          ${state.steamOptions.map((g) => `<option value="${escapeHtml(g.name)} (${g.appid})">`).join("")}
        </datalist>
      </div>
      <label class="checkbox">
        <input type="checkbox" id="makeShortcut" checked>
        <span>Criar atalho na Área de Trabalho para este destino</span>
      </label>
      <button class="btn btn-primary" id="btnAddTarget">+ Adicionar destino</button>
    </div>

    <div class="section">
      <button class="btn btn-outline-danger" id="btnRestore" ${d.swapped ? "" : "disabled"}>Restaurar original (remove todos os destinos)</button>
      <div class="muted small" style="margin-top:8px">
        ${escapeHtml(d.gamePath)}${d.backupDir ? `<br>Original em: ${escapeHtml(d.backupDir)}` : ""}
      </div>
    </div>`;

  wireDetailEvents();
}

function wireDetailEvents() {
  const kindRadios = document.querySelectorAll('input[name="kind"]');
  const exeFields = document.getElementById("exeFields");
  const steamFields = document.getElementById("steamFields");
  kindRadios.forEach((r) => r.addEventListener("change", () => {
    const isExe = document.querySelector('input[name="kind"]:checked').value === "exe";
    exeFields.classList.toggle("hidden", !isExe);
    steamFields.classList.toggle("hidden", isExe);
  }));

  document.getElementById("btnBrowse").addEventListener("click", async () => {
    const path = await api("browse_exe");
    if (path) document.getElementById("exePath").value = path;
  });

  document.getElementById("btnAddTarget").addEventListener("click", onAddTarget);
  document.getElementById("btnRestore").addEventListener("click", onRestore);

  document.querySelector(".targets-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    const row = e.target.closest(".target-row");
    const targetId = row.dataset.id;
    const name = row.querySelector(".target-name").textContent.trim();
    const appid = state.selectedAppid;
    if (btn.dataset.action === "default") {
      const r = await api("set_default_target", appid, targetId);
      handleResult(r, "Destino padrão atualizado.");
    } else if (btn.dataset.action === "shortcut") {
      const r = await api("recreate_shortcut", appid, targetId);
      handleResult(r, `Atalho de "${name}" recriado na Área de Trabalho.`);
    } else if (btn.dataset.action === "remove") {
      const ok = await confirmModal("Remover destino",
        `Remover o destino "${name}"? O atalho dele, se houver, também é apagado.`, "Remover", true);
      if (!ok) return;
      const r = await api("remove_target", appid, targetId);
      handleResult(r, `Destino "${name}" removido.`);
    }
  });
}

async function handleResult(r, successMessage) {
  if (!r.ok) { toast(r.error || "Não foi possível concluir.", true); return; }
  if (r.warning) toast(r.warning, true);
  await refreshGames();
  await selectGame(state.selectedAppid, true);
  setStatus(successMessage);
}

function parseSteamAppid(text) {
  const m = /(\d+)\)?\s*$/.exec((text || "").trim());
  return m ? parseInt(m[1], 10) : null;
}

async function onAddTarget() {
  const d = state.detail;
  const kind = document.querySelector('input[name="kind"]:checked').value;
  const name = document.getElementById("targetName").value.trim() || "Destino";
  const makeShortcut = document.getElementById("makeShortcut").checked;
  let path = "", args = "", followFolder = true, steamAppid = 0;

  if (kind === "exe") {
    path = document.getElementById("exePath").value.trim();
    if (!path) { toast("Informe o programa ou atalho de destino.", true); return; }
    args = document.getElementById("exeArgs").value.trim();
    followFolder = document.getElementById("followFolder").checked;
  } else {
    steamAppid = parseSteamAppid(document.getElementById("steamTarget").value);
    if (!steamAppid) { toast("Informe o AppID do jogo de destino.", true); return; }
  }

  const hostExe = d.swapped ? d.hostExe : (document.getElementById("hostExeSelect") || {}).value;
  if (!d.swapped && !hostExe) { toast("Escolha o executável que a Steam abre.", true); return; }

  if (!d.swapped) {
    const ok = await confirmModal(
      "Confirmar troca",
      `A pasta de "${d.name}" será renomeada e substituída por um launcher que abre o destino.\n\n` +
      `Se a Steam atualizar ou verificar esse jogo, a troca é desfeita (desative a atualização automática ` +
      `dele nas propriedades).\n\nContinuar?`, "Trocar");
    if (!ok) return;
  }

  const btn = document.getElementById("btnAddTarget");
  btn.disabled = true;
  try {
    const r = await api("add_target", d.appid, name, kind, path, args, followFolder, steamAppid, hostExe, makeShortcut);
    await handleResult(r, `"${name}" adicionado a "${d.name}".`);
  } finally {
    btn.disabled = false;
  }
}

async function onRestore() {
  const d = state.detail;
  let force = false;
  for (;;) {
    const r = await api("restore", d.appid, force);
    if (r.ok) { await handleResult(r, `"${d.name}" restaurado ao original.`); return; }
    if (r.needsForce) {
      const shown = r.extraFiles.slice(0, 10).join("\n") + (r.extraFiles.length > 10 ? "\n…" : "");
      const ok = await confirmModal(
        "Arquivos inesperados",
        `A pasta do jogo tem ${r.extraFiles.length} arquivo(s) além do launcher falso ` +
        `(a Steam pode ter atualizado o jogo):\n\n${shown}\n\nApagar tudo isso e restaurar o original?`,
        "Apagar e restaurar", true);
      if (!ok) return;
      force = true;
      continue;
    }
    toast(r.error || "Não foi possível restaurar.", true);
    return;
  }
}

// ---------- seleção ----------
async function selectGame(appid, keepScroll) {
  state.selectedAppid = appid;
  renderGameList();
  state.detail = await api("game_detail", appid);
  renderDetail();
}

// ---------- status ----------
function setStatus(text) {
  document.getElementById("statusBar").textContent = text;
}

// ---------- atualização ----------
let updateInfo = null;
let updateDismissed = false;

function showUpdateBanner(info) {
  updateInfo = info;
  if (updateDismissed) return;
  const banner = document.getElementById("updateBanner");
  document.getElementById("updateBannerText").textContent =
    `Nova versão disponível: v${info.version}. ` + (info.notes ? info.notes.split("\n")[0] : "");
  banner.classList.remove("hidden");
}

async function checkForUpdate() {
  const r = await api("check_update");
  if (r.available) showUpdateBanner(r);
}

async function onApplyUpdate() {
  const btn = document.getElementById("btnApplyUpdate");
  btn.disabled = true;
  btn.textContent = "Baixando… 0%";
  const r = await api("apply_update");
  if (!r.ok) {
    toast(r.error || "Não foi possível atualizar.", true);
    btn.disabled = false;
    btn.textContent = "Atualizar agora";
  }
  // sucesso: onUpdateProgress/onUpdateRestarting cuidam do resto (a janela fecha sozinha)
}

window.onUpdateProgress = (payload) => {
  const btn = document.getElementById("btnApplyUpdate");
  if (btn) btn.textContent = `Baixando… ${payload.pct}%`;
};
window.onUpdateFailed = (payload) => {
  toast(payload.error || "Falha ao atualizar.", true);
  const btn = document.getElementById("btnApplyUpdate");
  if (btn) { btn.disabled = false; btn.textContent = "Atualizar agora"; }
};
window.onUpdateRestarting = () => {
  document.getElementById("updateBannerText").textContent = "Atualizando e reiniciando…";
  document.getElementById("btnDismissUpdate").classList.add("hidden");
  document.getElementById("btnApplyUpdate").disabled = true;
};

// ---------- compatibilidade: eventos empurrados pelo Python ----------
window.onCompatProgress = (payload) => {
  document.getElementById("compatStatus").textContent = payload.statusText;
};
window.onCompatDone = (payload) => {
  document.getElementById("compatStatus").textContent = payload.statusText;
};
window.onCompatResult = (payload) => {
  document.getElementById("compatStatus").textContent = payload.statusText;
  const g = state.games.find((x) => x.appid === payload.appid);
  if (g) { g.compat = payload.compat; g.compatOk = payload.compatOk; }
  renderGameList();
};

// ---------- inicialização ----------
async function init() {
  document.getElementById("search").addEventListener("input", (e) => {
    state.filterText = e.target.value;
    renderGameList();
  });
  document.getElementById("filterCompat").addEventListener("change", (e) => {
    state.filterCompat = e.target.checked;
    renderGameList();
  });
  document.getElementById("btnRescan").addEventListener("click", () => api("start_compat_scan", true));
  document.getElementById("btnApplyUpdate").addEventListener("click", onApplyUpdate);
  document.getElementById("btnDismissUpdate").addEventListener("click", () => {
    updateDismissed = true;
    document.getElementById("updateBanner").classList.add("hidden");
  });

  const status = await api("status");
  const steamStatus = document.getElementById("steamStatus");
  document.getElementById("appVersion").textContent = status.appVersion ? `v${status.appVersion}` : "";
  if (status.steamPath) {
    steamStatus.textContent = `${status.steamPath} · ${status.gameCount} jogos`;
    setStatus(`Steam: ${status.steamPath} · ${status.gameCount} jogos`);
  } else {
    steamStatus.textContent = "Steam não encontrada.";
    setStatus("Steam não encontrada.");
  }

  state.steamOptions = await api("steam_game_options");
  await refreshGames();
  api("start_compat_scan", false);
  checkForUpdate();
}

window.addEventListener("pywebviewready", init);
