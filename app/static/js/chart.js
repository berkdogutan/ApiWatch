/* Minimal dependency-free SVG time-series chart for APIWatch. */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceMax(value) {
    if (value <= 0) return 100;
    const exp = Math.pow(10, Math.floor(Math.log10(value)));
    const f = value / exp;
    const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return nice * exp;
  }

  function formatMs(v) {
    return v >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + " s" : Math.round(v) + " ms";
  }

  function formatTick(date, spanMs) {
    const opts = spanMs > 2 * 86400000
      ? { month: "short", day: "numeric" }
      : { hour: "2-digit", minute: "2-digit" };
    return date.toLocaleString([], opts);
  }

  /**
   * Render a line chart.
   * series: [{ name, color, points: [{ t: Date, v: number|null, status }] }]
   * options: { threshold, emptyText }
   */
  function renderChart(container, series, options) {
    options = options || {};
    container.innerHTML = "";
    const all = series.flatMap((s) => s.points);
    const values = all.filter((p) => p.v != null);
    if (!all.length) {
      container.innerHTML = '<div class="chart-empty">' + (options.emptyText || "No data yet") + "</div>";
      return;
    }

    const width = Math.max(container.clientWidth, 280);
    const height = container.clientHeight || 280;
    const m = { top: 12, right: 14, bottom: 26, left: 52 };
    const iw = width - m.left - m.right;
    const ih = height - m.top - m.bottom;

    let tMin = Math.min(...all.map((p) => +p.t));
    let tMax = Math.max(...all.map((p) => +p.t));
    if (tMax - tMin < 60000) { tMin -= 30000; tMax += 30000; }
    const vMaxRaw = Math.max(0, ...values.map((p) => p.v), options.threshold ? options.threshold * 1.15 : 0);
    const vMax = niceMax(vMaxRaw * 1.08 || 100);

    const x = (t) => m.left + ((+t - tMin) / (tMax - tMin)) * iw;
    const y = (v) => m.top + ih - (v / vMax) * ih;

    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Response time chart" }, container);

    // Y grid + labels
    for (let i = 0; i <= 4; i++) {
      const v = (vMax / 4) * i;
      const yy = y(v);
      el("line", { x1: m.left, x2: width - m.right, y1: yy, y2: yy, class: "grid-line" }, svg);
      const label = el("text", { x: m.left - 8, y: yy + 4, "text-anchor": "end", class: "axis-label" }, svg);
      label.textContent = formatMs(v);
    }
    // X labels
    const ticks = Math.max(2, Math.min(6, Math.floor(iw / 110)));
    for (let i = 0; i <= ticks; i++) {
      const t = tMin + ((tMax - tMin) / ticks) * i;
      const label = el("text", { x: x(t), y: height - 6, "text-anchor": i === 0 ? "start" : i === ticks ? "end" : "middle", class: "axis-label" }, svg);
      label.textContent = formatTick(new Date(t), tMax - tMin);
    }

    if (options.threshold) {
      const ty = y(options.threshold);
      el("line", { x1: m.left, x2: width - m.right, y1: ty, y2: ty, class: "threshold" }, svg);
      const tl = el("text", { x: width - m.right, y: ty - 5, "text-anchor": "end", class: "threshold-label" }, svg);
      tl.textContent = "slow > " + formatMs(options.threshold);
    }

    // Lines (gaps where v is null, i.e. DOWN without a response)
    for (const s of series) {
      const pts = s.points.slice().sort((a, b) => a.t - b.t);
      let d = "";
      let pen = false;
      for (const p of pts) {
        if (p.v == null) { pen = false; continue; }
        d += (pen ? "L" : "M") + x(p.t).toFixed(1) + " " + y(p.v).toFixed(1) + " ";
        pen = true;
      }
      if (d) {
        if (series.length === 1) {
          const area = d + `L ${x(pts[pts.length - 1].t)} ${y(0)} L ${x(pts.find((p) => p.v != null).t)} ${y(0)} Z`;
          const gid = "g" + Math.random().toString(36).slice(2);
          const defs = el("defs", {}, svg);
          const grad = el("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
          el("stop", { offset: "0%", "stop-color": s.color, "stop-opacity": 0.28 }, grad);
          el("stop", { offset: "100%", "stop-color": s.color, "stop-opacity": 0 }, grad);
          if (pts.every((p) => p.v != null)) el("path", { d: area, fill: `url(#${gid})` }, svg);
        }
        el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      }
      for (const p of pts) {
        if (p.status === "DOWN") {
          el("line", { x1: x(p.t), x2: x(p.t), y1: y(0), y2: y(0) - 10, stroke: "#ef4444", "stroke-width": 2, "stroke-linecap": "round" }, svg);
        } else if (p.status === "DEGRADED" && p.v != null && pts.length <= 300) {
          el("circle", { cx: x(p.t), cy: y(p.v), r: 2.8, fill: "#f59e0b" }, svg);
        }
      }
    }

    // Hover tooltip
    const hover = el("line", { y1: m.top, y2: m.top + ih, class: "hover-line", visibility: "hidden" }, svg);
    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.hidden = true;
    container.appendChild(tip);
    const overlay = el("rect", { x: m.left, y: m.top, width: iw, height: ih, fill: "transparent" }, svg);

    overlay.addEventListener("mousemove", (ev) => {
      const rect = svg.getBoundingClientRect();
      const px = ((ev.clientX - rect.left) / rect.width) * width;
      const t = tMin + ((px - m.left) / iw) * (tMax - tMin);
      let best = null;
      for (const s of series) {
        for (const p of s.points) {
          const dist = Math.abs(p.t - t);
          if (!best || dist < best.dist) best = { dist, p, s };
        }
      }
      if (!best) return;
      const bx = x(best.p.t);
      hover.setAttribute("x1", bx); hover.setAttribute("x2", bx); hover.setAttribute("visibility", "visible");
      const rows = series.map((s) => {
        const near = s.points.reduce((acc, p) => (!acc || Math.abs(p.t - best.p.t) < Math.abs(acc.t - best.p.t) ? p : acc), null);
        if (!near || Math.abs(near.t - best.p.t) > (tMax - tMin) / 40) return "";
        const val = near.v == null ? '<span style="color:#ef4444">DOWN</span>' : formatMs(near.v);
        return `<div class="row"><span><span class="sw" style="background:${s.color}"></span>${escapeHtml(s.name)}</span><strong>${val}</strong></div>`;
      }).join("");
      tip.innerHTML = `<div class="t">${best.p.t.toLocaleString()}</div>${rows}`;
      tip.hidden = false;
      const left = (bx / width) * rect.width;
      tip.style.left = Math.min(Math.max(left + 12, 0), rect.width - tip.offsetWidth) + "px";
      tip.style.top = "8px";
    });
    overlay.addEventListener("mouseleave", () => { tip.hidden = true; hover.setAttribute("visibility", "hidden"); });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  window.APIWatchChart = { renderChart, formatMs };
})();
