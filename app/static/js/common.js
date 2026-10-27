/* Shared helpers: API calls, formatting, toasts and the add/edit monitor modal. */
(function () {
  "use strict";

  async function api(path, options) {
    options = options || {};
    const init = { method: options.method || "GET", headers: { Accept: "application/json" } };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const res = await fetch(path, init);
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
    if (!res.ok) {
      const err = new Error(errorMessage(data) || `Request failed (HTTP ${res.status})`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function errorMessage(data) {
    if (!data || data.detail == null) return "";
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join("; ");
    return "";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function timeAgo(iso) {
    if (!iso) return "never";
    const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (secs < 5) return "just now";
    if (secs < 60) return secs + "s ago";
    const mins = Math.round(secs / 60);
    if (mins < 60) return mins + "m ago";
    const hrs = Math.round(mins / 60);
    if (hrs < 48) return hrs + "h ago";
    return Math.round(hrs / 24) + "d ago";
  }

  function formatDate(iso) {
    return iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
  }

  function formatDuration(seconds) {
    if (seconds == null) return "";
    if (seconds < 60) return Math.round(seconds) + "s";
    if (seconds < 3600) return Math.round(seconds / 60) + "m";
    return (seconds / 3600).toFixed(1) + "h";
  }

  function formatMs(v) { return v == null ? "–" : window.APIWatchChart.formatMs(v); }
  function formatPct(v) { return v == null ? "N/A" : (v >= 99.995 ? "100" : v.toFixed(2)) + "%"; }

  function statusPill(status, extraClass) {
    return `<span class="pill ${escapeHtml(status)} ${extraClass || ""}">${escapeHtml(status)}</span>`;
  }

  function toast(message, kind) {
    const box = document.getElementById("toasts");
    const node = document.createElement("div");
    node.className = "toast " + (kind || "info");
    node.textContent = message;
    box.appendChild(node);
    setTimeout(() => node.remove(), 4200);
  }

  const ICONS = {
    play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
  };

  // ---------------- monitor modal ----------------
  const modal = document.getElementById("monitor-modal");
  const form = document.getElementById("monitor-form");
  let editingId = null;
  let onSaved = null;

  const DEFAULTS = { name: "", url: "", method: "GET", expected_status: 200, interval_seconds: 60, timeout_seconds: 10, slow_threshold_ms: 1000, enabled: true, inspect_json: false };

  function clearErrors() {
    form.querySelectorAll(".field").forEach((f) => { f.classList.remove("invalid"); const e = f.querySelector(".error"); if (e) e.textContent = ""; });
    const fe = document.getElementById("form-error"); fe.hidden = true; fe.textContent = "";
  }

  function showFieldError(name, msg) {
    const field = form.querySelector(`[data-field="${name}"]`);
    if (field && field.querySelector(".error")) {
      field.classList.add("invalid");
      field.querySelector(".error").textContent = msg.replace(/^Value error, /, "");
      return true;
    }
    return false;
  }

  function openMonitorModal(monitor, callback) {
    editingId = monitor ? monitor.id : null;
    onSaved = callback || null;
    const data = Object.assign({}, DEFAULTS, monitor || {});
    for (const [k, v] of Object.entries(DEFAULTS)) {
      const input = form.elements[k];
      if (!input) continue;
      if (typeof v === "boolean") input.checked = !!data[k]; else input.value = data[k];
    }
    document.getElementById("monitor-modal-title").textContent = monitor ? "Edit monitor" : "Add monitor";
    document.getElementById("monitor-submit").textContent = monitor ? "Save changes" : "Create monitor";
    clearErrors();
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    setTimeout(() => form.elements.name.focus(), 30);
  }

  function closeMonitorModal() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }

  function readForm() {
    const f = form.elements;
    return {
      name: f.name.value.trim(),
      url: f.url.value.trim(),
      method: f.method.value,
      expected_status: Number(f.expected_status.value),
      interval_seconds: Number(f.interval_seconds.value),
      timeout_seconds: Number(f.timeout_seconds.value),
      slow_threshold_ms: Number(f.slow_threshold_ms.value),
      enabled: f.enabled.checked,
      inspect_json: f.inspect_json.checked,
    };
  }

  function validateClient(d) {
    let ok = true;
    const fail = (k, m) => { showFieldError(k, m); ok = false; };
    if (!d.name) fail("name", "Name is required.");
    if (!/^https?:\/\/[^\s/]+/i.test(d.url)) fail("url", "Enter a full http:// or https:// URL.");
    if (!Number.isInteger(d.expected_status) || d.expected_status < 100 || d.expected_status > 599) fail("expected_status", "Use a status code between 100 and 599.");
    if (!Number.isInteger(d.interval_seconds) || d.interval_seconds < 30 || d.interval_seconds > 3600) fail("interval_seconds", "Interval must be 30–3600 seconds.");
    if (!(d.timeout_seconds > 0 && d.timeout_seconds <= 30)) fail("timeout_seconds", "Timeout must be between 0 and 30 seconds.");
    else if (d.timeout_seconds >= d.interval_seconds) fail("timeout_seconds", "Timeout must be shorter than the interval.");
    if (!Number.isInteger(d.slow_threshold_ms) || d.slow_threshold_ms < 1 || d.slow_threshold_ms > 60000) fail("slow_threshold_ms", "Threshold must be 1–60000 ms.");
    return ok;
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    clearErrors();
    const data = readForm();
    if (!validateClient(data)) return;
    const btn = document.getElementById("monitor-submit");
    btn.disabled = true;
    try {
      const saved = editingId
        ? await api(`/api/monitors/${editingId}`, { method: "PUT", body: data })
        : await api("/api/monitors", { method: "POST", body: data });
      closeMonitorModal();
      toast(editingId ? `Saved “${saved.name}”` : `Created “${saved.name}”`, "success");
      if (onSaved) onSaved(saved);
    } catch (err) {
      let placed = false;
      if (err.data && Array.isArray(err.data.detail)) {
        for (const d of err.data.detail) {
          const key = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : null;
          if (key && showFieldError(key, d.msg)) placed = true;
        }
      }
      if (!placed) {
        const fe = document.getElementById("form-error");
        fe.textContent = err.message;
        fe.hidden = false;
      }
    } finally {
      btn.disabled = false;
    }
  });

  modal.addEventListener("click", (ev) => {
    if (ev.target === modal || ev.target.closest("[data-action=close-modal]")) closeMonitorModal();
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && modal.classList.contains("open")) closeMonitorModal(); });

  // ---------------- shared monitor actions ----------------
  async function runCheck(id, button) {
    if (button) { button.disabled = true; button.querySelector("svg") && button.querySelector("svg").classList.add("spin"); }
    try {
      const r = await api(`/api/monitors/${id}/check`, { method: "POST" });
      const detail = r.response_time_ms != null ? ` in ${formatMs(r.response_time_ms)}` : "";
      toast(`Check finished: ${r.status}${detail}${r.error_message ? " – " + r.error_message : ""}`, r.status === "DOWN" ? "error" : "success");
      return r;
    } catch (err) {
      toast(err.message, "error");
      return null;
    } finally {
      if (button) { button.disabled = false; button.querySelector("svg") && button.querySelector("svg").classList.remove("spin"); }
    }
  }

  function monitorPayload(m) {
    const p = {};
    for (const k of Object.keys(DEFAULTS)) p[k] = m[k];
    return p;
  }

  async function setEnabled(monitor, enabled) {
    const updated = await api(`/api/monitors/${monitor.id}`, { method: "PUT", body: Object.assign(monitorPayload(monitor), { enabled }) });
    toast(`${updated.name} ${enabled ? "enabled" : "paused"}`, "info");
    return updated;
  }

  async function deleteMonitor(monitor) {
    if (!window.confirm(`Delete “${monitor.name}” and all of its history? This cannot be undone.`)) return false;
    await api(`/api/monitors/${monitor.id}`, { method: "DELETE" });
    toast(`Deleted “${monitor.name}”`, "info");
    return true;
  }

  function setRefreshed() {
    const t = document.getElementById("refresh-text");
    if (t) t.textContent = "Updated " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function bindWindowPicker(onChange) {
    const picker = document.getElementById("window-picker");
    if (!picker) return;
    picker.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-window]");
      if (!btn) return;
      picker.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      onChange(btn.dataset.window);
    });
  }

  window.APIWatch = {
    api, escapeHtml, timeAgo, formatDate, formatDuration, formatMs, formatPct, statusPill, toast, ICONS,
    openMonitorModal, closeMonitorModal, runCheck, setEnabled, deleteMonitor, setRefreshed, bindWindowPicker,
  };
})();
