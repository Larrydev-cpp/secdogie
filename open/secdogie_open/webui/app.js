"use strict";

const STATUS_POLL_MS = 700;
const state = {
  windows: [],
  selected: new Set(),
  status: {},
  models: [],
  selectedModels: new Set(),
};

const el = {
  banner: document.getElementById("banner"),
  list: document.getElementById("window-list"),
  empty: document.getElementById("empty-state"),
  task: document.getElementById("task"),
  modelSearch: document.getElementById("model-search"),
  modelOptions: document.getElementById("model-options"),
  modelCount: document.getElementById("model-count"),
  modelRefresh: document.getElementById("model-refresh"),
  modelSelectVisible: document.getElementById("model-select-visible"),
  modelClear: document.getElementById("model-clear"),
  modelCustom: document.getElementById("model-custom"),
  maxSteps: document.getElementById("max-steps"),
  apiKey: document.getElementById("api-key"),
  keyToggle: document.getElementById("key-toggle"),
  auto: document.getElementById("auto"),
  autoHelp: document.getElementById("auto-help"),
  refresh: document.getElementById("refresh"),
  start: document.getElementById("start"),
  stop: document.getElementById("stop"),
  themeToggle: document.getElementById("theme-toggle"),
};

function showBanner(message, kind) { el.banner.textContent = message; el.banner.className = `banner show ${kind}`; }
function hideBanner() { el.banner.className = "banner"; }

function applyTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  el.themeToggle.textContent = mode === "dark" ? "☀️" : "🌙";
  el.themeToggle.setAttribute("aria-label", mode === "dark" ? "Switch to light theme" : "Switch to dark theme");
}
function initTheme() {
  const saved = localStorage.getItem("secdogie-theme");
  applyTheme(saved || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
}
el.themeToggle.addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  localStorage.setItem("secdogie-theme", next); applyTheme(next);
});

function visibleModels() {
  const query = el.modelSearch.value.trim().toLowerCase();
  if (!query) return state.models;
  return state.models.filter((m) => `${m.name} ${m.id} ${m.provider}`.toLowerCase().includes(query));
}

function renderModels() {
  el.modelOptions.innerHTML = "";
  for (const model of visibleModels()) {
    const row = document.createElement("label");
    row.className = "model-option" + (state.selectedModels.has(model.id) ? " selected" : "");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedModels.has(model.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedModels.add(model.id);
      else state.selectedModels.delete(model.id);
      row.classList.toggle("selected", checkbox.checked);
      renderModelCount();
    });
    const main = document.createElement("span");
    main.className = "model-option-main";
    main.textContent = model.name || model.id;
    const meta = document.createElement("span");
    meta.className = "model-option-meta";
    const parts = [model.id.replace(/^openrouter\//, ""), model.vision ? "Vision" : "Text"];
    if (model.context_length) parts.push(`${Number(model.context_length).toLocaleString()} ctx`);
    meta.textContent = parts.join(" · ");
    row.append(checkbox, main, meta);
    el.modelOptions.appendChild(row);
  }
  if (!visibleModels().length) {
    const empty = document.createElement("div"); empty.className = "model-empty"; empty.textContent = "No models match this search.";
    el.modelOptions.appendChild(empty);
  }
  renderModelCount();
}
function renderModelCount() { el.modelCount.textContent = `${state.selectedModels.size} selected`; }

async function initModelPicker() {
  el.modelRefresh.disabled = true;
  try {
    const resp = await fetch("/api/models");
    const catalog = await resp.json();
    if (!resp.ok || catalog.error) throw new Error(catalog.error || `HTTP ${resp.status}`);
    state.models = (catalog.models || []).filter((m) => m.vision !== false);
    const saved = JSON.parse(localStorage.getItem("secdogie-models") || "[]");
    const available = new Set(state.models.map((m) => m.id));
    state.selectedModels = new Set(saved.filter((id) => available.has(id)));
    if (!state.selectedModels.size && catalog.default && available.has(catalog.default)) state.selectedModels.add(catalog.default);
    renderModels();
  } catch (e) {
    showBanner(`Could not load the OpenRouter model catalogue: ${e}`, "error");
  } finally { el.modelRefresh.disabled = false; }
}

el.modelSearch.addEventListener("input", renderModels);
el.modelRefresh.addEventListener("click", initModelPicker);
el.modelClear.addEventListener("click", () => { state.selectedModels.clear(); renderModels(); });
el.modelSelectVisible.addEventListener("click", () => { for (const model of visibleModels()) state.selectedModels.add(model.id); renderModels(); });

function initApiKey() {
  el.apiKey.value = localStorage.getItem("secdogie-api-key") || "";
  el.keyToggle.addEventListener("click", () => {
    const showing = el.apiKey.type === "text";
    el.apiKey.type = showing ? "password" : "text";
    el.keyToggle.textContent = showing ? "Show" : "Hide";
    el.keyToggle.setAttribute("aria-pressed", String(!showing));
  });
}

function windowCard(win) {
  const li = document.createElement("li"); li.className = "window-card loading"; li.dataset.id = win.id;
  const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(win.id);
  checkbox.setAttribute("aria-label", `Select ${win.title}`);
  checkbox.addEventListener("change", () => { if (checkbox.checked) state.selected.add(win.id); else state.selected.delete(win.id); li.classList.toggle("selected", checkbox.checked); });
  const thumb = document.createElement("img"); thumb.className = "window-thumb"; thumb.alt = ""; thumb.src = `/api/thumbnail?id=${encodeURIComponent(win.id)}`;
  thumb.addEventListener("load", () => li.classList.remove("loading")); thumb.addEventListener("error", () => { thumb.removeAttribute("src"); li.classList.remove("loading"); });
  const meta = document.createElement("div"); meta.className = "window-meta";
  const title = document.createElement("div"); title.className = "title"; title.textContent = win.title;
  const geo = document.createElement("div"); geo.className = "geo"; geo.textContent = `${win.width}×${win.height} @ (${win.left}, ${win.top})`;
  meta.append(title, geo);
  const badge = document.createElement("span"); badge.className = "status-badge"; badge.textContent = "idle";
  li.append(checkbox, thumb, meta, badge); if (checkbox.checked) li.classList.add("selected"); return li;
}
function renderWindows() {
  el.list.innerHTML = ""; el.empty.hidden = state.windows.length !== 0;
  for (const win of state.windows) el.list.appendChild(windowCard(win));
  renderStatus();
}
async function fetchWindows() {
  el.refresh.disabled = true;
  try {
    const resp = await fetch("/api/windows"); const data = await resp.json();
    if (data.error) { showBanner(data.error, "error"); state.windows = []; renderWindows(); return; }
    hideBanner(); const present = new Set(data.windows.map((w) => w.id));
    for (const id of [...state.selected]) if (!present.has(id)) state.selected.delete(id);
    state.windows = data.windows; renderWindows();
  } catch (e) { showBanner(`Could not reach the secdogie-open server: ${e}`, "error"); }
  finally { el.refresh.disabled = false; }
}

function renderStatus() {
  for (const li of el.list.children) {
    const entry = state.status[li.dataset.id]; const badge = li.querySelector(".status-badge");
    if (!entry) { badge.textContent = "idle"; badge.className = "status-badge"; continue; }
    const [status, detail] = entry; badge.textContent = detail ? `${status}: ${detail}` : status; badge.className = `status-badge ${status}`;
  }
}
async function pollStatus() { try { const resp = await fetch("/api/status"); state.status = await resp.json(); renderStatus(); } catch {} }

el.refresh.addEventListener("click", fetchWindows);
el.start.addEventListener("click", async () => {
  const task = el.task.value.trim();
  if (!task) return showBanner("Enter a task first.", "error");
  if (!state.selected.size) return showBanner("Select at least one window.", "error");
  const custom = el.modelCustom.value.trim();
  const models = [...state.selectedModels];
  if (custom) models.push(`openrouter/${custom.replace(/^openrouter\//, "")}`);
  const uniqueModels = [...new Set(models)];
  if (!uniqueModels.length) return showBanner("Select at least one model.", "error");
  const auto = el.auto.checked;
  if (auto && !window.confirm(`${el.autoHelp.textContent.trim()}\n\nStart anyway?`)) return;

  const apiKey = el.apiKey.value.trim();
  localStorage.setItem("secdogie-models", JSON.stringify([...state.selectedModels]));
  localStorage.setItem("secdogie-api-key", apiKey);
  el.start.disabled = true;
  try {
    const resp = await fetch("/api/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, models: uniqueModels, model: uniqueModels[0], api_key: apiKey, max_steps: parseInt(el.maxSteps.value, 10) || 50, auto, window_ids: [...state.selected] }),
    });
    const data = await resp.json();
    if (data.error) { showBanner(data.error, "error"); return; }
    hideBanner(); showBanner(`Started ${data.started.length} model/window runs${data.skipped?.length ? `; skipped ${data.skipped.length}` : ""}.`, "info");
    await pollStatus();
  } catch (e) { showBanner(`Could not start: ${e}`, "error"); }
  finally { el.start.disabled = false; }
});

el.stop.addEventListener("click", async () => {
  el.stop.disabled = true;
  try { await fetch("/api/stop", { method: "POST" }); await pollStatus(); }
  finally { el.stop.disabled = false; }
});

initTheme(); initModelPicker(); initApiKey(); fetchWindows(); setInterval(pollStatus, STATUS_POLL_MS);
