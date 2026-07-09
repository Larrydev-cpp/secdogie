"use strict";

const STATUS_POLL_MS = 700;

const state = {
  windows: [], // [{id, title, left, top, width, height}]
  selected: new Set(),
  status: {}, // id -> {status, detail}
};

const el = {
  banner: document.getElementById("banner"),
  list: document.getElementById("window-list"),
  empty: document.getElementById("empty-state"),
  task: document.getElementById("task"),
  model: document.getElementById("model"),
  maxSteps: document.getElementById("max-steps"),
  auto: document.getElementById("auto"),
  autoHelp: document.getElementById("auto-help"),
  refresh: document.getElementById("refresh"),
  start: document.getElementById("start"),
  stop: document.getElementById("stop"),
  themeToggle: document.getElementById("theme-toggle"),
};

function showBanner(message, kind) {
  el.banner.textContent = message;
  el.banner.className = `banner show ${kind}`;
}
function hideBanner() {
  el.banner.className = "banner";
}

// -- theme ---------------------------------------------------------

function applyTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  el.themeToggle.textContent = mode === "dark" ? "☀️" : "🌙";
  el.themeToggle.setAttribute("aria-label", mode === "dark" ? "Switch to light theme" : "Switch to dark theme");
}

function initTheme() {
  const saved = localStorage.getItem("secdogie-theme");
  const mode = saved || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  applyTheme(mode);
}

el.themeToggle.addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  localStorage.setItem("secdogie-theme", next);
  applyTheme(next);
});

// -- window list ---------------------------------------------------------

function windowCard(win) {
  const li = document.createElement("li");
  li.className = "window-card loading";
  li.dataset.id = win.id;

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.selected.has(win.id);
  checkbox.setAttribute("aria-label", `Select ${win.title}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selected.add(win.id);
    else state.selected.delete(win.id);
    li.classList.toggle("selected", checkbox.checked);
  });

  const thumb = document.createElement("img");
  thumb.className = "window-thumb";
  thumb.alt = "";
  thumb.src = `/api/thumbnail?id=${encodeURIComponent(win.id)}`;
  thumb.addEventListener("load", () => li.classList.remove("loading"));
  thumb.addEventListener("error", () => {
    thumb.removeAttribute("src");
    li.classList.remove("loading");
  });

  const meta = document.createElement("div");
  meta.className = "window-meta";
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = win.title;
  const geo = document.createElement("div");
  geo.className = "geo";
  geo.textContent = `${win.width}×${win.height} @ (${win.left}, ${win.top})`;
  meta.append(title, geo);

  const badge = document.createElement("span");
  badge.className = "status-badge";
  badge.textContent = "idle";

  li.append(checkbox, thumb, meta, badge);
  if (checkbox.checked) li.classList.add("selected");
  return li;
}

function renderWindows() {
  el.list.innerHTML = "";
  el.empty.hidden = state.windows.length !== 0;
  for (const win of state.windows) {
    el.list.appendChild(windowCard(win));
  }
  renderStatus(); // apply whatever status we already have onto the freshly rendered cards
}

async function fetchWindows() {
  el.refresh.disabled = true;
  try {
    const resp = await fetch("/api/windows");
    const data = await resp.json();
    if (data.error) {
      showBanner(data.error, "error");
      state.windows = [];
      renderWindows();
      return;
    }
    hideBanner();
    // Keep selection for windows that are still present after a refresh.
    const stillPresent = new Set(data.windows.map((w) => w.id));
    for (const id of [...state.selected]) {
      if (!stillPresent.has(id)) state.selected.delete(id);
    }
    state.windows = data.windows;
    renderWindows();
  } catch (e) {
    showBanner(`Could not reach the secdogie-open server: ${e}`, "error");
  } finally {
    el.refresh.disabled = false;
  }
}

// -- status polling ---------------------------------------------------------

function renderStatus() {
  for (const li of el.list.children) {
    const id = li.dataset.id;
    const entry = state.status[id];
    const badge = li.querySelector(".status-badge");
    if (!entry) {
      badge.textContent = "idle";
      badge.className = "status-badge";
      continue;
    }
    const [status, detail] = entry;
    badge.textContent = detail ? `${status}: ${detail}` : status;
    badge.className = `status-badge ${status}`;
  }
}

async function pollStatus() {
  try {
    const resp = await fetch("/api/status");
    state.status = await resp.json();
    renderStatus();
  } catch {
    // A transient poll failure isn't worth a banner; the next tick retries.
  }
}

// -- actions ---------------------------------------------------------

el.refresh.addEventListener("click", fetchWindows);

el.start.addEventListener("click", async () => {
  const task = el.task.value.trim();
  if (!task) {
    showBanner("Enter a task first.", "error");
    return;
  }
  if (state.selected.size === 0) {
    showBanner("Select at least one window.", "error");
    return;
  }
  const auto = el.auto.checked;
  if (auto && !window.confirm(`${el.autoHelp.textContent.trim()}\n\nStart anyway?`)) {
    return;
  }

  el.start.disabled = true;
  try {
    const resp = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task,
        model: el.model.value.trim(),
        max_steps: parseInt(el.maxSteps.value, 10) || 50,
        auto,
        window_ids: [...state.selected],
      }),
    });
    const data = await resp.json();
    if (data.error) {
      showBanner(data.error, "error");
      return;
    }
    hideBanner();
    if (data.skipped && data.skipped.length) {
      showBanner(`Started ${data.started.length}; skipped ${data.skipped.length} (already running).`, "info");
    }
    await pollStatus();
  } catch (e) {
    showBanner(`Could not start: ${e}`, "error");
  } finally {
    el.start.disabled = false;
  }
});

el.stop.addEventListener("click", async () => {
  el.stop.disabled = true;
  try {
    await fetch("/api/stop", { method: "POST" });
    await pollStatus();
  } finally {
    el.stop.disabled = false;
  }
});

// -- boot ---------------------------------------------------------

initTheme();
fetchWindows();
setInterval(pollStatus, STATUS_POLL_MS);
