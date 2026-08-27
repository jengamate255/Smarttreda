/* Split from app.js - loaded in order; shared global scope. */
/* ---------- Boot ---------- */
/* ---------- Backtesting ---------- */
const bt = {
  groups: null,
  mc: null,
  group: "month",
  detail: null,
  running: false,
};

/* ---------- Backtest ---------- */
async function loadBacktests() {
  showSkeleton($("bt-body"), "cards", 1);
  try {
    const data = await api("/api/backtests");
    bt.groups = data.groups;
    bt.mc = data.mc;
    state.btGenerated = data.generated;
    if (!bt.detail) renderBacktests();
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadBacktests", 0, 0, e);
  }
}

async function loadBacktestDetail(name) {
  try {
    const result = await api("/api/backtest/run?name=" + encodeURIComponent(name));
    showBacktestDetail(name, result);
  } catch (e) {
    toast("Failed to load backtest: " + e.message, "error");
  }
}

function showBacktestDetail(name, result) {
  if (!result) return;
  const detail = result;
  const html = `
    <div class="bt-detail">
      <div class="bt-header">
        <h3>${esc(detail.name || "Backtest")}</h3>
        <span class="badge ${detail.mode === "forward" ? "warn" : ""}">${detail.mode || "month"}</span>
      </div>
      <div class="bt-stats">
        <div class="stat"><span>Net P&L</span><b class="${detail.net_profit >= 0 ? "pos" : "neg"}">${fmtMoney(detail.net_profit)}</b></div>
        <div class="stat"><span>Win Rate</span><b>${detail.win_rate?.toFixed(1)}%</b></div>
        <div class="stat"><span>Profit Factor</span><b>${detail.profit_factor?.toFixed(2)}</b></div>
        <div class="stat"><span>Trades</span><b>${detail.trades}</b></div>
        <div class="stat"><span>Max DD</span><b class="neg">${detail.max_drawdown?.toFixed(2)}%</b></div>
        <div class="stat"><span>Avg R</span><b>${detail.avg_r?.toFixed(2)}</b></div>
      </div>
      <div class="bt-trades">
        <h4>Trades</h4>
        ${detail.trades_list?.slice(0, 20).map(t => `
          <div class="bt-trade-row">
            <span>${t.time}</span>
            <span class="${t.side === "BUY" ? "pos" : "neg"}">${t.side}</span>
            <span>${t.symbol}</span>
            <span>${t.lots} lots</span>
            <span class="${t.pnl >= 0 ? "pos" : "neg"}">${fmtMoney(t.pnl)}</span>
            <span>${t.r_multiple?.toFixed(2)}R</span>
          </div>
        `).join("") || "No trades"}
      </div>
      <button class="btn" onclick="loadBacktests()">Back to List</button>
    </div>
  `;
  $("bt-body").innerHTML = html;
}

/* ---------- Backtest ---------- */

async function loadBoomCrash() {
  const minConf = $("bc-min-conf").value || 50;
  showSkeleton($("bc-body"), "cards", 4);
  try {
    const data = await api(`/api/boom-crash/scan?min_confidence=${minConf}`);
    state.boomCrash = data;
    renderBoomCrash(data);
  } catch (e) { showPanelError("bc-body", e); }
}

function renderBoomCrash(data) {
  const body = $("bc-body");
  const meta = $("bc-meta");
  if (meta) {
    const stamp = data?.scanned_at ? new Date(data.scanned_at).toLocaleTimeString() : "--";
    meta.textContent = data?.symbols_scanned ? `Scanned ${data.symbols_scanned} symbols Â· ${stamp}` : "";
  }
  if (!data || data.error) {
    body.innerHTML = `<div class="plan-empty">Boom/Crash unavailable: ${esc(data?.error || "unknown")}</div>`;
    return;
  }
  const signals = data.signals || [];
  if (!signals.length) {
    body.innerHTML = `<div class="plan-empty">No signals at ${$("bc-min-conf").value}% confidence. Lower threshold or wait for spike.</div>`;
    return;
  }
  body.innerHTML = signals.map((s) => {
    const isSignal = s.confidence > 0;
    const badgeClass = isSignal ? "signal" : "watch";
    const badgeText = isSignal ? "SIGNAL" : "WATCH";
    const dirColor = s.direction === "spike_up" ? C.up : C.down;
    const driftColor = s.drift_direction === "up" ? C.up : C.down;
    return `
      <div class="bc-card ${badgeClass}">
        <div class="bc-header">
          <span class="bc-symbol">${esc(s.symbol)}</span>
          <span class="bc-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="bc-details">
          <div class="bc-row">
            <span class="bc-label">Direction</span>
            <span class="bc-value" style="color:${dirColor}">${esc(s.direction.replace("_", " ").toUpperCase())}</span>
          </div>
          <div class="bc-row">
            <span class="bc-label">Drift</span>
            <span class="bc-value" style="color:${driftColor}">${esc(s.drift_direction.toUpperCase())}</span>
          </div>
          <div class="bc-row">
            <span class="bc-label">Confidence</span>
            <span class="bc-value">${s.confidence}%</span>
          </div>
          <div class="bc-row">
            <span class="bc-label">Spike age</span>
            <span class="bc-value">${s.spike_age_candles ?? "--"} bars</span>
          </div>
          <div class="bc-row">
            <span class="bc-label">ATR</span>
            <span class="bc-value">${fmtPrice(s.atr)}</span>
          </div>
          <div class="bc-row">
            <span class="bc-label">Entry</span>
            <span class="bc-value">${fmtPrice(s.entry_price)}</span>
          </div>
          <div class="bc-row">
            <span class="bc-label">SL</span>
            <span class="bc-value" style="color:${C.down}">${fmtPrice(s.sl_price)}</span>
          </div>
          <div class="bc-row">
            <span class="bc-label">TP</span>
            <span class="bc-value" style="color:${C.up}">${fmtPrice(s.tp_price)}</span>
          </div>
          <div class="bc-reason">${esc(s.reason)}</div>
        </div>
      </div>
    `;
  }).join("");
}

/* ---------- AI Chart Detection ---------- */
async function loadAiDetection() {
  const symbol = state.symbol.trim().toUpperCase() || "EURUSD";
  const timeframe = $("aid-timeframe").value || "H1";
  const minQuality = $("aid-min-quality").value || 60;
  showSkeleton($("aid-body"), "cards", 3);
  try {
    const data = await api(`/api/ai-detection?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&lookback=500`);
    state.aiDetection = data;
    renderAiDetection(data, Number(minQuality));
  } catch (e) {
    $("aid-body").innerHTML = `<div class="plan-empty">AI detection unavailable: ${esc(e.message || String(e))}</div>`;
  }
}

function renderAiDetection(data, minQuality = 60) {
  const body = $("aid-body");
  if (!data || data.verdict === "unavailable") {
    body.innerHTML = `<div class="plan-empty">${esc(data?.errors?.join("; ") || "No detection data")}</div>`;
    return;
  }
  const patterns = (data.patterns || []).filter((p) => Number(p.ai_score || 0) >= minQuality);
  const market = data.market || {};
  const trend = market.trend || {};
  const verdictColor = data.verdict === "high_quality" ? C.up : data.verdict === "watch" ? C.yellow : C.dim;
  const header = `<div class="aid-summary">
    <div><span class="aid-symbol">${esc(data.symbol)}</span><span class="aid-meta">${esc(data.timeframe)} Â· ${fmtPrice(data.current_price)}</span></div>
    <div class="aid-verdict" style="color:${verdictColor}">${esc((data.verdict || "unknown").replaceAll("_", " ").toUpperCase())}</div>
    <div class="aid-meta">Bias: <b>${esc(data.bias || trend.direction || "neutral")}</b> Â· Regime: ${esc(market.regime?.regime || "unknown")} Â· ATR: ${fmtPrice(data.atr)}</div>
  </div>`;
  if (!patterns.length) {
    body.innerHTML = header + `<div class="plan-empty">No chart formation at quality ${minQuality}+. ${esc(data.explanation || "Wait for structure to develop.")}</div>`;
    return;
  }
  const cards = patterns.slice(0, 6).map((p) => {
    const color = p.direction === "bullish" ? C.up : p.direction === "bearish" ? C.down : C.yellow;
    const evidence = (p.evidence || []).slice(0, 3).join(" Â· ");
    return `<div class="aid-card" style="border-left-color:${color}">
      <div class="aid-card-head"><b>${esc(p.pattern.replaceAll("_", " "))}</b><span style="color:${color}">${esc(p.direction)} Â· ${esc(p.status)}</span></div>
      <div class="aid-score" style="color:${color}">${p.ai_score}/100</div>
      <div class="aid-levels"><span>Break ${fmtPrice(p.breakout_level)}</span><span>Target ${fmtPrice(p.target)}</span><span>Invalid ${fmtPrice(p.invalidation)}</span></div>
      <div class="aid-evidence">${esc(evidence)}</div>
    </div>`;
  }).join("");
  const levels = (data.support_resistance || []).slice(0, 6).map((l) => `<span class="aid-level ${l.type}">${esc(l.type)} ${fmtPrice(l.price)} Â· ${l.strength}</span>`).join("");
  body.innerHTML = header + `<div class="aid-grid">${cards}</div><div class="aid-levels-wrap"><span class="aid-section-label">Key levels</span>${levels || '<span class="aid-meta">No nearby levels</span>'}</div>`;
}

/* ---------- Market Intelligence Agent ---------- */

async function loadMarketIntel() {
  showSkeleton($("mi-body"), "cards", 3);
  try {
    const scan = await api("/api/market-scan?timeframe=H1&min_score=0&max_results=15");
    const primary = await api(`/api/market-intelligence/${encodeURIComponent(state.symbol)}`);
    state.marketIntel = { scan, primary };
    renderMarketIntel(scan, primary);
  } catch (e) { showPanelError("mi-body", e); }
}

function renderMarketIntel(scan, primary) {
  const body = $("mi-body");
  if (!scan || !scan.ranked || !scan.ranked.length) {
    body.innerHTML = `<div class="plan-empty">No market intelligence data</div>`;
    return;
  }
  const p = primary || scan.ranked[0];
  const verdictColor = p.verdict === "tradeable" ? C.up : p.verdict === "cautious" ? C.yellow : C.down;
  const cards = [
    `<div class="mi-card" style="border-left:3px solid ${verdictColor}"><div class="mi-title">${esc(p.symbol)}</div>
     <div class="mi-score" style="color:${verdictColor}">${p.health_score ?? "--"}/100</div>
     <div class="mi-verdict" style="color:${verdictColor}">${(p.verdict || "unknown").toUpperCase()}</div>
     <div class="mi-regime dim">${esc(p.regime || "--")} Â· ${esc(p.trend || "--")}</div></div>`,
    `<div class="mi-card"><div class="mi-title">Best Candidates</div>${scan.ranked.slice(0, 8).map((r) => `
       <div class="mi-row">
         <span class="mi-sym">${esc(r.symbol)}</span>
         <span class="mi-hs" style="color:${r.health_score >= 70 ? C.up : r.health_score >= 50 ? C.yellow : C.down}">${r.health_score}/100</span>
         <span class="mi-reg">${esc(r.regime)}</span>
         <span class="mi-trend" style="color:${r.trend === "bullish" ? C.up : r.trend === "bearish" ? C.down : C.dim}">${esc(r.trend)}</span>
         ${r.abnormal_volatility ? '<span class="mi-vol-warn" title="Abnormal volatility">âš¡</span>' : ""}
       </div>`).join("")}</div>`,
  ];
  body.innerHTML = `<div class="mi-grid">${cards.join("")}</div>`;
}

/* ---------- Research Agent ---------- */

async function loadResearch() {
  showSkeleton($("rch-body"), "cards", 1);
  try {
    const days = parseInt($("rch-days").value, 10) || 7;
    const report = await api(`/api/research-report?days=${days}&save=false`);
    state.research = report;
    renderResearch(report);
  } catch (e) { showPanelError("rch-body", e); }
}

async function loadWeeklyResearch() {
  showSkeleton($("rch-body"), "cards", 1);
  try {
    const report = await api("/api/weekly-research-report?save=true");
    state.research = report;
    renderResearch(report);
  } catch (e) { showPanelError("rch-body", e); }
}

function renderResearch(report) {
  const body = $("rch-body");
  if (!report || report.error) {
    body.innerHTML = `<div class="plan-empty">Research unavailable: ${esc(report?.error || "unknown")}</div>`;
    return;
  }
  const stats = [
    { label: "Trades", val: report.trades },
    { label: "Win Rate", val: (report.win_rate ?? 0).toFixed(1) + "%", cls: (report.win_rate ?? 0) >= 50 ? "pos" : "neg" },
    { label: "Expectancy", val: fmtMoney(report.expectancy), cls: (report.expectancy ?? 0) >= 0 ? "pos" : "neg" },
    { label: "Profit Factor", val: (report.profit_factor ?? 0).toFixed(2), cls: (report.profit_factor ?? 0) >= 1 ? "pos" : "neg" },
    { label: "Avg R", val: (report.average_rr ?? 0).toFixed(2) },
    { label: "Net P&L", val: fmtMoney(report.net_pnl), cls: (report.net_pnl ?? 0) >= 0 ? "pos" : "neg" },
    { label: "Max DD", val: (report.max_drawdown?.max_drawdown_percent ?? 0).toFixed(2) + "%", cls: "neg" },
  ];
  const suggestions = report.suggestions || [];
  body.innerHTML =
    `<div class="rch-header">
      <div class="stats-grid">${stats.map((s) =>
        `<div class="stat"><div class="stat-label">${s.label}</div><div class="stat-value ${s.cls || ""}">${s.val}</div></div>`).join("")}
    </div>` +
    (suggestions.length ? `
      <div class="rch-suggestions">
        <h4>Suggestions</h4>
        ${suggestions.map((s) => `
          <div class="rch-sugg ${s.severity}">
            <div class="rch-sugg-title">${esc(s.suggestion)}</div>
            <div class="rch-supp">Evidence: ${esc(s.support)}</div>
            <div class="rch-action">Action: ${esc(s.action)}</div>
          </div>`).join("")}
      </div>` : "") +
    (report.markdown ? `<details class="rch-markdown"><summary>Full Markdown Report</summary><pre>${esc(report.markdown)}</pre></details>` : "");
}

/* ---------- AI Coach ---------- */

async function loadCoach(ticket = null) {
  showSkeleton($("ch-body"), "cards", 1);
  try {
    const path = ticket ? `/api/coach-review/${ticket}` : "/api/coach-review/latest";
    const review = await api(path);
    state.coach = review;
    renderCoach(review);
  } catch (e) { showPanelError("ch-body", e); }
}

function renderCoach(review) {
  const body = $("ch-body");
  if (!review || review.error) {
    body.innerHTML = `<div class="plan-empty">Coach unavailable: ${esc(review?.error || "unknown")}</div>`;
    return;
  }
  const verdictColor = review.outcome === "win" ? C.up : C.down;
  const similar = review.similar_trades || {};
  body.innerHTML =
    `<div class="ch-header" style="border-left:3px solid ${verdictColor}">
       <div class="ch-title">#${review.ticket} Â· ${esc(review.symbol)} ${esc(review.side)} ${esc(review.setup || "")}</div>
       <div class="ch-verdict" style="color:${verdictColor}">${esc(review.verdict)}</div>
       <div class="ch-meta dim">${esc(review.close_time || "")} Â· RR ${review.rr ?? "--"} Â· conf ${review.confidence ?? "--"}% Â· score ${review.trade_score ?? "--"}</div>
     </div>` +
    `<div class="ch-section">
       <h4>Why taken</h4>
       <p>${esc(review.why_taken)}</p>
     </div>` +
    `<div class="ch-section">
       <h4>Evidence FOR</h4>
       <ul>${(review.evidence_for || []).map((e) => `<li>${esc(e)}</li>`).join("") || "<li class='dim'>none</li>"}</ul>
     </div>` +
    `<div class="ch-section">
       <h4>Evidence AGAINST</h4>
       <ul>${(review.evidence_against || []).map((e) => `<li>${esc(e)}</li>`).join("") || "<li class='dim'>none</li>"}</ul>
     </div>` +
    `<div class="ch-section">
       <h4>Improvements</h4>
       <ul>${(review.improvement || []).map((e) => `<li>${esc(e)}</li>`).join("") || "<li class='dim'>none</li>"}</ul>
     </div>` +
    (similar.count >= 1 ? `
      <div class="ch-section">
        <h4>Similar Trades (${similar.count})</h4>
        <div class="ch-sim-stats">
          <span>Win rate: <b class="${similar.win_rate >= 50 ? "pos" : "neg"}">${similar.win_rate}%</b></span>
          <span>Avg P&L: <b class="${similar.avg_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(similar.avg_pnl)}</b></span>
          <span>Expectancy: <b class="${similar.expectancy >= 0 ? "pos" : "neg"}">${fmtMoney(similar.expectancy)}</b></span>
        </div>
        <div class="table-wrap ch-sim-table">
          <table class="data-table"><thead><tr><th>Ticket</th><th>Symbol</th><th>Side</th><th>Setup</th><th>Close</th><th>P&L</th><th>RR</th><th>Conf</th><th>Sim</th></tr></thead>
          <tbody>${(similar.trades || []).map((t) => `
            <tr><td>${esc(t.ticket)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td>${esc(t.setup)}</td>
            <td>${esc((t.close_time || "").slice(0, 16))}</td>
            <td class="${(t.pnl ?? 0) >= 0 ? "pos" : "neg"}">${fmtMoney(t.pnl)}</td>
            <td>${t.rr ?? "--"}</td><td>${t.confidence ?? "--"}%</td><td>${t.similarity ?? "--"}%</td></tr>`).join("")}
          </tbody></table>
        </div>
      </div>` : "");
}

/* ---------- Backtesting presets ---------- */
const BT_PRESETS = {
  scalper:   { trend_tf: "H1", zone_tfs: "H1,M5", zscore: 30, rr: 1.5, tpmin: 20, tpmax: 40, slmin: 10, risk: 0.3, bars: 800 },
  swing:     { trend_tf: "H4", zone_tfs: "H1,H4", zscore: 40, rr: 2.5, tpmin: 60, tpmax: 100, slmin: 15, risk: 0.5, bars: 2000 },
  trend:     { trend_tf: "D1", zone_tfs: "H4,D1", zscore: 50, rr: 3.0, tpmin: 100, tpmax: 200, slmin: 20, risk: 0.5, bars: 5000 },
  conservative: { trend_tf: "H4", zone_tfs: "H4,D1", zscore: 60, rr: 3.5, tpmin: 80, tpmax: 150, slmin: 20, risk: 0.4, bars: 3000 },
};

function btFormHtml() {
  return `<div class="bt-form">
    <select id="bt-preset" title="Preset strategy">
      <option value="">Custom</option>
      <option value="scalper">Scalper (H1, fast)</option>
      <option value="swing">Swing (H4, medium)</option>
      <option value="trend">Trend (D1, slow)</option>
      <option value="conservative">Conservative (H4/D1, high threshold)</option>
    </select>
    <input id="bt-symbol" list="symbol-list" value="${esc(bt.detail ? bt.detail.params.symbol : "XAUUSD")}" spellcheck="false" title="Symbol">
    <select id="bt-trend" title="Trend timeframe"><option>H4</option><option>H1</option><option>D1</option></select>
    <input id="bt-zones" value="H1,H4" title="Zone timeframes (comma-separated)">
    <input id="bt-zscore" type="number" min="0" max="100" step="1" value="40" title="Min zone score (0-100)">
    <input id="bt-rr" type="number" min="0.5" max="10" step="0.5" value="3" title="Min RR (0.5-10)">
    <input id="bt-tpmin" type="number" min="1" max="1000" value="80" title="TP min pips (1-1000)">
    <input id="bt-tpmax" type="number" min="1" max="1000" value="100" title="TP max pips (1-1000)">
    <input id="bt-slmin" type="number" min="1" max="500" value="20" title="SL min pips (1-500)">
    <input id="bt-risk" type="number" min="0.01" max="10" step="0.1" value="0.5" title="Risk % (0.01-10)">
    <input id="bt-bars" type="number" min="200" max="20000" value="1500" title="Bars 200-20000">
    <input id="bt-since" placeholder="Since YYYY-MM-DD" pattern="\d{4}-\d{2}-\d{2}" title="Forward window start, YYYY-MM-DD">
    <button class="btn" data-bt="run" title="Run backtest now (MT5 terminal must be running)">Run backtest</button>
    <button class="btn" data-bt="forward" title="Windowed forward test + full comparison">Forward test</button>
    <button class="btn" data-bt="compare" title="Compare selected results">Compare</button>
  </div>`;
}

function mcHtml(mc) {
  const f = mc.final_return_pct, d = mc.max_drawdown_pct;
  const pos = mc.prob_positive_month >= 0.6;
  const blown = mc.prob_blown_account < 0.05;
  return `<div class="bt-mc" title="${mc.sims.toLocaleString()} simulations, 0.5% risk/trade, -3% daily halt, -8% stop">
    <span>MC ${mc.sims.toLocaleString()} sims</span>
    <span>P(positive) <b class="${pos ? "pos" : "neg"}">${(mc.prob_positive_month * 100).toFixed(0)}%</b></span>
    <span>P(blown) <b class="${blown ? "pos" : "neg"}">${(mc.prob_blown_account * 100).toFixed(1)}%</b></span>
    <span>P(daily limit) <b>${(mc.prob_daily_limit_hit * 100).toFixed(0)}%</b></span>
    <span>EV/mo <b class="${mc.expected_value_per_month >= 0 ? "pos" : "neg"}">${fmtMoney(mc.expected_value_per_month)}</b></span>
    <span>Return p50 <b class="${f.p50 >= 0 ? "pos" : "neg"}">${f.p50 > 0 ? "+" : ""}${f.p50}%</b>
      <span class="dim">p5 ${f.p05}% / p95 ${f.p95 > 0 ? "+" : ""}${f.p95}%</span></span>
    <span>MaxDD p90 <b class="neg">${d.p90}%</b></span>
    <span>${mc.n_trades} trades in pool</span>
  </div>`;
}

function rowsTable(rows) {
  if (!rows.length) return `<div class="plan-empty">No backtest results yet. Run one above (terminal must be attached to MT5).</div>`;
  const head = `<tr><th>Symbol</th><th>Variant</th><th>Trades</th><th>Win%</th><th>PF</th><th>Net</th><th>Return%</th><th>MaxDD%</th><th>Exp</th><th>Window</th></tr>`;
  const body = rows.map((r) => {
    const net = +r.net_pnl;
    const variant = r.file.replace(/\.json$/, "");
    return `<tr class="bt-row" data-bt="open" data-name="${esc(r.dir)}/${esc(r.file)}" title="${esc(variant)}">
      <td>${esc(r.symbol)}</td><td>${esc(variant)}</td>
      <td>${r.trades}</td><td>${r.win_rate}</td><td>${r.profit_factor}</td>
      <td class="${net >= 0 ? "pos" : "neg"}">${fmtMoney(net)}</td>
      <td class="${+r.total_return_pct >= 0 ? "pos" : "neg"}">${r.total_return_pct}</td>
      <td class="neg">${r.max_drawdown_pct}</td><td>${r.expectancy}</td>
      <td class="dim">${esc(r.start.slice(0, 10))}&rarr;${esc(r.end.slice(0, 10))}</td></tr>`;
  }).join("");
  return `<div class="table-wrap bt-table"><table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

function _ensureBtShell() {
  // Form + results container built ONCE - periodic refreshes only touch
  // #bt-results so in-progress form input survives (item #4/#18).
  const body = $("bt-body");
  if (!body.querySelector(".bt-form")) {
    body.innerHTML = btFormHtml() + `<div id="bt-results"></div>`;
  }
  return $("bt-results");
}

function renderBacktests() {
  const zone = _ensureBtShell();
  bt.detail = null;
  $("bt-sub").textContent = `saved results, updated ${fmtWhen(state.btGenerated)}`;
  const rows = (bt.groups && bt.groups[bt.group]) || [];
  const mc = bt.group === "month" ? bt.mc : null;
  zone.innerHTML =
    `<div class="bt-tabs">${["month", "forward", "all"].map((g) =>
      `<button class="btn small bt-tab${g === bt.group ? " active" : ""}" data-bt="tab" data-g="${g}">${g}</button>`).join("")}
    </div>` +
    (mc ? mcHtml(mc) : "") +
    rowsTable(rows);

  // init sortable + row selection after innerHTML
  setTimeout(() => {
    const table = zone.querySelector("table.data-table");
    if (!table) return;
    // Re-init when the table element was replaced by a re-render.
    if (window._backtestsSortable && window._backtestsSortable._table !== table) {
      window._backtestsSortable = null;
    }
    if (!window._backtestsSortable) {
      window._backtestsSortable = makeSortable(table, {
        pageSize: 25,
        numericCols: [2, 3, 4, 5, 6, 7, 8],
        dateCols: [9],
      });
      window._backtestsSortable._table = table;
    }
    window._backtestsSortable.setRows(table.querySelectorAll("tbody tr"));
    // row selection for comparison
    table.querySelectorAll("tbody tr").forEach((tr) => {
      tr.addEventListener("click", (e) => {
        if (e.target.closest("button, a, input, select")) return;
        tr.classList.toggle("selected");
      });
    });
  }, 0);
}

function btTradesTable(trades) {
  if (!trades.length) return `<div class="plan-empty">No trades in this run.</div>`;
  const head = `<tr><th>Entry</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>Exit</th><th>Lots</th><th>R</th><th>PnL</th><th>Reason</th></tr>`;
  const body = trades.map((t) => `<tr>
    <td>${esc(t.entry_time.slice(0, 16))}</td><td>${t.action}</td>
    <td>${t.entry}</td><td>${t.sl}</td><td>${t.tp}</td><td>${t.exit_price}</td>
    <td>${t.lots}</td><td>${t.rr}</td>
    <td class="${t.pnl >= 0 ? "pos" : "neg"}">${fmtMoney(t.pnl)}</td>
    <td class="dim">${esc(t.reason)}</td></tr>`).join("");
  return `<div class="table-wrap bt-table"><table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

function renderBtDetail(d) {
  bt.detail = d;
  const zone = _ensureBtShell();
  const p = d.params;
  const stats = [
    ["Trades", d.n_trades, ""],
    ["Win rate", d.win_rate + "%", +d.win_rate >= 50 ? "pos" : "neg"],
    ["Profit factor", d.profit_factor, +d.profit_factor >= 1 ? "pos" : "neg"],
    ["Net PnL", fmtMoney(d.net_pnl), +d.net_pnl >= 0 ? "pos" : "neg"],
    ["Return", (d.total_return_pct > 0 ? "+" : "") + d.total_return_pct + "%", +d.total_return_pct >= 0 ? "pos" : "neg"],
    ["Max DD", d.max_drawdown_pct + "%", "neg"],
    ["Expectancy", d.expectancy, +d.expectancy >= 0 ? "pos" : "neg"],
    ["Avg R", d.avg_rr, ""],
  ];
  $("bt-sub").textContent =
    `${p.symbol} ${p.trend_timeframe}/${p.zone_timeframes.join("+")} z${p.min_zone_score} rr${p.min_rr} tp${p.tp_min}-${p.tp_max} - ${d.start.slice(0, 10)} to ${d.end.slice(0, 10)}`;
  zone.innerHTML =
    `<div class="bt-detail-actions">
      <button class="btn small" data-bt="back">&#8592; Back to results</button>
      ${d.params && d.params.symbol ? `<button class="btn small" data-bt="chart">Show on chart</button>` : ""}
      ${d.saved ? `<span class="dim">saved ${esc(d.saved)}</span>` : ""}
      ${d.elapsed_s ? `<span class="dim">${d.elapsed_s}s</span>` : ""}
    </div>` +
    `<div class="stats-grid">${stats.map((s) =>
      `<div class="stat"><div class="stat-label">${s[0]}</div><div class="stat-value ${s[2]}">${s[1]}</div></div>`).join("")}
    </div>` +
    `<div class="bt-curve-wrap"><canvas id="bt-curve"></canvas></div>` +
    (d.errors && d.errors.length ? `<div class="plan-err">${esc(d.errors.join("; "))}</div>` : "") +
    btTradesTable(d.trades);
  drawBtCurve(d.equity);
}

function drawBtCurve(points) {
  const canvas = $("bt-curve");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = Math.max(200, rect.width * dpr);
  canvas.height = Math.max(120, rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = canvas.width / dpr;
  const H = canvas.height / dpr;
  ctx.clearRect(0, 0, W, H);
  if (!points || points.length < 2) {
    ctx.fillStyle = C.dim;
    ctx.font = "12px Consolas";
    ctx.textAlign = "center";
    ctx.fillText("No equity curve (no trades)", W / 2, H / 2);
    return;
  }
  const padL = 8, padR = 8, padT = 10, padB = 16;
  const vals = points.map((p) => p.value);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = (hi - lo) || 1;
  const x = (i) => padL + (i * (W - padL - padR)) / (points.length - 1);
  const y = (v) => padT + ((H - padT - padB) - ((v - lo) / span) * (H - padT - padB));

  ctx.strokeStyle = C.border;
  ctx.fillStyle = C.dim;
  ctx.font = "9px Consolas";
  ctx.textAlign = "left";
  for (let k = 0; k <= 3; k++) {
    const v = lo + (span * k) / 3, yy = y(v);
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
    ctx.fillText(v.toFixed(0), 0, yy + 3);
  }

  const color = vals[vals.length - 1] >= vals[0] ? C.up : C.down;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  vals.forEach((v, i) => (i === 0 ? ctx.moveTo(x(i), y(v)) : ctx.lineTo(x(i), y(v))));
  ctx.stroke();

  ctx.strokeStyle = C.border;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(padL, y(vals[0]));
  ctx.lineTo(W - padR, y(vals[0]));
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = C.dim;
  ctx.textAlign = "center";
  const step = Math.max(1, Math.round(points.length / 8));
  for (let i = 0; i < points.length; i += step) {
    ctx.fillText(points[i].label.slice(5), x(i), H - 4);
  }
}

async function openBtDetail(name) {
  try {
    const d = await api(`/api/backtest/run?name=${encodeURIComponent(name)}`);
    renderBtDetail(d);
  } catch (e) {
    toast(e.message || "Failed to load backtest", "down");
  }
}

async function runBacktest(forward) {
  if (bt.running) return;
  const body = {
    symbol: $("bt-symbol").value.trim().toUpperCase(),
    trend_tf: $("bt-trend").value,
    zone_tfs: $("bt-zones").value.trim(),
    min_zone_score: +$("bt-zscore").value,
    min_rr: +$("bt-rr").value,
    tp_min: +$("bt-tpmin").value,
    tp_max: +$("bt-tpmax").value,
    sl_min: +$("bt-slmin").value,
    risk: +$("bt-risk").value,
    bars: +$("bt-bars").value,
    since: $("bt-since").value.trim(),
    out: bt.group,
  };
  if (!body.symbol) { toast("Enter a symbol", "down"); return; }
  if (forward && !body.since) { toast("Forward test needs a 'since' date (YYYY-MM-DD)", "down"); return; }
  bt.running = true;
  const btnId = forward ? "bt-forward" : "bt-run";
  const btn = $(btnId);
  if (btn) {
    btn.disabled = true;
    btn.textContent = forward ? "Running forward test..." : "Running backtest...";
  }
  try {
    const res = await apiPost(forward ? "/api/backtest/forward" : "/api/backtest/run", body);
    const d = forward ? res.windowed : res;
    d.elapsed_s = res.elapsed_s;
    renderBtDetail(d);
    loadBacktests();
    toast(`${body.symbol} ${forward ? "forward test" : "backtest"} done in ${res.elapsed_s}s`);
  } catch (e) {
    toast(e.message || "Backtest failed", "down");
  } finally {
    bt.running = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = forward ? "Forward test" : "Run backtest";
    }
  }
}

function initBacktests() {
  $("bt-body").addEventListener("click", (ev) => {
    const el = ev.target.closest("[data-bt]");
    if (!el) return;
    const kind = el.dataset.bt;
    if (kind === "tab") {
      bt.group = el.dataset.g;
      bt.detail = null;
      renderBacktests();
    } else if (kind === "open") {
      openBtDetail(el.dataset.name);
    } else if (kind === "back") {
      bt.detail = null;
      renderBacktests();
    } else if (kind === "chart") {
      showBtOnChart(bt.detail);
    } else if (kind === "run" || kind === "forward") {
      runBacktest(kind === "forward");
    } else if (kind === "compare") {
      showComparison();
    }
  });
  $("bt-body").addEventListener("change", (ev) => {
    if (ev.target.id === "bt-preset") {
      applyPreset(ev.target.value);
    }
  });
$("bt-refresh").addEventListener("click", () => {
    loadBacktests();
    toast("Backtests refreshed");
  });
}

/* ---------- Backtesting presets ---------- */

function applyPreset(name) {
  const p = BT_PRESETS[name];
  if (!p) return;
  $("bt-trend").value = p.trend_tf;
  $("bt-zones").value = p.zone_tfs;
  $("bt-zscore").value = p.zscore;
  $("bt-rr").value = p.rr;
  $("bt-tpmin").value = p.tpmin;
  $("bt-tpmax").value = p.tpmax;
  $("bt-slmin").value = p.slmin;
  $("bt-risk").value = p.risk;
  $("bt-bars").value = p.bars;
  toast(`Applied ${name} preset`, "info");
}

function showComparison() {
  const selected = [...$("bt-body").querySelectorAll(".bt-row.selected")].map((r) => r.dataset.name);
  if (selected.length < 2) {
    toast("Select 2+ rows to compare (click row to toggle)", "warning");
    return;
  }
  Promise.all(selected.map((name) => api(`/api/backtest/run?name=${encodeURIComponent(name)}`)))
    .then((results) => renderComparison(results))
    .catch((e) => toast(e.message || "Comparison failed", "down"));
}

function renderComparison(results) {
  const headers = ["Metric", ...results.map((r) => `${r.params.symbol} ${r.params.trend_timeframe}/${r.params.zone_timeframes.join("+")} z${r.params.min_zone_score}`)];
  const metrics = [
    ["Trades", "n_trades"],
    ["Win rate", "win_rate", (v) => v + "%"],
    ["Profit factor", "profit_factor"],
    ["Net PnL", "net_pnl", fmtMoney],
    ["Return %", "total_return_pct", (v) => (v > 0 ? "+" : "") + v + "%"],
    ["Max DD %", "max_drawdown_pct", (v) => v + "%"],
    ["Expectancy", "expectancy"],
    ["Avg R", "avg_rr"],
    ["MC P(positive)", "mc_prob", (v) => (v * 100).toFixed(0) + "%"],
  ];
  const rows = metrics.map(([label, key, fmt]) =>
    `<tr><td>${label}</td>${results.map((r) => `<td>${fmt ? fmt(r[key]) : r[key]}</td>`).join("")}</tr>`
  ).join("");
  const html = `
    <div class="bt-compare">
      <div class="bt-compare-header">
        <h3>Comparison (${results.length} runs)</h3>
        <button class="btn small" data-bt="back">ï¿½ Back</button>
      </div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </div>`;
  $("bt-body").innerHTML = html;
}

/* ---------- Live trade events (watcher fills/closes/trails) ---------- */
const EVENTS_KEY = "mt5dash.eventsCursor";
let eventsCursor = parseInt(localStorage.getItem(EVENTS_KEY) || "0", 10) || 0;
async function pollEvents() {
  try {
    const r = await api(`/api/events?since=${eventsCursor}`);
    if (r.events && r.events.length) {
      for (const ev of r.events) {
        eventsCursor = Math.max(eventsCursor, ev.id);
        const kind = ev.kind === "filled" || ev.kind === "closed" ? "down"
          : ev.kind === "trail" ? "warning" : "info";
        toast(`${String(ev.account || "?").toUpperCase()} Â· ${ev.message}`, kind, { timeout: 7000 });
      }
      localStorage.setItem(EVENTS_KEY, String(eventsCursor));
    } else if (r.latest_id > eventsCursor) {
      eventsCursor = r.latest_id;
    }
  } catch (e) { if (!window._evtWarnAt || Date.now() - window._evtWarnAt > 60000) { window._evtWarnAt = Date.now(); console.warn('events poll failed:', e); } }
}

/* ---------- Peer dashboard (dual-account view) ---------- */
async function loadPeer() {
  const chip = $("peer-chip");
  if (!chip) return;
  try {
    const p = await api("/api/peer");
    if (!p.configured) { chip.classList.add("hidden"); return; }
    chip.classList.remove("hidden");
    chip.dataset.port = String(p.port || "");
    if (!p.ok) {
      chip.textContent = p.name + " Â· offline";
      chip.title = p.error || "Peer dashboard offline";
      return;
    }
    const a = p.account || {};
    const n = (p.positions || []).length;
    const eq = a.equity != null ? Number(a.equity).toFixed(2) : "--";
    const pnl = a.daily_pnl != null ? (a.daily_pnl >= 0 ? "+" : "") + Number(a.daily_pnl).toFixed(2) : "";
    chip.textContent = `${p.name}: ${a.login ?? "?"} ${eq} ${pnl} Â· ${n} pos`;
    chip.title = `Open ${p.name} dashboard (port ${p.port})`;
  } catch (e) { /* silent */ }
}

/* ---------- Sidebar (mobile) ---------- */
function initSidebar() {
  const sidebar = $("#sidebar");
  const toggle = $("#sidebar-toggle");
  const backdrop = $("#sidebar-backdrop");
  if (!sidebar || !toggle || !backdrop) return;

  const mq = window.matchMedia("(max-width: 700px)");
  const update = () => {
    const isMobile = mq.matches;
    toggle.classList.toggle("hidden", !isMobile);
    if (!isMobile) {
      sidebar.classList.remove("open");
      backdrop.classList.add("hidden");
    }
  };
  mq.addEventListener("change", update);
  update();

  toggle.addEventListener("click", () => {
    sidebar.classList.toggle("open");
    backdrop.classList.toggle("hidden");
  });
  backdrop.addEventListener("click", () => {
    sidebar.classList.remove("open");
    backdrop.classList.add("hidden");
  });
}

function init() {
  loadPrefs();
  if (!state.watchlist.length) state.watchlist = [...DEFAULT_WATCH];

  const peerChip = $("peer-chip");
  if (peerChip) peerChip.addEventListener("click", () => {
    const port = peerChip.dataset.port;
    if (port) window.open(`http://127.0.0.1:${port}`, "_blank");
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  buildTfButtons();
  initControls();
  initJournalTools();
  initBacktests();
  initConnectionDropdown();
  initTheme();
  registerShortcuts();
  setupCharts();
  connectWs();
  loadSummary();
  loadAccounts();
  loadChart();
  loadSymbols();
  loadHealth();
  loadEquity();
  loadJournal();
  loadJournalEquity();
  loadJournalDistribution();
  loadRisk();
  loadCompliance();
  loadForexBreakout();
  loadSessions();
  loadNews();
  loadBacktests();
  loadMarketIntel();
  loadResearch();
  loadCoach();
  loadBoomCrash();
  loadAiDetection();
  pollEvents();
  loadPeer();

  const tick = (fn) => () => { if (!document.hidden) fn(); };

  // Fast tier (5s): lightweight real-time data
  setInterval(tick(pollEvents), POLL_MS);
  setInterval(tick(loadPeer), POLL_MS);

  // Medium tier (15s): health + chart refresh
  setInterval(tick(loadHealth), 15000);
  setInterval(tick(loadChart), CHART_MS);

  // Slow tier (60s): heavier data, staggered with offsets
  let slowIdx = 0;
  const slowFns = [loadPlan, loadEquity, loadJournal, loadBoomCrash, loadRisk, loadCompliance];
  setInterval(() => {
    if (document.hidden) return;
    slowFns[slowIdx % slowFns.length]();
    slowIdx++;
  }, SLOW_MS);

  // Very slow tier (120s): expensive scans
  let vslowIdx = 0;
  const vslowFns = [loadForexBreakout, loadSessions, loadAiDetection];
  setInterval(() => {
    if (document.hidden) return;
    vslowFns[vslowIdx % vslowFns.length]();
    vslowIdx++;
  }, SLOW_MS * 2);

  // News tier (5min)
  setInterval(tick(loadNews), NEWS_MS);

  // WS fallback: only poll summary if WS is dead
  setInterval(tick(loadSummary), POLL_MS);
}

/* ---------- Walk-Forward Validation ---------- */
let wfRunning = false;

async function runWalkForward() {
  if (wfRunning) return;
  const body = $("wf-body");
  const btn = $("wf-run");
  wfRunning = true;
  btn.disabled = true;
  btn.textContent = "Running...";
  showSkeleton(body, "cards", 1);

  try {
    const payload = {
      symbol: $("wf-symbol").value.trim().toUpperCase() || "EURUSD",
      train_bars: parseInt($("wf-train").value, 10) || 2000,
      test_bars: parseInt($("wf-test").value, 10) || 500,
      step_bars: parseInt($("wf-step").value, 10) || 500,
      trend_tf: $("wf-trend").value,
      zone_tfs: $("wf-zones").value.trim(),
      slippage_points: parseFloat($("wf-slip").value) || 1.0,
      commission_per_lot: parseFloat($("wf-comm").value) || 7.0,
    };
    const result = await apiPost("/api/backtest/walk-forward", payload);
    renderWalkForward(result);
    toast(`Walk-forward done in ${result.elapsed_s}s (${result.n_folds} folds)`);
  } catch (e) {
    body.innerHTML = `<div class="plan-empty">Walk-forward failed: ${esc(e.message || String(e))}</div>`;
    toast("Walk-forward failed: " + e.message, "error");
  } finally {
    wfRunning = false;
    btn.disabled = false;
    btn.textContent = "Run Walk-Forward";
  }
}

function renderWalkForward(r) {
  const body = $("wf-body");
  if (!r || r.error || !r.folds || !r.folds.length) {
    body.innerHTML = `<div class="plan-empty">${esc(r?.errors?.join("; ") || r?.error || "No walk-forward results")}</div>`;
    return;
  }

  // overfitting verdict
  let verdictColor = C.dim, verdictText = "Unknown";
  if (r.consistency_score >= 60 && r.is_ratio >= 0.6) {
    verdictColor = C.up; verdictText = "LOW OVERFIT";
  } else if (r.consistency_score >= 40 && r.is_ratio >= 0.4) {
    verdictColor = C.yellow; verdictText = "MODERATE OVERFIT";
  } else {
    verdictColor = C.down; verdictText = "HIGH OVERFIT";
  }

  const stats = [
    { label: "OOS Trades", val: r.oos_trades },
    { label: "OOS Win Rate", val: r.oos_win_rate + "%", cls: r.oos_win_rate >= 50 ? "pos" : "neg" },
    { label: "OOS Profit Factor", val: r.oos_profit_factor, cls: r.oos_profit_factor >= 1 ? "pos" : "neg" },
    { label: "OOS Net P&L", val: fmtMoney(r.oos_net_pnl), cls: r.oos_net_pnl >= 0 ? "pos" : "neg" },
    { label: "OOS Expectancy", val: fmtMoney(r.oos_expectancy), cls: r.oos_expectancy >= 0 ? "pos" : "neg" },
    { label: "OOS Max DD", val: r.oos_max_drawdown_pct + "%", cls: "neg" },
    { label: "IS/OOS PF Ratio", val: r.is_ratio, cls: r.is_ratio >= 0.6 ? "pos" : r.is_ratio >= 0.4 ? "text-yellow" : "neg" },
    { label: "Degradation", val: r.degradation_pct + "%", cls: r.degradation_pct <= 30 ? "pos" : r.degradation_pct <= 60 ? "text-yellow" : "neg" },
    { label: "Consistency", val: r.consistency_score + "%", cls: r.consistency_score >= 60 ? "pos" : "neg" },
    { label: "Cost: Slippage", val: fmtMoney(r.oos_total_slippage) },
    { label: "Cost: Commission", val: fmtMoney(r.oos_total_commission) },
    { label: "Folds", val: r.n_folds },
  ];

  // fold table
  const foldHead = `<tr><th>Fold</th><th>Train Trades</th><th>Train WR</th><th>Train PF</th><th>Train P&L</th><th>Test Trades</th><th>Test WR</th><th>Test PF</th><th>Test P&L</th><th>Params</th></tr>`;
  const foldRows = r.folds.map((f) => {
    const tp = f.train_net >= 0, tep = f.test_net >= 0;
    return `<tr>
      <td>${f.fold + 1}</td>
      <td>${f.train_trades}</td>
      <td class="${f.train_wr >= 50 ? 'pos' : 'neg'}">${f.train_wr}%</td>
      <td class="${f.train_pf >= 1 ? 'pos' : 'neg'}">${f.train_pf}</td>
      <td class="${tp ? 'pos' : 'neg'}">${fmtMoney(f.train_net)}</td>
      <td>${f.test_trades}</td>
      <td class="${f.test_wr >= 50 ? 'pos' : 'neg'}">${f.test_wr}%</td>
      <td class="${f.test_pf >= 1 ? 'pos' : 'neg'}">${f.test_pf}</td>
      <td class="${tep ? 'pos' : 'neg'}">${fmtMoney(f.test_net)}</td>
      <td class="dim">z${f.best_params.min_zone_score ?? '--'} rr${f.best_params.min_rr ?? '--'}</td>
    </tr>`;
  }).join("");

  body.innerHTML = `
    <div class="wf-verdict" style="border-left:3px solid ${verdictColor};padding:8px 12px;margin-bottom:10px">
      <span style="color:${verdictColor};font-weight:700;font-size:14px">${esc(verdictText)}</span>
      <span class="dim" style="margin-left:8px">${r.n_folds} folds in ${r.elapsed_s}s</span>
    </div>
    <div class="stats-grid">${stats.map((s) =>
      `<div class="stat"><div class="stat-label">${s.label}</div><div class="stat-value ${s.cls || ''}">${s.val}</div></div>`
    ).join("")}</div>
    <div class="table-wrap" style="margin-top:10px"><table class="data-table">
      <thead>${foldHead}</thead>
      <tbody>${foldRows}</tbody>
    </table></div>
    <div style="margin-top:8px;font-size:11px;color:var(--dim)">
      <b>IS/OOS Ratio</b> = avg in-sample PF / avg out-of-sample PF. Closer to 1.0 = less overfitting.
      <b>Consistency</b> = % of folds with positive OOS P&L. Above 60% is healthy.
      <b>Degradation</b> = how much OOS PF drops vs IS PF. Below 30% is excellent.
    </div>`;
}

function initWalkForward() {
  $("wf-run").addEventListener("click", runWalkForward);
}

initWalkForward();

/* ---------- ML Ranker ---------- */
async function loadMLRankInfo() {
  try {
    const info = await api("/api/ml-rank/info");
    const el = $("ml-rank-info");
    if (!el) return;
    if (!info.trained) {
      el.innerHTML = '<span class="dim">No model trained yet</span>';
      return;
    }
    el.innerHTML = `
      <span class="stat-label">Trained</span> <span class="stat-value">${info.trained_at ? new Date(info.trained_at).toLocaleDateString() : "?"}</span>
      <span class="stat-label" style="margin-left:12px">Trades</span> <span class="stat-value">${info.n_trades}</span>
      <span class="stat-label" style="margin-left:12px">CV AUC</span> <span class="stat-value ${info.cv_auc >= 0.6 ? 'pos' : 'text-yellow'}">${info.cv_auc}</span>
    `;
  } catch (e) { /* ignore */ }
}

async function retrainMLRank() {
  const btn = $("ml-retrain-btn");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = "Training…";
  try {
    const r = await apiPost("/api/ml-rank/retrain");
    if (r.error) {
      toast("Retrain failed: " + r.error, "down");
    } else {
      toast(`Model retrained: AUC=${r.cv_auc}, accuracy=${r.cv_accuracy}, ${r.n_trades} trades (${r.elapsed_s}s)`, "up");
      await loadMLRankInfo();
    }
  } catch (e) {
    toast("Retrain error: " + e.message, "down");
  }
  btn.disabled = false;
  btn.textContent = "Retrain Model";
}

function initMLRank() {
  loadMLRankInfo();
  const btn = $("ml-retrain-btn");
  if (btn) btn.addEventListener("click", retrainMLRank);
}

/* ---------- Pattern Win Rates ---------- */
async function loadPatternWinRates() {
  const el = $("pattern-winrates-body");
  if (!el) return;
  try {
    const data = await api("/api/pattern-win-rates");
    const patterns = data.patterns || [];
    if (!patterns.length) {
      el.innerHTML = '<tr class="empty-row"><td colspan="7">No pattern data yet — trades need journaled setup types</td></tr>';
      return;
    }
    el.innerHTML = patterns.map(p => {
      const wrClass = p.win_rate >= 60 ? "pos" : p.win_rate >= 40 ? "text-yellow" : "neg";
      const pnlClass = p.total_pnl >= 0 ? "pos" : "neg";
      return `<tr>
        <td><b>${esc(p.setup)}</b></td>
        <td>${p.trades}</td>
        <td class="${wrClass}">${p.win_rate}%</td>
        <td class="pos">${p.wins}</td>
        <td class="neg">${p.losses}</td>
        <td class="${pnlClass}">${fmtMoney(p.total_pnl)}</td>
        <td class="${p.avg_pnl >= 0 ? 'pos' : 'neg'}">${fmtMoney(p.avg_pnl)}</td>
      </tr>`;
    }).join("");
  } catch (e) {
    el.innerHTML = '<tr class="empty-row"><td colspan="7">Failed to load pattern data</td></tr>';
  }
}

function initPatternWinRates() {
  loadPatternWinRates();
}

initMLRank();
initPatternWinRates();