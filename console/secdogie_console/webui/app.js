"use strict";

const $ = (id) => document.getElementById(id);
const conn = $("conn");
let requiresSignature = false;

function setConn(live) {
  conn.textContent = live ? "live" : "disconnected";
  conn.className = "pill " + (live ? "live" : "down");
}

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid);
  return node;
}

function renderNodes(nodes) {
  const body = document.querySelector("#nodes tbody");
  body.replaceChildren();
  $("nodes-empty").hidden = nodes.length > 0;
  for (const n of nodes) {
    body.append(el("tr", {},
      el("td", { text: n.node_id }),
      el("td", { text: n.label || "" }),
      el("td", { text: (n.capabilities || []).join(", ") }),
      el("td", { text: n.task_id || "idle" }),
    ));
  }
}

function renderTasks(tasks) {
  const body = document.querySelector("#tasks tbody");
  body.replaceChildren();
  $("tasks-empty").hidden = tasks.length > 0;
  for (const t of tasks) {
    const actions = el("td", {});
    if (t.state === "running" || t.state === "queued") {
      const stop = el("button", { class: "ghost", text: "stop" });
      stop.onclick = () => command({ op: "stop", task_id: t.task_id });
      actions.append(stop);
    } else if (t.state === "paused") {
      const resume = el("button", { class: "ghost", text: "resume" });
      resume.onclick = () => command({ op: "resume", task_id: t.task_id });
      actions.append(resume);
    }
    body.append(el("tr", {},
      el("td", { text: t.task_id }),
      el("td", {}, el("span", { class: "state " + t.state, text: t.state })),
      el("td", { text: t.task }),
      el("td", { text: t.node_id || "" }),
      actions,
    ));
  }
}

async function refresh() {
  try {
    const res = await fetch("/api/state");
    const state = await res.json();
    setConn(true);
    requiresSignature = !!state.requires_signature;
    $("signed-note").hidden = !requiresSignature;
    renderNodes(state.nodes || []);
    renderTasks(state.tasks || []);
  } catch (e) {
    setConn(false);
  }
}

async function command(body) {
  const msg = $("submit-msg");
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const out = await res.json();
    if (!res.ok) {
      msg.textContent = out.error || "command failed";
      msg.className = "msg bad";
    } else {
      msg.textContent = out.task_id ? `queued ${out.task_id}` : "ok";
      msg.className = "msg ok";
      refresh();
    }
  } catch (e) {
    msg.textContent = "request failed";
    msg.className = "msg bad";
  }
}

$("submit-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const task = $("task").value.trim();
  if (!task) return;
  command({ op: "submit", task, options: { auto: $("auto").checked } });
  $("task").value = "";
});

refresh();
setInterval(refresh, 2000);
