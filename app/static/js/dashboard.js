/* Dashboard page: summary cards, monitor table, overview chart, incidents and failures. */
(function () {
  "use strict";
  const A = window.APIWatch;
  const PALETTE = ["#3b82f6", "#a78bfa", "#22d3ee", "#f472b6", "#34d399", "#fb923c", "#facc15", "#94a3b8"];
  const WINDOW_LABEL = { "24h": "last 24 hours", "7d": "last 7 days", all: "all stored data" };
  let currentWindow = "24h";
  let monitors = [];
  let lastChart = null;
  // Chart points per monitor, refetched only when the window changed or a new check arrived.
  const chartCache = new Map();

  function renderCards(stats, w) {
    document.getElementById("card-total").textContent = stats.total_monitors;
    const extra = [];
    if (stats.paused) extra.push(`${stats.paused} paused`);
    if (stats.pending) extra.push(`${stats.pending} awaiting first check`);
    document.getElementById("card-total-hint").textContent = extra.join(" · ") || `${stats.enabled_monitors} active`;
    document.getElementById("card-up").textContent = stats.healthy;
    document.getElementById("card-degraded").textContent = stats.degraded;
    document.getElementById("card-down").textContent = stats.down;
    document.getElementById("card-down-hint").textContent = stats.open_incidents ? `${stats.open_incidents} open incident${stats.open_incidents > 1 ? "s" : ""}` : "No open incidents";
    document.getElementById("card-uptime").textContent = A.formatPct(stats.checks.uptime_percentage);
    document.getElementById("card-uptime-hint").textContent = stats.checks.total_checks
      ? `${stats.checks.successful_checks}/${stats.checks.total_checks} checks · ${WINDOW_LABEL[w]}`
      : "No checks recorded yet";
  }

  function renderTable() {
    const body = document.getElementById("monitor-rows");
    document.getElementById("monitor-count").textContent = monitors.length ? `${monitors.length} configured` : "";
    if (!monitors.length) {
      body.innerHTML = `<tr><td colspan="7"><div class="empty"><h3>No monitors yet</h3><p>Add your first API endpoint to start tracking uptime and latency.</p><button class="btn btn-primary" data-action="add-monitor" type="button">Add monitor</button></div></td></tr>`;
      return;
    }
    body.innerHTML = monitors.map((m) => {
      const s = m.summary;
      const status = m.enabled ? s.current_status : "PAUSED";
      const slow = s.last_response_time_ms != null && s.last_response_time_ms > m.slow_threshold_ms;
      const flags = [
        s.has_open_incident ? '<span class="badge danger" title="Open incident">INCIDENT</span>' : "",
        s.pending_json_changes ? '<span class="badge warn" title="JSON changes awaiting approval">JSON Δ</span>' : "",
      ].join(" ");
      return `<tr class="${m.enabled ? "" : "paused"}" data-id="${m.id}">
        <td class="name-cell"><a href="/monitors/${m.id}">${A.escapeHtml(m.name)}</a> ${flags}<div class="meta">every ${m.interval_seconds}s · expects ${m.expected_status}</div></td>
        <td class="hide-sm"><div class="endpoint"><span class="badge ${m.method === "HEAD" ? "muted" : ""}">${m.method}</span><span class="url" title="${A.escapeHtml(m.url)}">${A.escapeHtml(m.url)}</span></div></td>
        <td>${A.statusPill(status)}</td>
        <td class="num ${slow ? "lat-slow" : ""}">${A.formatMs(s.last_response_time_ms)}</td>
        <td class="hide-sm muted" title="${A.formatDate(s.last_checked_at)}">${A.timeAgo(s.last_checked_at)}</td>
        <td class="num">${A.formatPct(s.uptime_24h)}</td>
        <td><div class="actions">
          <button class="icon-btn" data-act="check" title="Run check now" aria-label="Run check now">${A.ICONS.play}</button>
          <button class="icon-btn" data-act="edit" title="Edit" aria-label="Edit">${A.ICONS.edit}</button>
          <label class="switch" title="${m.enabled ? "Disable" : "Enable"} monitoring"><input type="checkbox" data-act="toggle" ${m.enabled ? "checked" : ""} aria-label="Enable monitoring"><span class="track"></span></label>
          <button class="icon-btn danger" data-act="delete" title="Delete" aria-label="Delete">${A.ICONS.trash}</button>
        </div></td>
      </tr>`;
    }).join("");
  }

  async function chartPoints(monitor, w) {
    const key = `${w}|${monitor.summary.last_checked_at}`;
    const cached = chartCache.get(monitor.id);
    if (cached && cached.key === key) return cached.points;
    const history = await A.api(`/api/monitors/${monitor.id}/history?window=${w}&points=240`);
    const points = window.APIWatchChart.pointsFromResults(history.results);
    chartCache.set(monitor.id, { key, points });
    return points;
  }

  async function renderChart(w) {
    const container = document.getElementById("overview-chart");
    const legend = document.getElementById("overview-legend");
    const active = monitors.filter((m) => m.summary.last_checked_at);
    for (const id of chartCache.keys()) {
      if (!monitors.some((m) => m.id === id)) chartCache.delete(id);
    }
    if (!active.length) {
      lastChart = [[], { emptyText: "Response times appear here after the first checks run." }];
      window.APIWatchChart.renderChart(container, ...lastChart);
      legend.innerHTML = "";
      return;
    }
    const pointLists = await Promise.all(active.map((m) => chartPoints(m, w)));
    const series = active.map((m, i) => ({
      name: m.name,
      color: PALETTE[i % PALETTE.length],
      points: pointLists[i],
    })).filter((s) => s.points.length);
    lastChart = [series, { emptyText: `No checks in the ${WINDOW_LABEL[w]}.` }];
    window.APIWatchChart.renderChart(container, ...lastChart);
    legend.innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${A.escapeHtml(s.name)}</span>`).join("");
  }

  function renderIncidents(incidents, openCount) {
    const list = document.getElementById("incident-list");
    // The list is a page of the most recent incidents; the open count comes from /api/stats.
    document.getElementById("incident-sub").textContent = incidents.length
      ? `${openCount} open · latest ${incidents.length} shown`
      : "";
    if (!incidents.length) {
      list.innerHTML = `<li><div class="grow empty" style="padding:18px 0">No incidents recorded. Incidents open after ${document.getElementById("incident-sub").dataset.threshold} consecutive DOWN checks.</div></li>`;
      return;
    }
    list.innerHTML = incidents.map((i) => `<li>
      <div class="grow">
        <div class="title"><a href="/monitors/${i.monitor_id}">${A.escapeHtml(i.monitor_name)}</a>${i.is_open ? '<span class="badge danger">OPEN</span>' : '<span class="badge ok">RESOLVED</span>'}</div>
        <div class="detail">${A.escapeHtml(i.reason)}</div>
        <div class="time">${A.formatDate(i.started_at)}${i.resolved_at ? " → " + A.formatDate(i.resolved_at) + " · " + A.formatDuration(i.duration_seconds) : " · ongoing"}</div>
      </div></li>`).join("");
  }

  function renderFailures(failures) {
    const body = document.getElementById("failure-rows");
    if (!failures.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">No failed checks recorded.</td></tr>';
      return;
    }
    const names = Object.fromEntries(monitors.map((m) => [m.id, m.name]));
    body.innerHTML = failures.map((f) => `<tr>
      <td class="muted" title="${A.formatDate(f.checked_at)}">${A.timeAgo(f.checked_at)}</td>
      <td><a href="/monitors/${f.monitor_id}">${A.escapeHtml(names[f.monitor_id] || "#" + f.monitor_id)}</a></td>
      <td><span class="badge danger">${A.escapeHtml(f.error_category || "error")}</span></td>
      <td class="muted">${A.escapeHtml(f.error_message || "")}</td>
      <td class="num">${f.status_code ?? "–"}</td></tr>`).join("");
  }

  async function load() {
    const w = currentWindow; // label everything with the window this data was fetched for
    try {
      const [stats, list, incidents, failures] = await Promise.all([
        A.api(`/api/stats?window=${w}`),
        A.api("/api/monitors"),
        A.api("/api/incidents?limit=20"),
        A.api("/api/failures?limit=10"),
      ]);
      monitors = list;
      renderCards(stats, w);
      renderTable();
      renderIncidents(incidents, stats.open_incidents);
      renderFailures(failures);
      await renderChart(w);
      A.setRefreshed();
    } catch (err) {
      A.toast("Could not load dashboard data: " + err.message, "error");
    }
  }

  document.getElementById("monitor-rows").addEventListener("click", async (ev) => {
    const target = ev.target.closest("[data-act]");
    if (!target) return;
    const row = target.closest("tr");
    const monitor = monitors.find((m) => String(m.id) === row.dataset.id);
    if (!monitor) return;
    const act = target.dataset.act;
    if (act === "check") { await A.runCheck(monitor.id, target); refresh(); }
    else if (act === "edit") A.openMonitorModal(monitor, refresh);
    else if (act === "delete") { try { if (await A.deleteMonitor(monitor)) refresh(); } catch (e) { A.toast(e.message, "error"); } }
  });
  document.getElementById("monitor-rows").addEventListener("change", async (ev) => {
    const target = ev.target.closest("[data-act=toggle]");
    if (!target) return;
    const monitor = monitors.find((m) => String(m.id) === target.closest("tr").dataset.id);
    try { await A.setEnabled(monitor, target.checked); } catch (e) { A.toast(e.message, "error"); target.checked = !target.checked; }
    refresh();
  });
  A.hooks.onMonitorCreated = () => refresh();
  A.bindWindowPicker((w) => { currentWindow = w; refresh(); });

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (lastChart) window.APIWatchChart.renderChart(document.getElementById("overview-chart"), ...lastChart);
    }, 150);
  });

  const refresh = A.poller(load, 10000);
})();
