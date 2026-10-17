/**
 * app.risk.js — Risk / Compliance section for the hedge-fund dashboard.
 *
 * Exposes window.Risk with init() and render().
 * Renders into div#hf-section-risk.
 *
 * Sub-sections:
 *   1. Compliance Dashboard  (gauge, daily-loss %, drawdown %, trades remaining)
 *   2. Position Sizing Calculator (symbol, risk%, SL pips → lot size)
 *   3. Correlation Matrix     (heatmap grid across major pairs)
 *   4. Open Risk Monitor      (table of open positions with risk metrics)
 *   5. Daily / Weekly Loss Tracker (canvas bar chart – last 7 days)
 *   6. Halt Controls          (pause / resume trading)
 */
window.Risk = (function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /*  Helpers                                                            */
  /* ------------------------------------------------------------------ */

  const _noOpHandler = { get(_, prop) { if (prop === Symbol.toPrimitive) return () => ""; if (prop === "classList") return { add() {}, remove() {}, toggle() {}, contains() { return false; } }; if (prop === "dataset") return {}; if (prop === "style") return {}; if (prop === "getBoundingClientRect") return () => ({ width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0 }); if (prop === "getAttribute") return () => ""; if (prop === "hasAttribute") return () => false; if (prop === "querySelector") return () => null; if (prop === "querySelectorAll") return () => []; if (prop === "closest") return () => null; if (typeof prop === "string") { if (prop.startsWith("on")) return () => {}; if (/^(addEventListener|removeEventListener|click|focus|blur|appendChild|append|prepend|before|after|remove|replaceWith|insertBefore|replaceChild|removeChild|cloneNode|setAttribute|removeAttribute|toggleAttribute|scrollIntoView|scrollTo|scrollBy|dispatchEvent)$/.test(prop)) return () => {}; return ""; } return undefined; }, set() { return true; } };
  const _safeEl = () => new Proxy({}, _noOpHandler);
  const $ = (sel, ctx) => (ctx || document).querySelector(sel) || _safeEl();
  const $$ = (sel, ctx) => [...(ctx || document).querySelectorAll(sel)];

  function token() {
    const meta = $('meta[name="auth-token"]') || $('meta[name="X-Auth-Token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  async function api(path) {
    const res = await fetch(path, {
      headers: { "X-Auth-Token": token() },
    });
    if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
    return res.json();
  }

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (k === "class") e.className = v;
      else if (k === "html") e.innerHTML = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    });
    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(c => {
        if (typeof c === "string") e.appendChild(document.createTextNode(c));
        else if (c) e.appendChild(c);
      });
    }
    return e;
  }

  function fmt(n, d = 2) {
    return n == null ? "—" : Number(n).toFixed(d);
  }

  function fmtUSD(n) {
    return n == null ? "—" : "$" + Number(n).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function colorClass(pct) {
    if (pct <= 40) return "risk-green";
    if (pct <= 70) return "risk-yellow";
    return "risk-red";
  }

  function gaugeColor(pct) {
    if (pct <= 40) return "#22c55e";
    if (pct <= 70) return "#eab308";
    return "#ef4444";
  }

  /* ------------------------------------------------------------------ */
  /*  State                                                              */
  /* ------------------------------------------------------------------ */

  let root = null;
  let compliance = null;
  let riskData = null;
  let positions = null;
  let equityCurve = null;
  let calcResult = null;

  const PAIRS = [
    "EURUSD", "USDJPY", "AUDUSD", "XAUUSD",
    "GBPUSD", "EURJPY", "SPX500", "JP225",
  ];

  /* ------------------------------------------------------------------ */
  /*  Section HTML skeleton                                              */
  /* ------------------------------------------------------------------ */

  function skeleton() {
    root.innerHTML = `
      <h2 class="panel-title">Risk &amp; Compliance</h2>

      <!-- 1 ─ Compliance Dashboard -->
      <div class="panel" id="risk-compliance">
        <h3 class="panel-title">Compliance Dashboard</h3>
        <div id="risk-compliance-body"><p class="dim small">Loading…</p></div>
      </div>

      <!-- 2 ─ Position Sizing Calculator -->
      <div class="panel" id="risk-calc">
        <h3 class="panel-title">Position Sizing Calculator</h3>
        <div id="risk-calc-body"><p class="dim small">Loading…</p></div>
      </div>

      <!-- 3 ─ Correlation Matrix -->
      <div class="panel" id="risk-corr">
        <h3 class="panel-title">Correlation Matrix</h3>
        <div id="risk-corr-body"><p class="dim small">Loading…</p></div>
      </div>

      <!-- 4 ─ Open Risk Monitor -->
      <div class="panel" id="risk-open">
        <h3 class="panel-title">Open Risk Monitor</h3>
        <div id="risk-open-body"><p class="dim small">Loading…</p></div>
      </div>

      <!-- 5 ─ Daily / Weekly Loss Tracker -->
      <div class="panel" id="risk-loss">
        <h3 class="panel-title">Daily / Weekly Loss Tracker</h3>
        <div id="risk-loss-body"><p class="dim small">Loading…</p></div>
      </div>

      <!-- 6 ─ Halt Controls -->
      <div class="panel" id="risk-halt">
        <h3 class="panel-title">Halt Controls</h3>
        <div id="risk-halt-body"><p class="dim small">Loading…</p></div>
      </div>
    `;
  }

  /* ------------------------------------------------------------------ */
  /*  1. Compliance Dashboard                                            */
  /* ------------------------------------------------------------------ */

  function renderCompliance(data) {
    compliance = data;
    const c = data;
    const mode = (c.mode || "GREEN").toUpperCase();
    const lossPct = c.daily_loss_used_pct ?? c.daily_loss_pct ?? 0;
    const ddPct = c.drawdown_pct ?? 0;
    const tradesLeft = c.trades_remaining ?? "—";
    const maxDD = c.max_drawdown ?? "—";

    const box = $("#risk-compliance-body");
    box.innerHTML = "";

    /* red banner when halted */
    if (mode === "RED") {
      const banner = el("div", {
        class: "risk-card risk-red",
        style: "text-align:center;font-weight:700;margin-bottom:12px;",
      }, ["TRADING HALTED"]);
      box.appendChild(banner);
    }

    /* gauge row */
    const row = el("div", { style: "display:flex;flex-wrap:wrap;gap:16px;" });

    const metrics = [
      { label: "Mode", value: mode, pct: mode === "GREEN" ? 20 : mode === "YELLOW" ? 60 : 100 },
      { label: "Daily Loss", value: fmt(lossPct, 1) + "%", pct: lossPct },
      { label: "Drawdown", value: fmt(ddPct, 1) + "%", pct: ddPct },
      { label: "Trades Left", value: String(tradesLeft), pct: tradesLeft === "—" ? 0 : Math.min(100, (10 - tradesLeft) * 10) },
    ];

    metrics.forEach(m => {
      const card = el("div", { class: "risk-card", style: "flex:1;min-width:130px;" });

      const gauge = el("div", { class: "risk-gauge" });
      const fill = el("div", {
        class: "risk-gauge-fill",
        style: `width:${Math.min(100, m.pct)}%;background:${gaugeColor(m.pct)};`,
      });
      gauge.appendChild(fill);

      const val = el("div", { class: "risk-value", html: m.value });
      const lbl = el("div", { class: "risk-label small dim", html: m.label });

      card.append(gauge, val, lbl);
      row.appendChild(card);
    });

    /* max drawdown line */
    if (maxDD !== "—") {
      const ddLine = el("div", { class: "small dim", style: "margin-top:8px;" },
        [`Max Drawdown: ${fmt(maxDD)}%`]);
      row.appendChild(ddLine);
    }

    box.appendChild(row);
  }

  /* ------------------------------------------------------------------ */
  /*  2. Position Sizing Calculator                                      */
  /* ------------------------------------------------------------------ */

  function renderCalc() {
    const box = $("#risk-calc-body");
    box.innerHTML = "";

    /* form row */
    const form = el("div", { style: "display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end;margin-bottom:12px;" });

    /* symbol select */
    const symWrap = el("div");
    symWrap.appendChild(el("label", { class: "small dim", html: "Symbol" }));
    const sel = el("select", { id: "risk-calc-sym", class: "btn" });
    PAIRS.forEach(p => {
      const opt = el("option", { value: p }, [p]);
      sel.appendChild(opt);
    });
    symWrap.appendChild(sel);
    form.appendChild(symWrap);

    /* risk % */
    const riskWrap = el("div");
    riskWrap.appendChild(el("label", { class: "small dim", html: "Risk %" }));
    const riskIn = el("input", {
      id: "risk-calc-risk",
      class: "btn",
      type: "number",
      step: "0.1",
      min: "0.1",
      max: "5",
      value: "0.5",
    });
    riskWrap.appendChild(riskIn);
    form.appendChild(riskWrap);

    /* SL pips */
    const slWrap = el("div");
    slWrap.appendChild(el("label", { class: "small dim", html: "SL Pips" }));
    const slIn = el("input", {
      id: "risk-calc-sl",
      class: "btn",
      type: "number",
      step: "1",
      min: "1",
      max: "500",
      value: "30",
    });
    slWrap.appendChild(slIn);
    form.appendChild(slWrap);

    /* button */
    const btn = el("button", {
      class: "btn",
      html: "Calculate",
      onClick: doCalc,
    });
    form.appendChild(btn);
    box.appendChild(form);

    /* result area */
    box.appendChild(el("div", { id: "risk-calc-result" }));

    /* show previous result if any */
    if (calcResult) showCalcResult(calcResult);
  }

  async function doCalc() {
    const sym = $("#risk-calc-sym").value;
    const risk = $("#risk-calc-risk").value;
    const sl = $("#risk-calc-sl").value;
    const out = $("#risk-calc-result");
    out.innerHTML = '<p class="dim small">Calculating…</p>';
    try {
      const data = await api(
        `/api/calc-lots?symbol=${encodeURIComponent(sym)}&pips=${sl}&risk=${risk}`
      );
      calcResult = data;
      showCalcResult(data);
    } catch (err) {
      out.innerHTML = `<p class="loss small">${err.message}</p>`;
    }
  }

  function showCalcResult(d) {
    const out = $("#risk-calc-result");
    if (!out) return;
    out.innerHTML = "";
    const row = el("div", { style: "display:flex;flex-wrap:wrap;gap:16px;" });

    const items = [
      { label: "Recommended Lots", value: d.lots ?? d.lot_size ?? "—" },
      { label: "Dollar Risk", value: fmtUSD(d.dollar_risk ?? d.risk_amount) },
      { label: "Margin Required", value: fmtUSD(d.margin_required ?? d.margin) },
    ];
    items.forEach(it => {
      const card = el("div", { class: "risk-card", style: "flex:1;min-width:130px;" });
      card.appendChild(el("div", { class: "risk-value small", html: String(it.value) }));
      card.appendChild(el("div", { class: "risk-label small dim", html: it.label }));
      row.appendChild(card);
    });
    out.appendChild(row);
  }

  /* ------------------------------------------------------------------ */
  /*  3. Correlation Matrix                                              */
  /* ------------------------------------------------------------------ */

  function renderCorrelation(data) {
    riskData = data;
    const box = $("#risk-corr-body");
    box.innerHTML = "";

    /* try to extract correlation_groups from /api/risk response */
    let matrix = null;
    if (data && data.correlation_groups) {
      matrix = data.correlation_groups;
    } else if (data && Array.isArray(data.correlations)) {
      matrix = data.correlations;
    }

    if (!matrix) {
      /* build empty matrix as fallback */
      matrix = {};
      PAIRS.forEach((a, i) => {
        PAIRS.forEach((b, j) => {
          const key = `${a}-${b}`;
          matrix[key] = i === j ? 1 : 0;
        });
      });
    }

    /* normalise to a lookup: matrix[a][b] or matrix[a-b] */
    function getC(a, b) {
      if (matrix[a] && matrix[a][b] != null) return matrix[a][b];
      if (matrix[b] && matrix[b][a] != null) return matrix[b][a];
      const key1 = `${a}-${b}`, key2 = `${b}-${a}`;
      if (matrix[key1] != null) return matrix[key1];
      if (matrix[key2] != null) return matrix[key2];
      return a === b ? 1 : 0;
    }

    const table = el("table", {
      style: "border-collapse:collapse;width:100%;font-size:13px;",
    });

    /* header row */
    const thead = el("thead");
    const hrow = el("tr");
    hrow.appendChild(el("th", { style: "padding:4px;" })); /* empty corner */
    PAIRS.forEach(p => {
      hrow.appendChild(el("th", {
        class: "small",
        style: "padding:4px;text-align:center;color:#999;",
        html: p,
      }));
    });
    thead.appendChild(hrow);
    table.appendChild(thead);

    /* body */
    const tbody = el("tbody");
    PAIRS.forEach(a => {
      const tr = el("tr");
      tr.appendChild(el("td", {
        class: "small",
        style: "padding:4px;text-align:right;font-weight:600;color:#999;",
        html: a,
      }));
      PAIRS.forEach(b => {
        const val = getC(a, b);
        const absVal = Math.abs(val);
        let bg;
        if (a === b) bg = "rgba(255,255,255,0.05)";
        else if (absVal <= 0.3) bg = "rgba(34,197,94,0.35)";
        else if (absVal <= 0.6) bg = "rgba(234,179,8,0.35)";
        else bg = "rgba(239,68,68,0.4)";

        const td = el("td", {
          style: `padding:4px;text-align:center;background:${bg};cursor:default;`,
          title: `${a} / ${b}: ${val.toFixed(3)}`,
          html: a === b ? "1.00" : val.toFixed(2),
        });
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);

    /* legend */
    const legend = el("div", {
      class: "small dim",
      style: "margin-top:8px;display:flex;gap:12px;",
    }, [
      el("span", { html: '<span style="display:inline-block;width:12px;height:12px;background:rgba(34,197,94,0.35);vertical-align:middle;margin-right:4px;border-radius:2px;"></span> Low (good diversification)' }),
      el("span", { html: '<span style="display:inline-block;width:12px;height:12px;background:rgba(234,179,8,0.35);vertical-align:middle;margin-right:4px;border-radius:2px;"></span> Medium' }),
      el("span", { html: '<span style="display:inline-block;width:12px;height:12px;background:rgba(239,68,68,0.4);vertical-align:middle;margin-right:4px;border-radius:2px;"></span> High (concentrated risk)' }),
    ]);
    box.appendChild(legend);
  }

  /* ------------------------------------------------------------------ */
  /*  4. Open Risk Monitor                                               */
  /* ------------------------------------------------------------------ */

  function renderOpenRisk(positionsList, risk) {
    positions = positionsList;
    const box = $("#risk-open-body");
    box.innerHTML = "";

    if (!positionsList || !positionsList.length) {
      box.appendChild(el("p", { class: "dim small", html: "No open positions." }));
      return;
    }

    const table = el("table", {
      style: "border-collapse:collapse;width:100%;font-size:13px;",
    });

    const cols = [
      "Symbol", "Type", "Lots", "Entry", "Current", "SL Dist",
      "$ Risk", "R-Multiple",
    ];
    const thead = el("thead");
    const hrow = el("tr");
    cols.forEach(c => {
      hrow.appendChild(el("th", {
        class: "small",
        style: "padding:6px 8px;text-align:left;border-bottom:1px solid #333;color:#999;",
        html: c,
      }));
    });
    thead.appendChild(hrow);
    table.appendChild(thead);

    const tbody = el("tbody");
    let totalRiskPct = 0;

    positionsList.forEach(p => {
      const tr = el("tr");

      /* derive fields — adapt to whatever API returns */
      const sym = p.symbol || "—";
      const side = (p.side || p.type || "—").toUpperCase();
      const lots = p.lots ?? p.volume ?? 0;
      const entry = p.entry_price ?? p.entry ?? 0;
      const current = p.current_price ?? p.current ?? 0;
      const slDist = p.sl_distance_pips ?? p.sl_pips ?? 0;
      const dollarRisk = p.dollar_risk ?? p.risk_amount ?? 0;
      const rMult = p.r_multiple ?? p.R_multiple ?? "—";

      if (risk && risk.total_risk_pct) totalRiskPct = risk.total_risk_pct;

      const cells = [
        sym, side, fmt(lots, 2), fmt(entry), fmt(current),
        fmt(slDist, 1), fmtUSD(dollarRisk),
        typeof rMult === "number" ? rMult.toFixed(2) + "R" : String(rMult),
      ];
      cells.forEach((v, i) => {
        let cls = "";
        if (i === 1) cls = side === "BUY" ? "profit" : "loss";
        tr.appendChild(el("td", {
          class: cls,
          style: "padding:6px 8px;border-bottom:1px solid #222;",
          html: v,
        }));
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);

    /* total risk footer */
    const footer = el("div", {
      class: "risk-card",
      style: "margin-top:10px;display:inline-block;",
    }, [
      el("span", { class: "small", html: "Total Open Risk: " }),
      el("span", {
        class: `small ${colorClass(totalRiskPct)}`,
        style: "font-weight:700;",
        html: fmt(totalRiskPct, 2) + "% of equity",
      }),
    ]);
    box.appendChild(footer);
  }

  /* ------------------------------------------------------------------ */
  /*  5. Daily / Weekly Loss Tracker (canvas bar chart)                  */
  /* ------------------------------------------------------------------ */

  function renderLossChart(data) {
    const box = $("#risk-loss-body");
    box.innerHTML = "";

    /* extract daily P&L series */
    let daily = [];
    if (Array.isArray(data)) {
      daily = data.slice(-7);
    } else if (data && Array.isArray(data.daily_pnl)) {
      daily = data.daily_pnl.slice(-7);
    } else if (data && Array.isArray(data.equity_curve)) {
      /* derive daily P&L from equity curve */
      const ec = data.equity_curve.slice(-8);
      for (let i = 1; i < ec.length; i++) {
        const prev = typeof ec[i - 1] === "object" ? ec[i - 1].equity || ec[i - 1].balance : ec[i - 1];
        const cur = typeof ec[i] === "object" ? ec[i].equity || ec[i].balance : ec[i];
        daily.push({
          date: ec[i].date || ec[i].time || `Day ${i}`,
          pnl: cur - prev,
        });
      }
    }

    if (!daily.length) {
      box.appendChild(el("p", { class: "dim small", html: "No P&L data available." }));
      return;
    }

    /* canvas */
    const canvas = el("canvas", {
      id: "risk-loss-canvas",
      style: "width:100%;height:200px;",
    });
    box.appendChild(canvas);

    /* wait for layout then draw */
    requestAnimationFrame(() => drawBarChart(canvas, daily));
  }

  function drawBarChart(canvas, daily) {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = 200 * dpr;
    canvas.style.width = rect.width + "px";
    canvas.style.height = "200px";

    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    const W = rect.width;
    const H = 200;
    const pad = { top: 20, right: 16, bottom: 32, left: 56 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;

    const vals = daily.map(d => typeof d === "number" ? d : d.pnl ?? 0);
    const maxAbs = Math.max(...vals.map(Math.abs), 1);
    const barW = chartW / vals.length;

    /* grid lines */
    ctx.strokeStyle = "#333";
    ctx.lineWidth = 0.5;
    for (let i = -1; i <= 1; i += 0.5) {
      const y = pad.top + chartH / 2 - (i / maxAbs) * (chartH / 2);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(W - pad.right, y);
      ctx.stroke();
    }

    /* zero line */
    const zeroY = pad.top + chartH / 2;
    ctx.strokeStyle = "#666";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, zeroY);
    ctx.lineTo(W - pad.right, zeroY);
    ctx.stroke();

    /* bars */
    vals.forEach((v, i) => {
      const x = pad.left + i * barW + barW * 0.15;
      const w = barW * 0.7;
      const barH = Math.abs(v / maxAbs) * (chartH / 2);
      const y = v >= 0 ? zeroY - barH : zeroY;

      ctx.fillStyle = v >= 0 ? "#22c55e" : "#ef4444";
      ctx.beginPath();
      ctx.roundRect(x, y, w, barH, 3);
      ctx.fill();

      /* label */
      ctx.fillStyle = "#999";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      const label = typeof daily[i] === "object"
        ? (daily[i].date || daily[i].time || "").slice(5)
        : "";
      ctx.fillText(label, x + w / 2, H - pad.bottom + 14);

      /* value on top */
      ctx.fillStyle = v >= 0 ? "#22c55e" : "#ef4444";
      const valY = v >= 0 ? y - 4 : y + barH + 14;
      ctx.fillText("$" + v.toFixed(0), x + w / 2, valY);
    });

    /* Y-axis labels */
    ctx.fillStyle = "#999";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs].forEach(v => {
      const y = pad.top + chartH / 2 - (v / maxAbs) * (chartH / 2);
      ctx.fillText("$" + v.toFixed(0), pad.left - 6, y + 4);
    });
  }

  /* ------------------------------------------------------------------ */
  /*  6. Halt Controls                                                   */
  /* ------------------------------------------------------------------ */

  function renderHalt(data) {
    const box = $("#risk-halt-body");
    box.innerHTML = "";

    const mode = (data.mode || compliance?.mode || "GREEN").toUpperCase();
    const isHalted = mode === "RED" || data.halted === true;

    /* status row */
    const statusRow = el("div", {
      style: "display:flex;align-items:center;gap:12px;margin-bottom:12px;",
    });

    const dot = el("span", {
      style: `display:inline-block;width:14px;height:14px;border-radius:50%;background:${isHalted ? "#ef4444" : "#22c55e"};`,
    });
    statusRow.appendChild(dot);
    statusRow.appendChild(el("span", {
      html: isHalted
        ? '<strong class="loss">Trading Halted</strong>'
        : '<strong class="profit">Trading Active</strong>',
    }));
    box.appendChild(statusRow);

    /* halt banner */
    if (isHalted) {
      box.appendChild(el("div", {
        class: "risk-card risk-red",
        style: "text-align:center;font-weight:700;margin-bottom:12px;padding:12px;",
        html: "⚠ TRADING IS HALTED — Manual intervention required",
      }));
    }

    /* buttons */
    const btnRow = el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;" });

    const haltBtn = el("button", {
      class: "btn",
      html: isHalted ? "Resume Trading" : "Halt Trading",
      onClick: () => toggleHalt(!isHalted),
    });
    if (!isHalted) haltBtn.classList.add("loss");
    else haltBtn.classList.add("profit");
    btnRow.appendChild(haltBtn);

    box.appendChild(btnRow);

    /* extra info */
    if (data.halt_reason) {
      box.appendChild(el("div", {
        class: "small dim",
        style: "margin-top:8px;",
        html: `Reason: ${data.halt_reason}`,
      }));
    }
    if (data.halted_at) {
      box.appendChild(el("div", {
        class: "small dim",
        html: `Since: ${new Date(data.halted_at).toLocaleString()}`,
      }));
    }
  }

  async function toggleHalt(halt) {
    try {
      /* try dedicated endpoint first */
      await fetch("/api/compliance/halt", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Auth-Token": token(),
        },
        body: JSON.stringify({ halt }),
      });
    } catch {
      /* fall back — some APIs use query param */
      try {
        await fetch(`/api/compliance?halt=${halt}`, {
          method: "POST",
          headers: { "X-Auth-Token": token() },
        });
      } catch (err) {
        console.warn("Halt toggle failed:", err);
      }
    }
    /* re-fetch and re-render halt section */
    try {
      const data = await api("/api/compliance");
      renderHalt(data);
    } catch { /* ignore */ }
  }

  /* ------------------------------------------------------------------ */
  /*  Data fetching                                                      */
  /* ------------------------------------------------------------------ */

  async function fetchData() {
    const results = await Promise.allSettled([
      api("/api/compliance"),
      api("/api/risk"),
      api("/api/positions"),
      api("/api/journal/equity-curve"),
    ]);
    return {
      compliance: results[0].status === "fulfilled" ? results[0].value : null,
      risk: results[1].status === "fulfilled" ? results[1].value : null,
      positions: results[2].status === "fulfilled" ? results[2].value : null,
      equity: results[3].status === "fulfilled" ? results[3].value : null,
    };
  }

  /* ------------------------------------------------------------------ */
  /*  Public API                                                         */
  /* ------------------------------------------------------------------ */

  async function render() {
    if (!root) return;
    skeleton();

    try {
      const d = await fetchData();
      if (d.compliance) renderCompliance(d.compliance);
      else $("#risk-compliance-body").innerHTML = '<p class="dim small">Compliance data unavailable.</p>';

      renderCalc();

      if (d.risk) renderCorrelation(d.risk);
      else $("#risk-corr-body").innerHTML = '<p class="dim small">Risk data unavailable.</p>';

      if (d.positions) {
        const posList = Array.isArray(d.positions)
          ? d.positions
          : d.positions.positions || [];
        renderOpenRisk(posList, d.risk);
      } else {
        $("#risk-open-body").innerHTML = '<p class="dim small">Position data unavailable.</p>';
      }

      renderLossChart(d.equity || []);

      if (d.compliance) renderHalt(d.compliance);
      else $("#risk-halt-body").innerHTML = '<p class="dim small">Compliance data unavailable.</p>';

    } catch (err) {
      console.error("Risk section render error:", err);
      root.innerHTML = `<div class="panel"><p class="loss">Failed to load risk data: ${err.message}</p></div>`;
    }
  }

  function init() {
    root = document.getElementById("hf-section-risk");
    if (!root) {
      console.warn("Risk: container #hf-section-risk not found");
      return;
    }
    render();
  }

  return { init, render };
})();
