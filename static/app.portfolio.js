/**
 * app.portfolio.js — Portfolio / Research section for the hedge-fund dashboard.
 *
 * Renders inside div#hf-section-portfolio and provides:
 *   1. Strategy Comparison table  (sortable, colour-coded)
 *   2. Monte Carlo Simulator      (equity spaghetti chart + histogram + stats)
 *   3. Walk-Forward Analysis      (in-sample vs out-of-sample robustness)
 *   4. Equity Curve Chart         (cumulative P&L across all symbols)
 *
 * Vanilla JS – no frameworks.  Exposes window.Portfolio via IIFE.
 */

const Portfolio = (function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /*  Helpers                                                           */
  /* ------------------------------------------------------------------ */

  /** Fetch helper that attaches the X-Auth-Token from the <meta> tag. */
  async function apiFetch(url, opts = {}) {
    const token =
      document.querySelector('meta[name="auth-token"]')?.content || "";
    const headers = { "X-Auth-Token": token, ...(opts.headers || {}) };
    const res = await fetch(url, { ...opts, headers });
    if (!res.ok) throw new Error(`HTTP ${res.status} – ${res.statusText}`);
    return res.json();
  }

  /** Shorthand: get the root container or create it if missing. */
  function root() {
    let el = document.getElementById("hf-section-portfolio");
    if (!el) {
      el = document.createElement("div");
      el.id = "hf-section-portfolio";
      document.body.appendChild(el);
    }
    return el;
  }

  /** Set a container's innerHTML to a loading spinner. */
  function showLoading(container, label = "Loading…") {
    container.innerHTML = `<p class="dim small">${label}</p>`;
  }

  /** Set a container's innerHTML to an error message. */
  function showError(container, msg) {
    container.innerHTML = `<p class="loss small">⚠ ${msg}</p>`;
  }

  /** Format a number with commas and optional decimal places. */
  function fmt(n, dec = 2) {
    if (n == null || isNaN(n)) return "–";
    return Number(n).toLocaleString(undefined, {
      minimumFractionDigits: dec,
      maximumFractionDigits: dec,
    });
  }

  /** Return a CSS class based on profit-factor value. */
  function pfClass(pf) {
    if (pf > 1.5) return "profit";
    if (pf > 1.0) return "small"; // neutral – just dimmed slightly
    return "loss";
  }

  /** Return a CSS class for percentage values (profit / loss). */
  function pnlClass(v) {
    return v >= 0 ? "profit" : "loss";
  }

  /* ------------------------------------------------------------------ */
  /*  Section 1 – Strategy Comparison                                   */
  /* ------------------------------------------------------------------ */

  let strategyData = []; // cached rows for sorting
  let strategySortCol = null;
  let strategySortAsc = true;

  function renderStrategyTable(container) {
    if (!strategyData.length) {
      container.innerHTML = `<p class="dim small">No backtest data available.</p>`;
      return;
    }

    const cols = [
      { key: "symbol", label: "Symbol" },
      { key: "trades", label: "Trades" },
      { key: "win_pct", label: "Win%" },
      { key: "profit_factor", label: "PF" },
      { key: "return_pct", label: "Return%" },
      { key: "max_dd_pct", label: "MaxDD%" },
      { key: "expectancy", label: "Expectancy" },
    ];

    // Sort data if a column is active
    const sorted = [...strategyData];
    if (strategySortCol) {
      sorted.sort((a, b) => {
        const va = a[strategySortCol] ?? 0;
        const vb = b[strategySortCol] ?? 0;
        if (typeof va === "string") return strategySortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        return strategySortAsc ? va - vb : vb - va;
      });
    }

    let html = `<table class="table">
      <thead><tr>`;
    cols.forEach((c) => {
      const arrow = strategySortCol === c.key ? (strategySortAsc ? " ▲" : " ▼") : "";
      html += `<th class="sortable" data-col="${c.key}">${c.label}${arrow}</th>`;
    });
    html += `</tr></thead><tbody>`;

    sorted.forEach((r) => {
      html += `<tr>
        <td>${r.symbol}</td>
        <td>${fmt(r.trades, 0)}</td>
        <td>${fmt(r.win_pct, 1)}%</td>
        <td class="${pfClass(r.profit_factor)}">${fmt(r.profit_factor)}</td>
        <td class="${pnlClass(r.return_pct)}">${fmt(r.return_pct, 1)}%</td>
        <td class="loss">${fmt(r.max_dd_pct, 1)}%</td>
        <td class="${pnlClass(r.expectancy)}">${fmt(r.expectancy)}</td>
      </tr>`;
    });

    html += `</tbody></table>`;
    container.innerHTML = html;

    // Bind header click for sorting
    container.querySelectorAll("th.sortable").forEach((th) => {
      th.style.cursor = "pointer";
      th.addEventListener("click", () => {
        const col = th.dataset.col;
        if (strategySortCol === col) {
          strategySortAsc = !strategySortAsc;
        } else {
          strategySortCol = col;
          strategySortAsc = true;
        }
        renderStrategyTable(container);
      });
    });
  }

  async function loadStrategyComparison() {
    const panel = document.getElementById("strategy-table-body");
    if (!panel) return;
    showLoading(panel, "Loading backtest summary…");
    try {
      const data = await apiFetch("/api/backtests");
      // API returns {groups: {month: [...]}} or {results: [...]}
      if (data.groups) {
        const all = [];
        Object.values(data.groups).forEach(arr => { if (Array.isArray(arr)) all.push(...arr); });
        strategyData = all.map(r => ({
          symbol: r.symbol,
          trades: r.trades,
          win_pct: r.win_rate,
          profit_factor: r.profit_factor,
          return_pct: r.total_return_pct,
          max_dd_pct: r.max_drawdown_pct,
          expectancy: r.expectancy,
        }));
      } else {
        strategyData = data.results || data || [];
      }
      renderStrategyTable(panel);
    } catch (e) {
      showError(panel, "Failed to load backtest summary: " + e.message);
    }
  }

  /* ------------------------------------------------------------------ */
  /*  Section 2 – Monte Carlo Simulator                                 */
  /* ------------------------------------------------------------------ */

  let mcSimCount = 1000;

  /** Draw a simple line chart (spaghetti) on a canvas. */
  function drawSpaghetti(canvas, curves) {
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    const pad = { top: 20, right: 10, bottom: 30, left: 60 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    if (!curves.length) return;

    // Find global min/max across all curves
    let yMin = Infinity,
      yMax = -Infinity;
    let xLen = 0;
    curves.forEach((c) => {
      if (c.length > xLen) xLen = c.length;
      c.forEach((v) => {
        if (v < yMin) yMin = v;
        if (v > yMax) yMax = v;
      });
    });

    // Add 5% padding
    const yRange = yMax - yMin || 1;
    yMin -= yRange * 0.05;
    yMax += yRange * 0.05;

    function toX(i) {
      return pad.left + (i / (xLen - 1 || 1)) * plotW;
    }
    function toY(v) {
      return pad.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
    }

    // Draw axes
    ctx.strokeStyle = "#444";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, pad.top + plotH);
    ctx.lineTo(pad.left + plotW, pad.top + plotH);
    ctx.stroke();

    // Y-axis labels
    ctx.fillStyle = "#888";
    ctx.font = "10px monospace";
    ctx.textAlign = "right";
    const yTicks = 5;
    for (let i = 0; i <= yTicks; i++) {
      const val = yMin + (i / yTicks) * (yMax - yMin);
      const y = toY(val);
      ctx.fillText(fmt(val, 0), pad.left - 4, y + 3);
      ctx.beginPath();
      ctx.strokeStyle = "#333";
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();
    }

    // Draw each equity curve with low opacity
    curves.forEach((curve, idx) => {
      ctx.beginPath();
      ctx.strokeStyle =
        idx === 0
          ? "#4fc3f7" // highlight median curve
          : "rgba(100,181,246,0.08)";
      ctx.lineWidth = idx === 0 ? 2 : 0.6;
      curve.forEach((v, i) => {
        const x = toX(i);
        const y = toY(v);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  /** Draw a histogram on a canvas. */
  function drawHistogram(canvas, values, bins = 30) {
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    const pad = { top: 20, right: 10, bottom: 30, left: 60 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);
    if (!values.length) return;

    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const step = range / bins;

    // Bucket counts
    const counts = new Array(bins).fill(0);
    values.forEach((v) => {
      let b = Math.floor((v - min) / step);
      if (b >= bins) b = bins - 1;
      counts[b]++;
    });
    const maxCount = Math.max(...counts) || 1;

    // Draw bars
    const barW = plotW / bins;
    counts.forEach((c, i) => {
      const barH = (c / maxCount) * plotH;
      const x = pad.left + i * barW;
      const y = pad.top + plotH - barH;
      ctx.fillStyle = min + i * step >= 0 ? "rgba(102,187,106,0.6)" : "rgba(239,83,80,0.6)";
      ctx.fillRect(x, y, barW - 1, barH);
    });

    // X-axis labels
    ctx.fillStyle = "#888";
    ctx.font = "10px monospace";
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) {
      const val = min + (i / 4) * range;
      ctx.fillText(fmt(val, 1), pad.left + (i / 4) * plotW, pad.top + plotH + 14);
    }

    // Y-axis
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const val = (i / 4) * maxCount;
      const y = pad.top + plotH - (i / 4) * plotH;
      ctx.fillText(fmt(val, 0), pad.left - 4, y + 3);
    }
  }

  async function runMonteCarlo() {
    const symbol = document.getElementById("mc-symbol")?.value;
    const sims = parseInt(document.getElementById("mc-sims")?.value, 10) || 1000;
    mcSimCount = sims;

    const status = document.getElementById("mc-status");
    const curveCanvas = document.getElementById("mc-curve-canvas");
    const histCanvas = document.getElementById("mc-hist-canvas");
    const statsDiv = document.getElementById("mc-stats");

    if (!symbol) {
      if (status) status.textContent = "Select a symbol first.";
      return;
    }

    if (status) status.textContent = "Running Monte Carlo simulation…";

    try {
      const data = await apiFetch(
        `/api/research/monte-carlo?symbol=${encodeURIComponent(symbol)}&simulations=${sims}`
      );

      if (status) status.textContent = "";

      const curves = data.curves || [];
      const finals = data.finals || data.final_balances || data.finalBalances || [];
      // API returns flat fields: p_positive, avg_return, etc. or nested in stats
      const stats = data.stats || data;

      // Draw spaghetti chart
      if (curveCanvas) {
        curveCanvas.width = curveCanvas.parentElement.clientWidth || 700;
        curveCanvas.height = 300;
        drawSpaghetti(curveCanvas, curves);
      }

      // Draw histogram
      if (histCanvas) {
        histCanvas.width = histCanvas.parentElement.clientWidth || 700;
        histCanvas.height = 220;
        drawHistogram(histCanvas, finals);
      }

      // Stats - handle both ratio (0-1) and percent (0-100) formats
      if (statsDiv) {
        let pPos = stats.p_positive ?? stats.pPositive ?? 0;
        let pBlown = stats.p_blown ?? stats.pBlown ?? 0;
        const avgRet = stats.avg_return ?? stats.avgReturn ?? 0;
        const worst = stats.worst_case ?? stats.worstCase ?? 0;
        // Convert ratio to percent if needed
        const toPct = v => (v > 1 ? v : v * 100);
        // But if already percent (e.g. 77.6) keep, if ratio (0.77) convert
        const pPosPct = pPos > 1.01 ? pPos : pPos * 100;
        const pBlownPct = pBlown > 1.01 ? pBlown : pBlown * 100;
        statsDiv.innerHTML = `
          <div class="stat-grid">
            <div class="stat">
              <span class="stat-label dim small">P(positive)</span>
              <span class="stat-value ${pPosPct >= 50 ? 'profit' : 'loss'}">${fmt(pPosPct, 1)}%</span>
            </div>
            <div class="stat">
              <span class="stat-label dim small">P(account blown)</span>
              <span class="stat-value loss">${fmt(pBlownPct, 1)}%</span>
            </div>
            <div class="stat">
              <span class="stat-label dim small">Avg return</span>
              <span class="stat-value ${avgRet >= 0 ? 'profit' : 'loss'}">${fmt(avgRet, 1)}%</span>
            </div>
            <div class="stat">
              <span class="stat-label dim small">Worst case</span>
              <span class="stat-value loss">${fmt(worst, 1)}%</span>
            </div>
          </div>`;
      }
    } catch (e) {
      if (status) status.textContent = "Error: " + e.message;
    }
  }

  /* ------------------------------------------------------------------ */
  /*  Section 3 – Walk-Forward Analysis                                 */
  /* ------------------------------------------------------------------ */

  async function runWalkForward() {
    const symbol = document.getElementById("wf-symbol")?.value;
    const resultDiv = document.getElementById("wf-result");

    if (!symbol) {
      if (resultDiv) resultDiv.innerHTML = `<p class="dim small">Select a symbol first.</p>`;
      return;
    }

    showLoading(resultDiv, "Running walk-forward analysis…");

    try {
      const data = await apiFetch("/api/backtest/walk-forward", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol }),
      });

      const inSample = data.in_sample || {};
      const outSample = data.out_of_sample || {};
      const efficiency = data.efficiency_ratio ?? data.efficiencyRatio ?? null;
      const robust = data.is_robust ?? data.isRobust ?? null;

      let html = `<div class="wf-grid">
        <div class="wf-card">
          <h4 class="small dim">In-Sample (Training)</h4>
          <table class="table table-sm">
            <tr><td>Trades</td><td>${fmt(inSample.trades, 0)}</td></tr>
            <tr><td>Win%</td><td>${fmt(inSample.win_pct, 1)}%</td></tr>
            <tr><td>PF</td><td class="${pfClass(inSample.profit_factor)}">${fmt(inSample.profit_factor)}</td></tr>
            <tr><td>Return</td><td class="${pnlClass(inSample.return_pct)}">${fmt(inSample.return_pct, 1)}%</td></tr>
            <tr><td>Max DD</td><td class="loss">${fmt(inSample.max_dd_pct, 1)}%</td></tr>
          </table>
        </div>
        <div class="wf-card">
          <h4 class="small dim">Out-of-Sample (Validation)</h4>
          <table class="table table-sm">
            <tr><td>Trades</td><td>${fmt(outSample.trades, 0)}</td></tr>
            <tr><td>Win%</td><td>${fmt(outSample.win_pct, 1)}%</td></tr>
            <tr><td>PF</td><td class="${pfClass(outSample.profit_factor)}">${fmt(outSample.profit_factor)}</td></tr>
            <tr><td>Return</td><td class="${pnlClass(outSample.return_pct)}">${fmt(outSample.return_pct, 1)}%</td></tr>
            <tr><td>Max DD</td><td class="loss">${fmt(outSample.max_dd_pct, 1)}%</td></tr>
          </table>
        </div>
      </div>`;

      // Efficiency / robustness summary
      const effClass = efficiency != null && efficiency >= 0.5 ? "profit" : "loss";
      const robustLabel = robust === true || robust === "true"
        ? '<span class="profit">✓ Robust</span>'
        : robust === false || robust === "false"
          ? '<span class="loss">✗ Not Robust</span>'
          : '<span class="dim">–</span>';

      html += `
        <div class="wf-summary" style="margin-top:12px;">
          <span class="small dim">Efficiency Ratio: </span>
          <span class="${effClass} small"><strong>${efficiency != null ? fmt(efficiency * 100, 1) + '%' : '–'}</strong></span>
          <span class="small dim" style="margin-left:20px;">Verdict: </span>
          ${robustLabel}
        </div>`;

      resultDiv.innerHTML = html;
    } catch (e) {
      showError(resultDiv, "Walk-forward failed: " + e.message);
    }
  }

  /* ------------------------------------------------------------------ */
  /*  Section 4 – Equity Curve Chart                                    */
  /* ------------------------------------------------------------------ */

  function drawEquityCurve(canvas, equityData) {
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    const pad = { top: 20, right: 20, bottom: 40, left: 70 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);
    if (!equityData.length) return;

    const values = equityData.map((d) => d.equity ?? d.balance ?? d.value ?? 0);
    let yMin = Math.min(...values);
    let yMax = Math.max(...values);

    // Start from 0 if close
    if (yMin > 0 && yMin < (yMax - yMin) * 0.2) yMin = 0;

    const yRange = yMax - yMin || 1;
    yMin -= yRange * 0.05;
    yMax += yRange * 0.05;

    function toX(i) {
      return pad.left + (i / (values.length - 1 || 1)) * plotW;
    }
    function toY(v) {
      return pad.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
    }

    // Grid lines
    ctx.strokeStyle = "#333";
    ctx.lineWidth = 0.5;
    const yTicks = 6;
    for (let i = 0; i <= yTicks; i++) {
      const val = yMin + (i / yTicks) * (yMax - yMin);
      const y = toY(val);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();

      ctx.fillStyle = "#888";
      ctx.font = "10px monospace";
      ctx.textAlign = "right";
      ctx.fillText(fmt(val, 0), pad.left - 6, y + 3);
    }

    // X-axis date labels
    ctx.textAlign = "center";
    const labelStep = Math.max(1, Math.floor(values.length / 8));
    equityData.forEach((d, i) => {
      if (i % labelStep === 0 || i === values.length - 1) {
        const lbl = d.date ?? d.time ?? "";
        const short = typeof lbl === "string" ? lbl.slice(5, 10) : lbl;
        ctx.fillText(short, toX(i), pad.top + plotH + 16);
      }
    });

    // Fill area under curve
    ctx.beginPath();
    ctx.moveTo(toX(0), toY(values[0]));
    values.forEach((v, i) => ctx.lineTo(toX(i), toY(v)));
    ctx.lineTo(toX(values.length - 1), pad.top + plotH);
    ctx.lineTo(toX(0), pad.top + plotH);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
    grad.addColorStop(0, "rgba(102,187,106,0.25)");
    grad.addColorStop(1, "rgba(102,187,106,0.02)");
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.beginPath();
    ctx.strokeStyle = "#66bb6a";
    ctx.lineWidth = 2;
    values.forEach((v, i) => {
      const x = toX(i);
      const y = toY(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  async function loadEquityCurve() {
    const canvas = document.getElementById("equity-curve-canvas");
    const info = document.getElementById("equity-curve-info");
    if (!canvas) return;

    canvas.width = canvas.parentElement.clientWidth || 800;
    canvas.height = 320;

    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#888";
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Loading equity curve…", canvas.width / 2, canvas.height / 2);

    try {
      const data = await apiFetch("/api/journal/equity-curve");
      let points = data.points || data.equity || data.curve || data || [];
      // API returns points with {cumulative, time} -> convert to {equity, date}
      if (points.length && points[0].cumulative !== undefined) {
        const base = 10000;
        points = points.map(p => ({ equity: base + (p.cumulative ?? 0), date: (p.time || '').slice(0,10), balance: base + (p.cumulative ?? 0) }));
      }
      drawEquityCurve(canvas, points);

      if (info && points.length) {
        const last = points[points.length - 1].equity ?? points[points.length - 1].balance ?? 0;
        const first = points[0].equity ?? points[0].balance ?? 0;
        const total = last - first;
        // Also try net_pnl from API if available
        const totalPnl = data.net_pnl ?? total;
        info.innerHTML = `
          <span class="small dim">Current equity: </span>
          <span class="small ${pnlClass(total)}"><strong>$${fmt(last)}</strong></span>
          <span class="small dim" style="margin-left:16px;">Total P&L: </span>
          <span class="small ${pnlClass(totalPnl)}"><strong>${totalPnl >= 0 ? '+' : ''}$${fmt(totalPnl)}</strong></span>`;
      }
    } catch (e) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      showError(canvas.parentElement, "Failed to load equity curve: " + e.message);
    }
  }

  /* ------------------------------------------------------------------ */
  /*  Render full section                                               */
  /* ------------------------------------------------------------------ */

  function render() {
    const el = root();
    el.innerHTML = `
      <!-- Strategy Comparison -->
      <div class="panel">
        <div class="panel-title">Strategy Comparison</div>
        <div id="strategy-table-body" class="small">
          <p class="dim">Loading…</p>
        </div>
      </div>

      <!-- Monte Carlo Simulator -->
      <div class="panel">
        <div class="panel-title">Monte Carlo Simulator</div>
        <div class="mc-controls" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
          <select id="mc-symbol" class="input-small">
            <option value="">– select symbol –</option>
          </select>
          <label class="small dim">
            Simulations
            <input id="mc-sims" type="number" value="1000" min="100" max="10000" step="100"
                   class="input-small" style="width:80px;margin-left:4px;">
          </label>
          <button id="mc-run" class="btn btn-sm">Run</button>
          <span id="mc-status" class="small dim"></span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div>
            <h4 class="small dim">Equity Curves</h4>
            <canvas id="mc-curve-canvas" style="width:100%;background:#111;border-radius:4px;"></canvas>
          </div>
          <div>
            <h4 class="small dim">Final Balance Distribution</h4>
            <canvas id="mc-hist-canvas" style="width:100%;background:#111;border-radius:4px;"></canvas>
          </div>
        </div>
        <div id="mc-stats" style="margin-top:12px;"></div>
      </div>

      <!-- Walk-Forward Analysis -->
      <div class="panel">
        <div class="panel-title">Walk-Forward Analysis</div>
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;">
          <select id="wf-symbol" class="input-small">
            <option value="">– select symbol –</option>
          </select>
          <button id="wf-run" class="btn btn-sm">Run Analysis</button>
        </div>
        <div id="wf-result" class="small dim">Select a symbol and run the analysis.</div>
      </div>

      <!-- Equity Curve -->
      <div class="panel">
        <div class="panel-title">Equity Curve – All Symbols</div>
        <div id="equity-curve-info" style="margin-bottom:8px;"></div>
        <canvas id="equity-curve-canvas" style="width:100%;background:#111;border-radius:4px;"></canvas>
      </div>`;

    // Bind events
    document.getElementById("mc-run")?.addEventListener("click", runMonteCarlo);
    document.getElementById("wf-run")?.addEventListener("click", runWalkForward);

    // Populate symbol dropdowns
    populateSymbolDropdowns();

    // Load async data
    loadStrategyComparison();
    loadEquityCurve();
  }

  /* ------------------------------------------------------------------ */
  /*  Populate symbol <select> elements from the available symbols API  */
  /* ------------------------------------------------------------------ */

  async function populateSymbolDropdowns() {
    try {
      const data = await apiFetch("/api/symbols");
      const symbols = data.symbols || data || [];
      const options = symbols.map((s) => {
        const name = typeof s === "string" ? s : s.name || s.symbol || "";
        return `<option value="${name}">${name}</option>`;
      }).join("");

      const placeholder = `<option value="">– select symbol –</option>`;
      const mcSel = document.getElementById("mc-symbol");
      const wfSel = document.getElementById("wf-symbol");
      if (mcSel) mcSel.innerHTML = placeholder + options;
      if (wfSel) wfSel.innerHTML = placeholder + options;
    } catch (e) {
      // Dropdowns remain with placeholder – not critical
      console.warn("Could not load symbols for dropdowns:", e.message);
    }
  }

  /* ------------------------------------------------------------------ */
  /*  Public API                                                        */
  /* ------------------------------------------------------------------ */

  return {
    /** One-time setup: render the section into the DOM. */
    init() {
      render();
    },

    /** Re-render (e.g. after tab switch). */
    render() {
      render();
    },

    /** Expose internals for testing / external use. */
    _loadStrategy: loadStrategyComparison,
    _loadEquity: loadEquityCurve,
    _runMC: runMonteCarlo,
    _runWF: runWalkForward,
  };
})();

// Auto-initialise when the DOM is ready.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => Portfolio.init());
} else {
  Portfolio.init();
}
