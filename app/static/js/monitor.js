/* Monitor details page. */
(function () {
  "use strict";
  const A = window.APIWatch;
  const id = window.APIWATCH_MONITOR_ID;
  const WINDOW_LABEL = { "24h": "last 24 hours", "7d": "last 7 days", all: "all stored data" };
  // Checks whose body could not be compared with the JSON baseline.
  const NOT_INSPECTED = new Set(["invalid_json", "json_not_object", "response_too_large"]);
  let currentWindow = "24h";
  let monitor = null;
  let chartSeries = [];

  function renderHeader() {
    document.getElementById("monitor-name").textContent = monitor.name;
    document.title = `${monitor.name} · APIWatch`;
    document.getElementById("monitor-url").textContent = `${monitor.method} ${monitor.url}`;
    const pill = document.getElementById("monitor-status");
    const status = monitor.enabled ? monitor.summary.current_status : "PAUSED";
    pill.className = `pill lg ${status}`;
    pill.textContent = status;
    document.getElementById("btn-toggle").textContent = monitor.enabled ? "Disable" : "Enable";
    document.getElementById("threshold-label").textContent = monitor.slow_threshold_ms;
  }

  function renderConfig() {
    const s = monitor.summary;
    const rows = [
      ["Endpoint", `<span class="mono">${A.escapeHtml(monitor.url)}</span>`],
      ["Method", `<span class="badge">${monitor.method}</span>`],
      ["Expected status", monitor.expected_status],
      ["Check interval", `${monitor.interval_seconds} s`],
      ["Timeout", `${monitor.timeout_seconds} s`],
      ["Slow threshold", `${monitor.slow_threshold_ms} ms`],
      ["Monitoring", monitor.enabled ? '<span class="badge ok">ENABLED</span>' : '<span class="badge muted">PAUSED</span>'],
      ["JSON inspection", monitor.inspect_json ? "On" : "Off"],
      ["Last checked", s.last_checked_at ? `${A.formatDate(s.last_checked_at)} (${A.timeAgo(s.last_checked_at)})` : "Never"],
      ["Next scheduled check", s.next_check_at ? A.formatDate(s.next_check_at) : (monitor.enabled ? "–" : "Paused")],
      ["Created", A.formatDate(monitor.created_at)],
    ];
    document.getElementById("config-list").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  }

  function renderStats(stats, w) {
    document.getElementById("s-uptime").textContent = A.formatPct(stats.uptime_percentage);
    document.getElementById("s-uptime-hint").textContent = stats.total_checks ? `${stats.total_checks} checks · ${WINDOW_LABEL[w]}` : "No checks in this window";
    document.getElementById("s-avg").textContent = A.formatMs(stats.avg_response_time_ms);
    document.getElementById("s-minmax").textContent = stats.min_response_time_ms != null ? `min ${A.formatMs(stats.min_response_time_ms)} · max ${A.formatMs(stats.max_response_time_ms)}` : "No responses yet";
    document.getElementById("s-success").textContent = stats.successful_checks;
    document.getElementById("s-failed").textContent = stats.failed_checks;
    document.getElementById("s-last-success").textContent = stats.last_successful_check ? A.timeAgo(stats.last_successful_check) : "N/A";
    document.getElementById("s-last-success-abs").textContent = stats.last_successful_check ? A.formatDate(stats.last_successful_check) : "No successful check in window";
    document.getElementById("window-note").textContent = stats.first_check
      ? `Showing ${WINDOW_LABEL[w]} · data since ${A.formatDate(stats.first_check)}`
      : `Showing ${WINDOW_LABEL[w]}`;
  }

  function drawChart() {
    window.APIWatchChart.renderChart(document.getElementById("detail-chart"), chartSeries, {
      threshold: monitor ? monitor.slow_threshold_ms : null,
      emptyText: "No checks in this window yet. Use “Run check” to record one.",
    });
  }

  function renderHistory(history) {
    const rows = history.results;
    document.getElementById("history-sub").textContent = `${rows.length} most recent checks`;
    const body = document.getElementById("history-rows");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">No checks recorded in this window.</td></tr>';
      return;
    }
    body.innerHTML = rows.map((r) => `<tr>
      <td class="muted">${A.formatDate(r.checked_at)}</td>
      <td>${A.statusPill(r.status)}</td>
      <td class="num">${r.status_code ?? "–"}</td>
      <td class="num ${r.status === "DEGRADED" ? "lat-slow" : ""}">${A.formatMs(r.response_time_ms)}</td>
      <td class="hide-sm muted">${r.error_category ? `<span class="badge ${r.success ? "warn" : "danger"}">${A.escapeHtml(r.error_category)}</span> ` : ""}${A.escapeHtml(r.error_message || "")}</td></tr>`).join("");
  }

  function renderIncidents(incidents) {
    const list = document.getElementById("incident-list");
    if (!incidents.length) {
      list.innerHTML = '<li><div class="grow empty" style="padding:18px 0">No incidents for this monitor.</div></li>';
      return;
    }
    list.innerHTML = incidents.map((i) => `<li><div class="grow">
      <div class="title">${i.is_open ? '<span class="badge danger">OPEN</span>' : '<span class="badge ok">RESOLVED</span>'}<span class="time">${A.formatDate(i.started_at)}${i.resolved_at ? " → " + A.formatDate(i.resolved_at) : ""}</span></div>
      <div class="detail">${A.escapeHtml(i.reason)}</div>
      <div class="time">${i.resolved_at ? "Lasted " + A.formatDuration(i.duration_seconds) : "Ongoing"}</div></div></li>`).join("");
  }

  function renderFailures(failures) {
    const list = document.getElementById("failure-list");
    if (!failures.length) {
      list.innerHTML = '<li><div class="grow empty" style="padding:18px 0">No failed checks recorded.</div></li>';
      return;
    }
    list.innerHTML = failures.map((f) => `<li><div class="grow">
      <div class="title"><span class="badge danger">${A.escapeHtml(f.error_category || "error")}</span>${f.status_code ? `<span class="badge muted">HTTP ${f.status_code}</span>` : ""}</div>
      <div class="detail">${A.escapeHtml(f.error_message || "")}</div></div>
      <span class="time" title="${A.formatDate(f.checked_at)}">${A.timeAgo(f.checked_at)}</span></li>`).join("");
  }

  function renderJson(baseline, latest) {
    const body = document.getElementById("json-body");
    const sub = document.getElementById("json-sub");
    if (!monitor.inspect_json) {
      sub.textContent = "Disabled";
      body.innerHTML = '<p class="muted" style="margin:0">JSON inspection is off. Enable “Inspect JSON” in the monitor settings to track top-level fields and types of GET responses.</p>';
      return;
    }
    if (!baseline) {
      sub.textContent = "Waiting for first JSON response";
      body.innerHTML = '<p class="muted" style="margin:0">No baseline yet. The first successful JSON object response is captured as the baseline.</p>';
      return;
    }
    const chips = Object.entries(baseline.fields).map(([k, t]) => `<span class="field-chip">${A.escapeHtml(k)}<span class="type">${A.escapeHtml(t)}</span></span>`).join("");
    let pending = "";
    if (baseline.pending_changes && baseline.pending_changes.length) {
      sub.textContent = "Changes detected";
      const LABEL = { added: ["warn", "ADDED"], removed: ["danger", "REMOVED"], type_changed: ["warn", "TYPE CHANGED"] };
      pending = `<div class="callout"><strong>${baseline.pending_changes.length} change(s) detected</strong> <span class="faint">at ${A.formatDate(baseline.pending_detected_at)}</span>
        <div style="margin-top:8px">${baseline.pending_changes.map((c) => `<div class="change-row"><span class="badge ${LABEL[c.change][0]}">${LABEL[c.change][1]}</span><code>${A.escapeHtml(c.field)}</code><span class="faint">${c.old_type ? A.escapeHtml(c.old_type) : "–"} → ${c.new_type ? A.escapeHtml(c.new_type) : "–"}</span></div>`).join("")}</div>
        <div style="margin-top:12px; display:flex; gap:10px; align-items:center; flex-wrap:wrap"><button class="btn btn-primary btn-sm" type="button" id="btn-approve">Approve as new baseline</button><span class="faint">The saved baseline only changes after approval.</span></div></div>`;
    } else if (latest && (latest.status === "DOWN" || NOT_INSPECTED.has(latest.error_category))) {
      sub.textContent = "Latest response not compared";
      const reason = latest.status === "DOWN" ? "the check failed" : (latest.error_message || latest.error_category);
      pending = `<div class="callout">The latest response could not be compared with the baseline: ${A.escapeHtml(reason)}.</div>`;
    } else {
      sub.textContent = "No changes";
      pending = '<div class="callout ok">The latest inspected response matches the approved baseline.</div>';
    }
    body.innerHTML = `${pending}<div class="faint" style="margin-bottom:8px">Approved baseline · ${A.formatDate(baseline.approved_at)}</div><div class="fields">${chips || '<span class="faint">(empty object)</span>'}</div>`;
    const approve = document.getElementById("btn-approve");
    if (approve) approve.addEventListener("click", async () => {
      approve.disabled = true;
      try {
        // Send what was reviewed: if a newer response replaced it, the server answers 409.
        await A.api(`/api/monitors/${id}/json-baseline/approve`, { method: "POST", body: { pending_fields: baseline.pending_fields } });
        A.toast("Baseline updated", "success");
      } catch (e) {
        A.toast(e.status === 409 ? `${e.message}.` : e.message, "error");
      }
      refresh();
    });
  }

  async function load() {
    const w = currentWindow; // label everything with the window this data was fetched for
    try {
      const [m, history, chart, incidents, failures, baseline] = await Promise.all([
        A.api(`/api/monitors/${id}`),
        A.api(`/api/monitors/${id}/history?window=${w}&limit=200`),
        A.api(`/api/monitors/${id}/history?window=${w}&points=400`),
        A.api(`/api/incidents?monitor_id=${id}&limit=50`),
        A.api(`/api/failures?monitor_id=${id}&limit=15`),
        A.api(`/api/monitors/${id}/json-baseline`).catch((e) => { if (e.status === 404) return null; throw e; }),
      ]);
      monitor = m;
      renderHeader();
      renderConfig();
      renderStats(history.stats, w);
      chartSeries = [{ name: monitor.name, color: "#3b82f6", points: window.APIWatchChart.pointsFromResults(chart.results) }];
      drawChart();
      renderHistory(history);
      renderIncidents(incidents);
      renderFailures(failures);
      renderJson(baseline, history.results[0]);
      A.setRefreshed();
    } catch (err) {
      if (err.status === 404) { window.location.href = "/"; return; }
      A.toast("Could not load monitor: " + err.message, "error");
    }
  }

  document.getElementById("btn-check").addEventListener("click", async (ev) => { await A.runCheck(id, ev.currentTarget); refresh(); });
  document.getElementById("btn-edit").addEventListener("click", () => { if (monitor) A.openMonitorModal(monitor, refresh); });
  document.getElementById("btn-toggle").addEventListener("click", async () => {
    if (!monitor) return;
    try { await A.setEnabled(monitor, !monitor.enabled); refresh(); } catch (e) { A.toast(e.message, "error"); }
  });
  document.getElementById("btn-delete").addEventListener("click", async () => {
    if (!monitor) return;
    try { if (await A.deleteMonitor(monitor)) window.location.href = "/"; } catch (e) { A.toast(e.message, "error"); }
  });
  A.bindWindowPicker((w) => { currentWindow = w; refresh(); });

  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawChart, 150); });

  const refresh = A.poller(load, 10000);
})();
