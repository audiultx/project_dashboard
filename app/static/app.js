/* Project Dashboard — frontend logic (vanilla JS) */
"use strict";

const STATUSES = ["idea", "planning", "in_progress", "paused", "done"];
const STATUS_LABELS = {
  idea: "Idea",
  planning: "Planning",
  in_progress: "In progress",
  paused: "Paused",
  done: "Done",
};

const state = {
  projects: [],
  stats: {},
  filters: { status: "", category: "", q: "" },
  detailId: null,
};

// ---------------------------------------------------------------- helpers

const $ = (sel) => document.querySelector(sel);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "Z"); // SQLite datetime('now') is UTC
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((e) => e.msg).join("; ");
    } catch { /* non-JSON error body */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------------------------------------------------------------- data

async function loadAll() {
  const params = new URLSearchParams();
  if (state.filters.status) params.set("status", state.filters.status);
  if (state.filters.category) params.set("category", state.filters.category);
  if (state.filters.q) params.set("q", state.filters.q);
  const qs = params.toString();
  const [projects, stats] = await Promise.all([
    api(`/api/projects${qs ? `?${qs}` : ""}`),
    api("/api/stats"),
  ]);
  state.projects = projects;
  state.stats = stats;
  renderStats();
  renderList();
  renderCategoryOptions();
}

// ---------------------------------------------------------------- rendering

function renderStats() {
  const el = $("#stats");
  el.innerHTML = STATUSES.map((s) => `
    <div class="stat ${state.filters.status === s ? "active" : ""}" data-status="${s}" title="Filter: ${STATUS_LABELS[s]}">
      <div class="stat-count">${state.stats[s] ?? 0}</div>
      <div class="stat-label"><span class="stat-dot" style="background:var(--c-${s})"></span>${STATUS_LABELS[s]}</div>
    </div>`).join("");
  el.querySelectorAll(".stat").forEach((node) => {
    node.addEventListener("click", () => {
      const s = node.dataset.status;
      state.filters.status = state.filters.status === s ? "" : s;
      $("#filter-status").value = state.filters.status;
      loadAll();
    });
  });
}

function tagList(tags) {
  return (tags || "").split(",").map((t) => t.trim()).filter(Boolean);
}

function renderList() {
  const list = $("#project-list");
  const empty = $("#empty-state");
  if (!state.projects.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  list.innerHTML = state.projects.map((p) => `
    <article class="project-card" data-id="${p.id}" style="border-left-color:var(--c-${p.status})">
      <div class="pc-title">${escapeHtml(p.title)}</div>
      ${p.description ? `<div class="pc-desc">${escapeHtml(p.description)}</div>` : ""}
      <div class="pc-meta">
        <span class="badge status-${p.status}"><span class="dot"></span>${STATUS_LABELS[p.status]}</span>
        <span class="badge prio-${p.priority}">${p.priority} priority</span>
        <span class="badge">${escapeHtml(p.category || "Other")}</span>
        ${tagList(p.tags).length ? `<span class="pc-tags">${tagList(p.tags).map((t) => "#" + escapeHtml(t)).join(" ")}</span>` : ""}
        <span class="pc-date">updated ${fmtDate(p.updated_at)}</span>
      </div>
    </article>`).join("");
  list.querySelectorAll(".project-card").forEach((node) => {
    node.addEventListener("click", () => openDetail(Number(node.dataset.id)));
  });
}

function renderCategoryOptions() {
  const cats = [...new Set(state.projects.map((p) => p.category).filter(Boolean))].sort();
  $("#category-options").innerHTML = cats.map((c) => `<option value="${escapeHtml(c)}">`).join("");
}

// ---------------------------------------------------------------- detail view

function openDetail(id) {
  const p = state.projects.find((x) => x.id === id);
  if (!p) return;
  state.detailId = id;
  $("#detail-title").textContent = p.title;
  const desc = p.description
    ? `<div class="detail-desc">${escapeHtml(p.description)}</div>`
    : `<div class="detail-desc empty-desc">No description yet.</div>`;
  const tags = tagList(p.tags);
  $("#detail-body").innerHTML = `
    <div class="detail-meta">
      <span class="badge status-${p.status}"><span class="dot"></span>${STATUS_LABELS[p.status]}</span>
      <span class="badge prio-${p.priority}">${p.priority} priority</span>
      <span class="badge">${escapeHtml(p.category || "Other")}</span>
      ${tags.map((t) => `<span class="badge">#${escapeHtml(t)}</span>`).join("")}
    </div>
    ${desc}
    <div class="detail-dates">Created ${fmtDate(p.created_at)} · Updated ${fmtDate(p.updated_at)}</div>`;
  openModal("detail-modal");
}

// ---------------------------------------------------------------- form modal

function openForm(project = null) {
  const form = $("#project-form");
  form.reset();
  $("#form-error").classList.add("hidden");
  $("#form-title").textContent = project ? "Edit Project" : "New Project";
  $("#f-id").value = project ? project.id : "";
  if (project) {
    $("#f-title").value = project.title;
    $("#f-description").value = project.description;
    $("#f-category").value = project.category;
    $("#f-status").value = project.status;
    $("#f-priority").value = project.priority;
    $("#f-tags").value = project.tags;
  } else {
    $("#f-status").value = "idea";
    $("#f-priority").value = "medium";
  }
  openModal("form-modal");
  setTimeout(() => $("#f-title").focus(), 50);
}

async function submitForm(e) {
  e.preventDefault();
  const errEl = $("#form-error");
  errEl.classList.add("hidden");
  const id = $("#f-id").value;
  const payload = {
    title: $("#f-title").value.trim(),
    description: $("#f-description").value,
    category: $("#f-category").value.trim() || "Other",
    status: $("#f-status").value,
    priority: $("#f-priority").value,
    tags: $("#f-tags").value.trim(),
  };
  if (!payload.title) {
    errEl.textContent = "Title is required.";
    errEl.classList.remove("hidden");
    return;
  }
  try {
    if (id) {
      await api(`/api/projects/${id}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/projects", { method: "POST", body: JSON.stringify(payload) });
    }
    closeModal("form-modal");
    await loadAll();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
}

// ---------------------------------------------------------------- delete

async function confirmDelete(id) {
  const p = state.projects.find((x) => x.id === id);
  if (!p) return;
  if (!confirm(`Delete “${p.title}”? This cannot be undone.`)) return;
  try {
    await api(`/api/projects/${id}`, { method: "DELETE" });
    if (state.detailId === id) closeModal("detail-modal");
    await loadAll();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

// ---------------------------------------------------------------- modals

function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

// ---------------------------------------------------------------- wiring

$("#btn-new").addEventListener("click", () => openForm());
$("#project-form").addEventListener("submit", submitForm);
$("#btn-detail-edit").addEventListener("click", () => {
  const p = state.projects.find((x) => x.id === state.detailId);
  if (!p) return;
  closeModal("detail-modal");
  openForm(p);
});
$("#btn-detail-delete").addEventListener("click", () => confirmDelete(state.detailId));

document.querySelectorAll("[data-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeModal(btn.dataset.close));
});
document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
  backdrop.addEventListener("mousedown", (e) => {
    if (e.target === backdrop) backdrop.classList.add("hidden");
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) => m.classList.add("hidden"));
  }
});

let searchTimer = null;
$("#filter-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.filters.q = e.target.value.trim();
    loadAll();
  }, 250);
});
$("#filter-status").addEventListener("change", (e) => {
  state.filters.status = e.target.value;
  loadAll();
});
let catTimer = null;
$("#filter-category").addEventListener("input", (e) => {
  clearTimeout(catTimer);
  catTimer = setTimeout(() => {
    state.filters.category = e.target.value.trim();
    loadAll();
  }, 300);
});
$("#btn-clear-filters").addEventListener("click", () => {
  state.filters = { status: "", category: "", q: "" };
  $("#filter-status").value = "";
  $("#filter-category").value = "";
  $("#filter-search").value = "";
  loadAll();
});

// ---------------------------------------------------------------- init

loadAll().catch((err) => {
  $("#project-list").innerHTML = `<p class="empty">Failed to load: ${escapeHtml(err.message)}</p>`;
});
