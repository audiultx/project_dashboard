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
  me: null,
  signup: { bootstrap: false, signup_open: false },
  projects: [],
  stats: {},
  tokens: [],
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
  // SQLite stores 'YYYY-MM-DD HH:MM:SS' (UTC); normalize the space to 'T' so
  // every browser parses it as ISO 8601.
  const d = new Date(iso.replace(" ", "T") + "Z");
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    // Session expired / never logged in while the app view is up → back to login.
    if (res.status === 401 && state.me) {
      state.me = null;
      showAuthView();
    }
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((e) => e.msg).join("; ");
    } catch { /* non-JSON error body */ }
    const err = new Error(`${res.status}: ${detail}`);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------------------------------------------------------------- auth / session

function canManage(p) {
  return !!state.me && (state.me.is_admin || p.owner_id === state.me.id);
}

async function showAuthView() {
  // Tear down authenticated-session UI so nothing from the previous session
  // lingers over the login screen. The modals are siblings of #app-main (not
  // children), so hiding #app-main alone leaves an open modal painted on top.
  document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) => m.classList.add("hidden"));
  state.detailId = null;
  state.projects = [];
  // In admin sessions this holds every user's token metadata — drop it on logout.
  state.tokens = [];
  clearTokenCallout();
  $("#app-main").classList.add("hidden");
  $("#user-chip").classList.add("hidden");
  $("#btn-logout").classList.add("hidden");
  $("#btn-tokens").classList.add("hidden");
  $("#btn-new").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
  $("#login-form").classList.remove("hidden");
  $("#register-form").classList.add("hidden");
  // Clear leftover input from a previous session so a typed password is not left in the DOM
  $("#login-form").reset();
  $("#register-form").reset();
  $("#login-error").classList.add("hidden");
  $("#register-error").classList.add("hidden");
  // Decide whether to offer a register link.
  let cfg = { bootstrap: false, signup_open: false };
  try { cfg = await api("/api/auth/config"); } catch { /* stay on login */ }
  state.signup = cfg;
  const canRegister = cfg.bootstrap || cfg.signup_open;
  const sw = $("#auth-switch");
  if (canRegister) {
    sw.classList.remove("hidden");
    $("#auth-switch-text").textContent = cfg.bootstrap
      ? "No account yet? The first account becomes admin."
      : "No account?";
    $("#auth-switch-link").textContent = "Create one";
  } else {
    sw.classList.add("hidden");
  }
  setTimeout(() => $("#login-username").focus(), 50);
}

function showAppView() {
  $("#auth-view").classList.add("hidden");
  $("#app-main").classList.remove("hidden");
  const chip = $("#user-chip");
  chip.textContent = `${state.me.display_name || state.me.username}${state.me.is_admin ? " · admin" : ""}`;
  chip.classList.remove("hidden");
  $("#btn-logout").classList.remove("hidden");
  $("#btn-tokens").classList.remove("hidden");
  $("#btn-new").classList.remove("hidden");
}

async function submitLogin(e) {
  e.preventDefault();
  const errEl = $("#login-error");
  errEl.classList.add("hidden");
  try {
    state.me = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("#login-username").value.trim(),
        password: $("#login-password").value,
      }),
    });
    showAppView();
    await loadAll();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
}

async function submitRegister(e) {
  e.preventDefault();
  const errEl = $("#register-error");
  errEl.classList.add("hidden");
  const password = $("#reg-password").value;
  if (password.length < 8) {
    errEl.textContent = "Password must be at least 8 characters.";
    errEl.classList.remove("hidden");
    return;
  }
  try {
    // Register auto-logs-in (session cookie is set by the response).
    state.me = await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        username: $("#reg-username").value.trim(),
        password,
        display_name: $("#reg-display-name").value.trim(),
      }),
    });
    showAppView();
    await loadAll();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* best effort */ }
  state.me = null;
  showAuthView();
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
        <span class="badge owner" title="Owner">@${escapeHtml(p.owner_username || "?")}</span>
        ${tagList(p.tags).length ? `<span class="pc-tags">${tagList(p.tags).map((t) => "#" + escapeHtml(t)).join(" ")}</span>` : ""}
        <span class="pc-date">updated ${fmtDate(p.updated_at)}${p.updated_by_username ? ` by @${escapeHtml(p.updated_by_username)}` : ""}</span>
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
      <span class="badge owner" title="Owner">@${escapeHtml(p.owner_username || "?")}</span>
      ${tags.map((t) => `<span class="badge">#${escapeHtml(t)}</span>`).join("")}
    </div>
    ${desc}
    <div class="detail-dates">Created ${fmtDate(p.created_at)} · Updated ${fmtDate(p.updated_at)}${p.updated_by_username ? ` by @${escapeHtml(p.updated_by_username)}` : ""}</div>`;
  // Owner-gated writes: hide edit/delete for non-owner non-admins.
  const writable = canManage(p);
  $("#btn-detail-edit").style.display = writable ? "" : "none";
  $("#btn-detail-delete").style.display = writable ? "" : "none";
  $("#detail-readonly").classList.toggle("hidden", writable);
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

// ---------------------------------------------------------------- API tokens

async function openTokens() {
  $("#token-form").reset();
  $("#token-error").classList.add("hidden");
  clearTokenCallout();
  const adminRow = $("#token-admin-row");
  adminRow.classList.toggle("hidden", !state.me.is_admin);
  $("#token-admin-toggle").checked = false;
  // Clear rows/errors from a previous open so nothing (including other users'
  // tokens with live Revoke buttons for an admin) flashes until the fetch resolves.
  $("#token-list").innerHTML = "";
  openModal("tokens-modal");
  setTimeout(() => $("#t-name").focus(), 50);
  await loadTokens();
}

async function loadTokens() {
  try {
    const all = $("#token-admin-toggle").checked;
    state.tokens = await api(`/api/auth/tokens${all ? "?all=1" : ""}`);
    renderTokens(all);
  } catch (err) {
    $("#token-list").innerHTML = `<p class="empty token-list-empty">Failed to load: ${escapeHtml(err.message)}</p>`;
  }
}

function renderTokens(showOwner) {
  const list = $("#token-list");
  if (!state.tokens.length) {
    list.innerHTML = `<p class="empty token-list-empty">No API tokens yet.</p>`;
    return;
  }
  list.innerHTML = state.tokens.map((t) => `
    <div class="token-row${t.revoked_at ? " revoked" : ""}">
      <div class="token-info">
        <span class="token-name">${escapeHtml(t.name)}</span>
        ${t.revoked_at ? `<span class="badge badge-revoked">Revoked</span>` : ""}
        ${showOwner ? `<span class="badge owner" title="Owner">@${escapeHtml(t.owner_username || "?")}</span>` : ""}
      </div>
      ${t.revoked_at ? "" : `<button class="btn btn-danger btn-sm" data-revoke="${t.id}" type="button">Revoke</button>`}
      <div class="token-meta">created ${fmtDate(t.created_at)} · last used ${t.last_used_at ? fmtDate(t.last_used_at) : "never"} · expires ${t.expires_at ? fmtDate(t.expires_at) : "never"}</div>
    </div>`).join("");
  list.querySelectorAll("[data-revoke]").forEach((btn) => {
    btn.addEventListener("click", () => revokeToken(Number(btn.dataset.revoke)));
  });
}

async function submitTokenForm(e) {
  e.preventDefault();
  const errEl = $("#token-error");
  errEl.classList.add("hidden");
  const name = $("#t-name").value.trim();
  const expires = $("#t-expires").value;
  if (!name) {
    errEl.textContent = "Name is required.";
    errEl.classList.remove("hidden");
    return;
  }
  // Guard against a double-submit minting a second token whose plaintext is lost:
  // the button stays disabled for the whole in-flight POST.
  const submitBtn = $("#token-form button[type=submit]");
  submitBtn.disabled = true;
  try {
    const res = await api("/api/auth/tokens", {
      method: "POST",
      body: JSON.stringify({
        name,
        // A bare date means end-of-day UTC, so a "today" pick doesn't expire immediately.
        expires_at: expires ? `${expires}T23:59:59` : null,
      }),
    });
    // The plaintext is shown exactly once — kept only in the callout, never in state.
    $("#token-plaintext").textContent = res.token;
    $("#token-callout").classList.remove("hidden");
    $("#token-form").reset();
    await loadTokens();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
  }
}

async function copyToken() {
  const text = $("#token-plaintext").textContent;
  const btn = $("#btn-copy-token");
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      // Self-hosted plain-HTTP fallback where the Clipboard API is unavailable.
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      let ok = false;
      try {
        ta.select();
        ok = document.execCommand("copy");
      } finally {
        // Always remove the plaintext-bearing textarea, even if copy throws.
        ta.remove();
      }
      if (!ok) throw new Error("copy command failed");
    }
    btn.textContent = "Copied!";
  } catch {
    btn.textContent = "Copy failed";
  }
  setTimeout(() => { btn.textContent = "Copy"; }, 1500);
}

async function revokeToken(id) {
  const t = state.tokens.find((x) => x.id === id);
  if (!t) return;
  if (!confirm(`Revoke “${t.name}”? Clients using it will stop working.`)) return;
  try {
    await api(`/api/auth/tokens/${id}`, { method: "DELETE" });
    await loadTokens();
  } catch (err) {
    if (err.status === 404) await loadTokens(); // already revoked
    else alert(`Revoke failed: ${err.message}`);
  }
}

function clearTokenCallout() {
  $("#token-callout").classList.add("hidden");
  $("#token-plaintext").textContent = "";
}

// ---------------------------------------------------------------- modals

function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}
function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
  if (id === "tokens-modal") clearTokenCallout();
}

// ---------------------------------------------------------------- wiring

$("#btn-new").addEventListener("click", () => openForm());
$("#project-form").addEventListener("submit", submitForm);
$("#login-form").addEventListener("submit", submitLogin);
$("#register-form").addEventListener("submit", submitRegister);
$("#btn-logout").addEventListener("click", logout);
$("#btn-tokens").addEventListener("click", openTokens);
$("#token-form").addEventListener("submit", submitTokenForm);
$("#btn-copy-token").addEventListener("click", copyToken);
$("#token-admin-toggle").addEventListener("change", loadTokens);
$("#auth-switch-link").addEventListener("click", (e) => {
  e.preventDefault();
  const reg = $("#register-form");
  const showRegister = reg.classList.contains("hidden"); // toggle
  $("#login-form").classList.toggle("hidden", showRegister);
  reg.classList.toggle("hidden", !showRegister);
  if (showRegister) {
    $("#auth-switch-text").textContent = "Already have an account?";
    $("#auth-switch-link").textContent = "Sign in";
    $("#reg-username").focus();
  } else {
    $("#auth-switch-text").textContent = state.signup.bootstrap
      ? "No account yet? The first account becomes admin."
      : "No account?";
    $("#auth-switch-link").textContent = "Create one";
    $("#login-username").focus();
  }
});
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
    if (e.target === backdrop) closeModal(backdrop.id);
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((m) => closeModal(m.id));
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

async function init() {
  // Check for an existing session; fall back to the login screen on 401.
  try {
    state.me = await api("/api/auth/me");
  } catch (err) {
    if (err.status === 401) {
      showAuthView();
      return;
    }
    $("#project-list").innerHTML = `<p class="empty">Failed to load: ${escapeHtml(err.message)}</p>`;
    return;
  }
  showAppView();
  loadAll().catch((err) => {
    $("#project-list").innerHTML = `<p class="empty">Failed to load: ${escapeHtml(err.message)}</p>`;
  });
}

init();
