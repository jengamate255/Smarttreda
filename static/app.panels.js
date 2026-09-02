/* Split from app.js - loaded in order; shared global scope. */
let lastImminent = false;
/* ---------- Health ---------- */
async function loadHealth() {
  try {
    const h = await api("/api/health");
    const t = h.terminal;
    $("h-terminal").textContent =
      t && t.company ? `${t.company}${t.build ? " b" + t.build : ""}` : "--";
  } catch (e) { /* offline - ignore */ }
}

/* ---------- Account / positions / history ---------- */
async function loadSummary() {
  if (Date.now() - wsAliveAt < POLL_MS) return;
  showSkeleton($("positions-body"), "rows", 5);
  showSkeleton($("trades-body"), "rows", 5);
  showSkeleton($("risk-body"), "bars", 4);
  try {
    const [acc, pos, hist, risk] = await Promise.all([
      api("/api/account"),
      api("/api/positions"),
      api("/api/history?days=30"),
      api("/api/risk"),
    ]);
    setStatus(true, "Connected");
    applyAccount(acc);
    renderPositions(pos.positions);
    renderStats(hist.stats);
    renderTrades(hist.recent);
    renderCorrelationRisk(risk);
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadSummary", 0, 0, e);
    const msg = e && e.message ? e.message : "";
    if (/not connected|terminal/i.test(msg)) setStatus(false, "MT5 disconnected");
    else if (/no rates|not found|symbol/i.test(msg)) setStatus(false, "Data unavailable: " + msg);
    else setStatus(false, "Offline");
  }
}

/* ---------- Correlation Risk ---------- */
function renderCorrelationRisk(risk) {
  const el = $("corr-risk");
  if (!el) return;
  const groups = risk && risk.correlation_groups;
  if (!groups || Object.keys(groups).length === 0) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  const tags = Object.entries(groups).map(([g, v]) =>
    `<span class="corr-tag">${esc(g)}: ${fmtMoney(v)}</span>`
  ).join("");
  el.innerHTML = `<span class="corr-label">Correlation Risk:</span> ${tags}`;
}

function _positionsSignature(positions) {
  // Static shape only - volatile fields (price_current, total) excluded so
  // live ticks update cells in place instead of rebuilding the table.
  return (positions || []).map(p =>
    `${p.ticket}|${p.symbol}|${p.type}|${p.volume}|${p.price_open}|${p.sl ?? ""}|${p.tp ?? ""}`
  ).join(";");
}

function renderPositions(positions) {
  state.positions = positions || [];
  $("pos-badge").textContent = state.positions.length;
  const body = $("positions-body");

  if (!state.positions.length) {
    if (body.dataset.sig !== "empty") {
      body.innerHTML = '<tr class="empty-row"><td colspan="9">📁 No open positions — load a chart and execute a plan, or wait for the spike watcher</td></tr>';
      body.dataset.sig = "empty";
    }
    return;
  }

  // Fast path: same position set -> patch volatile cells in place.
  const sig = _positionsSignature(state.positions);
  const existing = body.querySelectorAll("tr[data-ticket]");
  if (sig === body.dataset.sig && existing.length === state.positions.length) {
    let totalPnl = 0;
    for (const p of state.positions) {
      totalPnl += p.total;
      const tr = body.querySelector(`tr[data-ticket="${p.ticket}"]`);
      if (!tr) continue;
      tr.children[4].textContent = fmtPrice(p.price_current);
      const pnlTd = tr.children[7];
      pnlTd.textContent = fmtMoney(p.total);
      pnlTd.className = p.total >= 0 ? "pos" : "neg";
    }
    const tfoot = body.querySelector("tfoot td:last-child");
    if (tfoot) {
      tfoot.textContent = fmtMoney(totalPnl);
      tfoot.className = totalPnl >= 0 ? "pos" : "neg";
    }
    return;
  }

  body.dataset.sig = sig;
  body.innerHTML = "";
  let totalPnl = 0;
  for (const p of state.positions) {
    totalPnl += p.total;
    const tr = document.createElement("tr");
    tr.dataset.ticket = p.ticket;
    const isBuy = p.type === "BUY";
    tr.innerHTML = `
      <td>${esc(p.symbol)}</td>
      <td style="color:${isBuy ? C.up : C.down}">${esc(p.type)}</td>
      <td>${esc(p.volume)}</td>
      <td>${fmtPrice(p.price_open)}</td>
      <td>${fmtPrice(p.price_current)}</td>
      <td>${p.sl ? fmtPrice(p.sl) : "--"}</td>
      <td>${p.tp ? fmtPrice(p.tp) : "--"}</td>
      <td class="${p.total >= 0 ? "pos" : "neg"}">${fmtMoney(p.total)}</td>
      <td class="col-actions pos-actions">
        <input class="sl-input" data-ticket="${p.ticket}" placeholder="SL" value="${p.sl || ""}">
        <input class="tp-input" data-ticket="${p.ticket}" placeholder="TP" value="${p.tp || ""}">
        <button class="btn tiny" data-act="sltp" data-ticket="${p.ticket}" title="Apply SL/TP" aria-label="Apply SL and TP for ticket">SL/TP</button>
        <button class="btn tiny danger" data-act="close" data-ticket="${p.ticket}" title="Close position" aria-label="Close position">âœ•</button>
      </td>`;
    body.appendChild(tr);
  }

  const tfoot = document.createElement("tfoot");
  tfoot.innerHTML = `
    <tr><td colspan="8" style="text-align:right;color:${C.dim};font-weight:600">Total</td>
    <td class="${totalPnl >= 0 ? "pos" : "neg"}" style="font-weight:600">${fmtMoney(totalPnl)}</td></tr>`;
  body.appendChild(tfoot);

  if (!window._positionsSortable) {
    window._positionsSortable = makeSortable($("positions-table"), {
      pageSize: 25,
      numericCols: [2, 3, 4, 5, 6, 7, 8],
    });
  }
  window._positionsSortable.setRows($("positions-body").querySelectorAll("tr[data-ticket]"));
}

$("positions-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const ticket = parseInt(btn.dataset.ticket, 10);
  try {
    if (btn.dataset.act === "close") {
      if (!confirm(`Close position ${ticket}?`)) return;
      const r = await apiPost(`/api/positions/${ticket}/close`);
      toast(r.accepted ? `Closed #${r.ticket || ticket}` : `Close failed: ${r.error}`, r.accepted ? "up" : "down");
    } else {
      const row = btn.closest("tr");
      const slRaw = row.querySelector(".sl-input").value;
      const tpRaw = row.querySelector(".tp-input").value;
      const body = {};
      if (slRaw !== "") body.sl = parseFloat(slRaw);
      if (tpRaw !== "") body.tp = parseFloat(tpRaw);
      if (!body.sl && !body.tp) { toast("Enter SL and/or TP", "down"); return; }
      const r = await apiPost(`/api/positions/${ticket}/sltp`, body);
      toast(r.accepted ? `#${ticket} SL/TP updated` : `Update failed: ${r.error}`, r.accepted ? "up" : "down");
    }
  } catch (err) {
    toast(`Action failed: ${err.message}`, "down");
  }
  loadPositionsOnly();
});

async function loadPositionsOnly() {
  try {
    const pos = await api("/api/positions");
    renderPositions(pos.positions);
  } catch (e) { /* ignore */ }
}

function renderStats(s) {
  $("stat-period").textContent = "last 30 days";
  setMoney($("s-net"), s.net_profit);
  $("s-winrate").textContent = s.win_rate.toFixed(1) + "%";
  $("s-winrate").style.color = s.win_rate >= 50 ? C.up : C.down;
  $("s-pf").textContent = s.profit_factor >= 999 ? "\u221e" : s.profit_factor.toFixed(2);
  $("s-pf").style.color = s.profit_factor >= 1 ? C.up : C.down;
  $("s-trades").textContent = s.trades;
  setMoney($("s-avgwin"), s.avg_win);
  setMoney($("s-avgloss"), s.avg_loss);
}

function renderTrades(trades) {
  const body = $("trades-body");
  body.innerHTML = "";
  if (!trades.length) {
    body.innerHTML = '<tr class="empty-row"><td colspan="8">📂 No closed trades in this period yet</td></tr>';
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  for (const t of trades) {
    const tr = document.createElement("tr");
    const total = t.profit + t.swap;
    tr.innerHTML = `
      <td>${esc(t.time)}</td>
      <td>${esc(t.symbol)}</td>
      <td style="color:${t.type === "BUY" ? C.up : C.down}">${esc(t.type)}</td>
      <td>${esc(t.volume)}</td>
      <td>${fmtPrice(t.price)}</td>
      <td class="${t.profit >= 0 ? "pos" : "neg"}">${fmtMoney(t.profit)}</td>
      <td>${fmtMoney(t.swap)}</td>
      <td class="${total >= 0 ? "pos" : "neg"}">${fmtMoney(total)}</td>`;
    body.appendChild(tr);
    if (t.time && t.time.startsWith(today)) addToWatchlist(t.symbol); // auto-add last traded
  }

  if (!window._tradesSortable) {
    window._tradesSortable = makeSortable($("trades-table"), {
      pageSize: 25,
      numericCols: [3, 5, 6, 7],
      dateCols: [0],
    });
  }
  window._tradesSortable.setRows($("trades-body").querySelectorAll("tr:not(.empty-row)"));
}

/* ---------- Trade plan ---------- */
async function loadPlan() {
  const symbol = state.symbol.trim().toUpperCase() || "EURUSD";
  try {
    const plan = await api(
      `/api/trade-plan?symbol=${encodeURIComponent(symbol)}&timeframe=${state.timeframe}&lookback=${state.count}`
    );
    state.plan = plan;
    renderPlan(plan);
  } catch (e) {
    $("plan-badge").className = "plan-badge hidden";
    $("plan-body").innerHTML =
      `<div class="plan-wait">Plan unavailable: ${esc(e && e.message ? e.message : "error")}</div>`;
  }
}

function renderPlan(plan) {
  const badge = $("plan-badge");
  const tf = $("plan-tf");
  const body = $("plan-body");
  const wait = plan && plan.action === "NONE";

  if (wait || !plan) {
    badge.className = "plan-badge wait";
    badge.textContent = "WAIT";
    tf.textContent = plan ? `${plan.symbol} ${plan.timeframe}` : "";
    const reason = plan && (plan.reason || (plan.errors || []).join("; "));
    body.innerHTML = `<div class="plan-wait">No valid setup right now.</div>` +
      (reason ? `<div class="plan-reason">${esc(reason)}</div>` : "") +
      `<div class="plan-zone">${zoneLine(plan)}</div>`;
    return;
  }

  const buy = plan.action === "BUY";
  badge.className = `plan-badge ${buy ? "buy" : "sell"}`;
  badge.textContent = plan.action;
  tf.textContent = `${plan.symbol} ${plan.timeframe} Â· score ${plan.trade_score ?? "--"}`;

  const rows = [
    `<div class="plan-item"><div class="k">Entry</div><div class="v" style="color:${buy ? C.up : C.down}">${fmtPrice(plan.entry)}</div></div>`,
    `<div class="plan-item"><div class="k">SL (${plan.sl_pips ?? "--"} pips)</div><div class="v" style="color:${C.down}">${fmtPrice(plan.sl)}</div></div>`,
    `<div class="plan-item"><div class="k">TP (${plan.tp_pips ?? "--"} pips)</div><div class="v" style="color:${C.up}">${fmtPrice(plan.tp)}</div></div>`,
    `<div class="plan-item"><div class="k">Lots</div><div class="v">${plan.lots ?? "--"}</div></div>`,
    `<div class="plan-item"><div class="k">RR</div><div class="v" style="color:${(plan.risk_reward || 0) >= 3 ? C.up : C.yellow}">${plan.risk_reward ?? "--"}</div></div>`,
    `<div class="plan-item"><div class="k">Risk ${plan.risk_amount ?? "--"}</div><div class="v" style="color:${plan.valid ? C.up : C.dim}">${plan.confidence ?? "--"}%</div></div>`,
  ];
  body.innerHTML = `<div class="plan-grid">${rows.join("")}</div>` +
    `<div class="plan-reason">${esc(plan.reason)}</div>` +
    (plan.warnings && plan.warnings.length
      ? `<div class="plan-warn">âš  ${esc(plan.warnings.join(" Â· "))}</div>` : "") +
    (plan.errors && plan.errors.length
      ? `<div class="plan-err">${esc(plan.errors.join(" Â· "))}</div>` : "") +
    `<div class="plan-zone">${zoneLine(plan)}</div>` +
    `<button id="plan-execute" class="btn full ${buy ? "go" : "sell"}" ${plan.valid ? "" : "disabled"}>` +
    `Execute ${plan.action} Â· ${plan.lots ?? "--"} lots</button>`;
}

function zoneLine(plan) {
  if (!plan || !plan.zone_type || !plan.zone_upper) return "";
  const supply = plan.zone_type === "supply";
  return `<div class="plan-zone-txt" style="color:${supply ? C.down : C.up}">` +
    `Zone: ${plan.zone_type} ${plan.zone_strength ?? "--"}/100 Â· ` +
    `${fmtPrice(plan.zone_lower)} â€“ ${fmtPrice(plan.zone_upper)}</div>`;
}

$("plan-body").addEventListener("click", async (e) => {
  if (e.target.id !== "plan-execute") return;
  const plan = state.plan;
  if (!plan || plan.action === "NONE") return;
  if (!confirm(`Execute ${plan.action} ${plan.symbol} ${plan.lots} lots?\nEntry ${fmtPrice(plan.entry)} Â· SL ${fmtPrice(plan.sl)} Â· TP ${fmtPrice(plan.tp)}`)) return;
  const btn = $("plan-execute");
  btn.disabled = true;
  btn.textContent = "Executing...";
  try {
    const r = await apiPost("/api/execute-plan", {
      symbol: plan.symbol,
      timeframe: plan.timeframe || state.timeframe,
    });
    toast(r.accepted ? `Order sent â€” ticket #${r.ticket}` : `Rejected: ${r.error || r.retcode}`, r.accepted ? "up" : "down");
  } catch (err) {
    toast(`Execute failed: ${err.message}`, "down");
  } finally {
    btn.disabled = false;
    btn.textContent = "Execute";
  }
  loadPositionsOnly();
});

/* ---------- Risk ---------- */
async function loadRisk() {
  showSkeleton($("risk-body"), "bars", 4);
  try {
    const r = await api("/api/risk");
    if (r.error) throw new Error(r.error);
    state.risk = r;
    const badge = $("risk-badge");
    badge.textContent = r.trading_allowed ? "OK" : "BLOCKED";
    badge.style.background = r.trading_allowed ? "rgba(38,166,154,0.2)" : "rgba(239,83,80,0.25)";
    badge.style.color = r.trading_allowed ? C.up : C.down;
    $("h-risk-badge").className = "risk-badge" + (r.warnings.length ? "" : " hidden");
    $("h-risk-badge").textContent = r.warnings.length ? "âš  RISK" : "";

    const thresholdColor = (pct) => pct >= 100 ? "var(--red)" : pct >= 80 ? "var(--yellow)" : pct >= 60 ? "var(--orange)" : "var(--green)";
    const bar = (label, used, pct, color, limit) => {
      const tc = thresholdColor(pct);
      const over = pct >= 100;
      return `
        <div class="risk-row">
          <div class="risk-label">${label} <b>${fmtMoney(used)}</b> <span class="risk-pct" style="color:${tc}">${pct.toFixed(1)}%</span></div>
          <div class="risk-bar" style="position:relative">
            <div class="risk-fill ${over ? "over" : ""}" style="width:${Math.min(100, pct)}%;background:${color}"></div>
            ${limit ? `<div class="risk-threshold" style="left:${Math.min(100, (limit / (limit * (100 / pct))))}%"></div>` : ""}
          </div>
        </div>`;
    };
    $("risk-body").innerHTML =
      bar("Daily loss", r.daily_pnl, r.daily_loss_percent_used, C.orange, r.daily_loss_limit) +
      bar("Weekly loss", r.weekly_pnl, r.weekly_loss_percent_used, C.yellow, r.weekly_loss_limit) +
      bar("Open risk", r.open_risk, r.open_risk_percent_used, C.accent, r.open_risk_limit) +
      `<div class="risk-extra">Open positions ${r.open_positions}/${r.max_positions ?? "âˆž"} Â· Exposure ${r.total_exposure?.toLocaleString?.() ?? r.total_exposure}</div>` +
      (r.mode != null || r.max_trades_per_day != null
        ? `<div class="risk-extra">Mode <b>${esc(r.mode ?? "normal")}</b> Â· Trades ${r.trades_today ?? "?"}/${r.max_trades_per_day ?? "?"}` +
          (r.max_trades_per_day_green_bonus ? ` (+${r.max_trades_per_day_green_bonus} green)` : "") +
          `${r.trading_allowed ? "" : " Â· <b style=\"color:var(--red)\">BLOCKED</b>"}</div>`
        : "") +
      (r.warnings.length ? `<div class="plan-warn">${esc(r.warnings.join(" Â· "))}</div>` : "");
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadRisk", 0, 0, e);
    $("risk-body").innerHTML = `<div class="plan-empty">Risk unavailable: ${esc(e.message)}</div>`;
  }
}

/* ---------- Compliance Dashboard ---------- */
async function loadCompliance() {
  showSkeleton($("comp-body"), "bars", 4);
  try {
    const c = await api("/api/compliance");
    if (c.error) throw new Error(c.error);
    state.compliance = c;
    
    const badge = $("comp-badge");
    badge.textContent = c.mode.toUpperCase();
    badge.style.background = c.mode_color + "33";
    badge.style.color = c.mode_color;
    badge.style.borderColor = c.mode_color;
    
    // Drawdown gauge
    const ddGaugePct = Math.min(100, c.dd_gauge_pct || 0);
    const ddColor = c.mode === "halted" ? "var(--red)" : c.mode === "conservative" ? "var(--orange)" : "var(--green)";
    
    // Daily loss bar
    const dailyUsedPct = Math.min(100, c.daily_used_pct || 0);
    
    // Slots
    const tradesToday = c.trades_today || 0;
    const maxTrades = c.max_trades || 0;
    const tradesRemaining = c.trades_remaining || 0;
    
    $("comp-body").innerHTML = `
      <div class="comp-gauge">
        <div class="gauge-item">
          <div class="gauge-label">Mode</div>
          <div class="gauge-value ${c.mode}" style="color: ${c.mode_color}">${c.mode.toUpperCase()}</div>
        </div>
        <div class="gauge-item">
          <div class="gauge-label">Drawdown</div>
          <div class="gauge-value ${c.mode}">${c.dd_pct.toFixed(2)}%</div>
        </div>
        <div class="gauge-item">
          <div class="gauge-label">Daily Loss</div>
          <div class="gauge-value">${c.daily_used_pct.toFixed(1)}%</div>
        </div>
        <div class="gauge-item">
          <div class="gauge-label">Daily P&L</div>
          <div class="gauge-value ${c.daily_pnl >= 0 ? "pos" : "neg"}">${c.daily_pnl >= 0 ? "+" : ""}${fmtMoney(c.daily_pnl)}</div>
        </div>
      </div>
      
      <div class="dd-gauge">
        <label>Drawdown Gauge: <span style="color: ${c.mode === 'halted' ? 'var(--red)' : c.mode === 'conservative' ? 'var(--orange)' : 'var(--green)'}">${c.dd_pct.toFixed(2)}%</span> / Halt at ${c.halt_threshold_pct}%</label>
        <div class="dd-bar">
          <div class="dd-fill ${c.mode}" style="width: ${Math.min(100, c.dd_gauge_pct || 0)}%"></div>
          <div class="dd-threshold" style="left: ${Math.min(100, (c.halt_threshold_pct / (c.max_drawdown_pct || 8)) * 100)}%"></div>
        </div>
        <div class="dd-markers">
          <span>0%</span>
          <span>${c.halt_threshold_pct}% (halt)</span>
          <span>${c.max_drawdown_pct}% (max)</span>
        </div>
      </div>
      
      <div class="slots-row">
        <div class="slot-item">
          <div class="slot-label">Trades Today</div>
          <div class="slot-value">${c.trades_today || 0} / ${c.max_trades || 0}</div>
        </div>
        <div class="slot-item">
          <div class="slot-label">Slots Left</div>
          <div class="slot-value ${c.trades_remaining === 0 ? 'neg' : ''}">${c.trades_remaining || 0}</div>
        </div>
        <div class="slot-item">
          <div class="slot-label">Green Bonus</div>
          <div class="slot-value">${c.green_bonus || 0}</div>
        </div>
        <div class="slot-item">
          <div class="slot-label">Daily P&L</div>
          <div class="slot-value ${c.daily_pnl >= 0 ? 'pos' : 'neg'}">${c.daily_pnl >= 0 ? '+' : ''}${fmtMoney(c.daily_pnl)}</div>
        </div>
      </div>
      ${c.halt_reason ? `<div class="plan-warn" style="margin-top: 8px;">âš  ${esc(c.halt_reason)}</div>` : ""}
    `;
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadCompliance", 0, 0, e);
    $("comp-body").innerHTML = `<div class="plan-empty">Compliance unavailable: ${esc(e.message)}</div>`;
  }
}

/* ---------- Auto Trader (deployed unattended runner) ---------- */
async function loadAutoTrader() {
  const body = $("at-body");
  if (!body) return;
  try {
    const at = await api("/api/autotrader/status");
    if (at.error) throw new Error(at.error);

    const running = at.task_state === "Running" || at.task_state === "Ready";
    const killed = !!at.kill_switch;
    const badge = $("at-state-badge");
    badge.textContent = killed ? "PAUSED" : (running ? at.task_state.toUpperCase() : at.task_state.toUpperCase());
    badge.style.background = killed ? "var(--orange)33" : (running ? "var(--green)33" : "var(--red)33");
    badge.style.color = killed ? "var(--orange)" : (running ? "var(--green)" : "var(--red)");
    $("at-cycles-sub").textContent = `${at.cycles || 0} cycles`;

    const planRows = Object.entries(at.plans || {}).map(([sym, p]) => {
      const conf = p.confidence != null ? Number(p.confidence).toFixed(1) : "--";
      const confOk = p.confidence != null && p.confidence >= 90;
      return `<tr>
        <td class="pos">${esc(sym)}</td>
        <td>${esc(p.action || "--")}</td>
        <td class="${confOk ? 'pos' : ''}">${conf}%</td>
        <td class="small" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(p.reason || '')}">${esc(p.reason || "--")}</td>
      </tr>`;
    }).join("");

    const v = at.last_validation || {};
    const vHtml = v.available ? `
      <div class="slot-item" title="${esc(v.file || '')}">
        <div class="slot-label">Monthly Validation</div>
        <div class="slot-value ${String(v.action || '').startsWith("CONTINUE") ? 'pos' : 'neg'}">${String(v.action || "--").split(" - ")[0]}</div>
      </div>` : `
      <div class="slot-item">
        <div class="slot-label">Monthly Validation</div>
        <div class="slot-value">not run</div>
      </div>`;

    body.innerHTML = `
      <div class="slots-row">
        <div class="slot-item">
          <div class="slot-label">Task</div>
          <div class="slot-value ${running ? 'pos' : 'neg'}">${esc(at.task_state)}</div>
        </div>
        <div class="slot-item">
          <div class="slot-label">Kill Switch</div>
          <div class="slot-value ${killed ? 'neg' : 'pos'}">${killed ? "ENGAGED" : "off"}</div>
        </div>
        ${vHtml}
        <div class="slot-item">
          <div class="slot-label">Cycles</div>
          <div class="slot-value">${at.cycles || 0}</div>
        </div>
      </div>
      <table class="data-table" style="margin-top:8px;">
        <thead><tr><th>Symbol</th><th>Plan</th><th>Conf</th><th>Status</th></tr></thead>
        <tbody>${planRows || '<tr><td colspan="4" class="plan-empty">No evaluations yet</td></tr>'}</tbody>
      </table>
      <div style="margin-top:10px;display:flex;gap:8px;">
        ${killed
          ? `<button class="btn small" onclick="AutoTraderCtl.kill(false)">▶ Resume</button>`
          : `<button class="btn small danger" onclick="AutoTraderCtl.kill(true)">⏸ Pause (kill switch)</button>`}
        <button class="btn small" onclick="AutoTraderCtl.toggleLog()">📜 Log</button>
      </div>
    `;

    const logEl = $("at-log-lines");
    if (logEl) {
      logEl.textContent = (at.log_tail || []).join("\n") || "(no log output yet)";
    }
  } catch (e) {
    body.innerHTML = `<div class="plan-empty">Auto trader unavailable: ${esc(e.message)}</div>`;
  }
}

/* Auto Trader controls (kill switch + log toggle) */
const AutoTraderCtl = {
  async kill(engage) {
    if (engage && !confirm("Engage kill switch?\\n\\nThe auto trader pauses at its next cycle. Open positions are NOT closed.")) return;
    try {
      if (engage) await apiPost("/api/autotrader/kill");
      else await apiDelete("/api/autotrader/kill");
      loadAutoTrader();
    } catch (e) {
      alert("Failed: " + (e.message || e));
    }
  },
  toggleLog() {
    const el = $("at-log");
    if (!el) return;
    el.classList.toggle("hidden");
  }
};
window.AutoTraderCtl = AutoTraderCtl;

/* ---------- Forex Breakout Panel ---------- */
async function loadForexBreakout() {
  showSkeleton($("fb-body"), "rows", 5);
  try {
    const fb = await api("/api/forex-breakout/scan?min_confidence=25&range_bars=24&min_rr=1.2&rr=1.5&risk_percent=0.5");
    if (fb.error) throw new Error(fb.error);
    
    const signals = fb.signals || [];
    const count = fb.count || 0;
    
    if (!signals.length) {
      $("fb-body").innerHTML = `<div class="fb-empty">No breakout candidates above ${25}% confidence</div>`;
      return;
    }
    
    const rows = signals.map(s => `
      <div class="signal-row" data-symbol="${esc(s.symbol)}">
        <div class="signal-header">
          <span class="signal-symbol">${esc(s.symbol)}</span>
          <span class="signal-side ${s.side.toLowerCase()}">${esc(s.side)}</span>
          <span class="signal-conf">${s.confidence.toFixed(1)}%</span>
        </div>
        <div class="signal-details">
          <div class="signal-detail"><span>Entry</span><strong>${s.entry.toFixed(s.symbol.startsWith("JPY") || s.symbol.includes("JPY") ? 3 : 5)}</strong></div>
          <div class="signal-detail"><span>SL</span><strong>${s.sl.toFixed(s.symbol.startsWith("JPY") || s.symbol.includes("JPY") ? 3 : 5)}</strong></div>
          <div class="signal-detail"><span>TP</span><strong>${s.tp.toFixed(s.symbol.startsWith("JPY") || s.symbol.includes("JPY") ? 3 : 5)}</strong></div>
          <div class="signal-detail"><span>RR</span><strong>${s.rr}</strong></div>
          <div class="signal-detail"><span>Range</span><strong>${s.range_low.toFixed(5)} â€“ ${s.range_high.toFixed(5)}</strong></div>
          <div class="signal-detail"><span>ATR</span><strong>${s.atr.toFixed(5)}</strong></div>
        </div>
      </div>
    `).join("");
    
    $("fb-body").innerHTML = `
      <div class="signal-list">${rows}</div>
      <div class="fb-empty" style="font-size:11px;margin-top:8px;color:var(--dim);">
        Scanned ${fb.symbols_scanned} symbols Â· ${count} signals Â· min conf 25%
      </div>
    `;
    
    // Add click handler to load symbol on chart
    document.querySelectorAll("#fb-body .signal-row").forEach(row => {
      row.style.cursor = "pointer";
      row.addEventListener("click", () => {
        const sym = row.dataset.symbol;
        if (sym && window.loadSymbolOnChart) {
          window.loadSymbolOnChart(sym);
        }
      });
    });
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadForexBreakout", 0, 0, e);
    $("fb-body").innerHTML = `<div class="fb-empty">Forex breakout unavailable: ${esc(e.message)}</div>`;
  }
}

/* ---------- Sessions ---------- */
async function loadSessions() {
  showSkeleton($("sessions-body"), "cards", 3);
  try {
    const s = await api("/api/sessions");
    if (s.error) throw new Error(s.error);
    const setups = (s.setups || []).slice(0, 4);
    const sessions = (s.sessions || []).slice(0, 4);
    const pairs = (s.pairs || []).slice(0, 4);
    const col = (title, items, fmt) => `
      <div class="insight-col"><div class="insight-title">${title}</div>
      ${items.length ? items.map(fmt).join("") : '<div class="insight-empty">no data</div>'}</div>`;
    $("sessions-body").innerHTML =
      col("Top setups", setups, (x) =>
        `<div class="insight-row"><span>${esc(x.setup)}</span><span class="${x.net_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(x.net_pnl)} Â· ${x.win_rate.toFixed(0)}%</span></div>`) +
      col("Best hours (UTC)", sessions, (x) =>
        `<div class="insight-row"><span>${x.hour}:00</span><span class="${x.net_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(x.net_pnl)} Â· ${x.win_rate.toFixed(0)}%</span></div>`) +
      col("Best pairs", pairs, (x) =>
        `<div class="insight-row"><span>${esc(x.symbol)}</span><span class="${x.net_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(x.net_pnl)} Â· ${x.win_rate.toFixed(0)}%</span></div>`);
  } catch (e) {
    $("sessions-body").innerHTML = `<div class="plan-empty">Insights unavailable: ${esc(e.message)}</div>`;
  }
}

/* ---------- Alerts ---------- */
async function loadAlerts() {
  showSkeleton($("alerts-body"), "rows", 4);
  try {
    const a = await api("/api/alerts");
    if (a.error) throw new Error(a.error);
    state.alerts = a.alerts || [];
    renderAlerts();
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadAlerts", 0, 0, e);
    $("alerts-body").innerHTML = `<div class="plan-empty">Alerts unavailable: ${esc(e.message)}</div>`;
  }
}

function renderAlerts() {
  const alerts = state.alerts || [];
  
  if (!alerts.length) {
    $("alerts-body").innerHTML = `<div class="alert-empty">No alerts configured. Add one above.</div>`;
    return;
  }
  
  const rows = alerts.map(a => `
    <div class="alert-row" data-id="${esc(a.id)}">
      <div class="alert-header">
        <span class="alert-symbol">${esc(a.symbol)}</span>
        <span class="alert-condition ${a.condition}">${esc(a.condition)}</span>
        <span class="alert-name">${esc(a.name || "â€”")}</span>
        <span class="alert-price">${a.price.toFixed(a.symbol.includes("JPY") ? 3 : 5)}</span>
      </div>
      <div class="alert-actions">
        <button class="btn small" onclick="toggleAlert('${esc(a.id)}')">${a.active ? "Disable" : "Enable"}</button>
        <button class="btn small danger" onclick="deleteAlert('${esc(a.id)}')">Delete</button>
      </div>
    </div>
  `).join("");
  
  $("alerts-body").innerHTML = `<div class="alert-list">${rows}</div>`;
}

async function addAlert() {
  const symbol = $("#alert-symbol").value.trim().toUpperCase();
  const price = parseFloat($("#alert-price").value);
  const condition = $("#alert-condition").value;
  const name = $("#alert-name").value.trim();
  
  if (!symbol || isNaN(price)) {
    toast("Symbol and price required", "error");
    return;
  }
  
  try {
    const result = await api("/api/alerts", { method: "POST", body: JSON.stringify({ symbol, price, condition, name }) });
    if (result.error) throw new Error(result.error);
    toast("Alert added", "success");
    $("#alert-price").value = "";
    $("#alert-name").value = "";
    loadAlerts();
  } catch (e) {
    toast("Failed to add alert: " + e.message, "error");
  }
}

async function deleteAlert(id) {
  try {
    await api("/api/alerts/" + id, { method: "DELETE" });
    toast("Alert deleted", "success");
    loadAlerts();
  } catch (e) {
    toast("Failed to delete alert: " + e.message, "error");
  }
}

async function toggleAlert(id) {
  try {
    await apiPost("/api/alerts/" + id + "/toggle");
    toast("Alert toggled", "success");
    loadAlerts();
  } catch (e) {
    toast("Failed to toggle alert: " + e.message, "error");
  }
}

/* ---------- News ---------- */
async function loadNews() {
  showSkeleton($("news-body"), "rows", 6);
  try {
    const n = await api("/api/news?horizon_hours=24");
    state.news = n;
    $("news-provider").textContent = n.provider || "";
    const banner = $("news-banner");
    if (n.trading_blocked && n.imminent_events && n.imminent_events.length) {
      banner.classList.remove("hidden");
      $("news-banner-text").textContent = n.imminent_events
        .map((e) => `${e.currency} ${e.title}`).join(" Â· ");
      if (!lastImminent) {
        notify("High-impact news imminent", n.imminent_events.map((e) => `${e.currency} ${e.title}`).join(", "));
        toast("High-impact news within the hour â€” be careful", "down");
      }
      lastImminent = true;
    } else {
      banner.classList.add("hidden");
      lastImminent = false;
    }

    const body = $("news-body");
    body.innerHTML = "";
    if (!n.events || !n.events.length) {
      body.innerHTML = '<tr class="empty-row"><td colspan="6">No events in the next 24h (feed covers current week)</td></tr>';
      return;
    }
    for (const ev of n.events) {
      const imp = ev.impact;
      const color = imp === "high" ? C.down : imp === "medium" ? C.yellow : C.dim;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(ev.time.slice(5))}</td>
        <td>${esc(ev.currency)}</td>
        <td><span class="impact" style="color:${color};border-color:${color}">${imp.toUpperCase()}</span></td>
        <td style="text-align:left">${esc(ev.title)}</td>
        <td>${esc(ev.forecast || "--")}</td>
        <td>${esc(ev.previous || "--")}</td>`;
      body.appendChild(tr);
    }
  } catch (e) {
    $("news-body").innerHTML = `<tr class="empty-row"><td colspan="6">News unavailable: ${esc(e.message)}</td></tr>`;
  }
}

/* ---------- Equity ---------- */
async function loadEquity() {
  showSkeleton($("equity-chart"), "chart");
  showSkeleton($("equity-stats"), "stats");
  try {
    const eq = await api("/api/equity?days=30");
    state.equity = eq;
    renderEquity(eq);
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadEquity", 0, 0, e);
  }
}

function renderEquity(eq) {
  const canvas = $("equity-chart");
  const ctx2 = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = Math.max(200, rect.width * dpr);
  canvas.height = Math.max(120, rect.height * dpr);
  ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = canvas.width / dpr;
  const H = canvas.height / dpr;
  ctx2.clearRect(0, 0, W, H);

  const stats = $("equity-stats");
  if (!eq || !eq.dates.length) {
    ctx2.fillStyle = C.dim;
    ctx2.font = "12px Consolas";
    ctx2.textAlign = "center";
    ctx2.fillText("No deal history in period", W / 2, H / 2);
    stats.innerHTML = "";
    return;
  }

  $("equity-period").textContent = `last ${eq.dates.length} day(s)`;
  stats.innerHTML =
    `<span>Net <b class="${eq.total >= 0 ? "pos" : "neg"}">${fmtMoney(eq.total)}</b></span>` +
    `<span>Best <b class="pos">${fmtMoney(eq.best_day)}</b></span>` +
    `<span>Worst <b class="neg">${fmtMoney(eq.worst_day)}</b></span>` +
    `<span>Profitable days <b>${eq.profitable_days}/${eq.dates.length}</b></span>` +
    `<span>Floating <b class="${eq.floating >= 0 ? "pos" : "neg"}">${fmtMoney(eq.floating)}</b></span>`;

  const n = eq.dates.length;
  const padL = 12, padT = 22, padB = 18;
  const plotW = W - padL - 6;
  const balH = (H - padT - padB) * 0.58;
  const pnlTop = padT + balH + 8;
  const pnlH = H - pnlTop - padB;

  const bal = eq.balance;
  const equityLine = eq.balance.map((v) => v + eq.floating);
  const ma5 = [];
  for (let i = 0; i < n; i++) {
    const s = bal.slice(Math.max(0, i - 4), i + 1);
    ma5.push(s.reduce((a, b) => a + b, 0) / s.length);
  }

  const series = [];
  if (state.equityOpts.balance) series.push({ vals: bal, color: C.accent, label: "balance" });
  if (state.equityOpts.floating) series.push({ vals: equityLine, color: C.purple, label: "equity" });
  if (state.equityOpts.ma5) series.push({ vals: ma5, color: C.yellow, label: "MA5" });
  const all = series.flatMap((s) => s.vals);
  const balMin = Math.min(...all);
  const balMax = Math.max(...all);
  const balSpan = balMax - balMin || 1;
  const pnlAbs = Math.max(...eq.daily_pnl.map(Math.abs), 0.000001);

  const x = (i) => padL + (i + 0.5) * (plotW / n);
  const yBal = (v) => padT + balH - ((v - balMin) / balSpan) * balH;
  const yPnl = (v) => pnlTop + pnlH / 2 - (v / pnlAbs) * (pnlH / 2 - 4);

  /* drawdown shading (balance vs running max) */
  if (state.equityOpts.dd && state.equityOpts.balance) {
    let peak = -Infinity;
    const ddFill = [];
    for (const v of bal) { peak = Math.max(peak, v); ddFill.push([v, peak]); }
    const anyDd = ddFill.some(([v, pk]) => pk - v > 0.005);
    if (anyDd) {
      ctx2.fillStyle = "rgba(239,83,80,0.10)";
      ctx2.beginPath();
      ddFill.forEach(([v], i) => { const py = yBal(v); i === 0 ? ctx2.moveTo(x(i), py) : ctx2.lineTo(x(i), py); });
      ddFill.slice().reverse().forEach(([, pk], j) => {
        const i = n - 1 - j;
        ctx2.lineTo(x(i), yBal(pk));
      });
      ctx2.closePath();
      ctx2.fill();
    }
  }

  /* grid + balance labels */
  ctx2.strokeStyle = C.border;
  ctx2.fillStyle = C.dim;
  ctx2.font = "9px Consolas";
  ctx2.textAlign = "left";
  for (let k = 0; k <= 3; k++) {
    const v = balMin + (balSpan * k) / 3;
    const y = yBal(v);
    ctx2.beginPath();
    ctx2.moveTo(padL, y);
    ctx2.lineTo(W - 6, y);
    ctx2.stroke();
    ctx2.fillText(v.toFixed(0), 0, y + 3);
  }

  /* curves */
  for (const s of series) {
    ctx2.strokeStyle = s.color;
    ctx2.lineWidth = s.label === "MA5" ? 1.4 : 1.6;
    ctx2.setLineDash(s.label === "MA5" ? [4, 3] : []);
    ctx2.beginPath();
    s.vals.forEach((v, i) => {
      const px = x(i), py = yBal(v);
      if (i === 0) ctx2.moveTo(px, py);
      else ctx2.lineTo(px, py);
    });
    ctx2.stroke();
    ctx2.setLineDash([]);
    ctx2.font = "8px Consolas";
    ctx2.fillStyle = s.color;
    ctx2.fillText(s.label.toUpperCase(), padL + 2, padT - 6 - series.indexOf(s) * 10);
  }

  /* daily P&L bars */
  ctx2.strokeStyle = C.border;
  ctx2.beginPath();
  ctx2.moveTo(padL, pnlTop + pnlH / 2);
  ctx2.lineTo(W - 6, pnlTop + pnlH / 2);
  ctx2.stroke();

  const barW = Math.max(1, (plotW / n) * 0.6);
  for (let i = 0; i < n; i++) {
    const v = eq.daily_pnl[i];
    const top = v >= 0 ? yPnl(v) : pnlTop + pnlH / 2;
    const bh = Math.max(1, Math.abs(yPnl(v) - (pnlTop + pnlH / 2)));
    ctx2.fillStyle = v >= 0 ? C.up : C.down;
    ctx2.globalAlpha = 0.85;
    ctx2.fillRect(x(i) - barW / 2, top, barW, bh);
    ctx2.globalAlpha = 1;
  }

  /* date labels */
  ctx2.fillStyle = C.dim;
  ctx2.textAlign = "center";
  const step = Math.max(1, Math.round(n / 8));
  for (let i = 0; i < n; i += step) {
    ctx2.fillText(eq.dates[i].slice(5), x(i), H - 5);
  }
}

function toggleEquityOpt(key, el) {
  state.equityOpts[key] = el.checked;
  savePrefs();
  renderEquity(state.equity);
}

/* ---------- Journal ---------- */
async function loadJournal() {
  showSkeleton($("journal-body"), "rows", 6);
  try {
    const j = await api(`/api/journal?limit=500`);
    state.journal = j.entries || [];
    populateJournalFilters();
    renderJournal();
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadJournal", 0, 0, e);
  }
}

/* ---------- Journal Equity Curve ---------- */
async function loadJournalEquity() {
  try {
    const eq = await api("/api/journal/equity-curve?days=30");
    if (eq.error) throw new Error(eq.error);
    state.journalEquity = eq;
    if (equityTab === "equity") renderEquityChart();
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadJournalEquity", 0, 0, e);
  }
}

async function loadJournalDistribution() {
  try {
    const dist = await api("/api/journal/distribution?days=30");
    if (dist.error) throw new Error(dist.error);
    state.journalDistribution = dist;
    if (equityTab === "dist") renderDistributionChart();
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadJournalDistribution", 0, 0, e);
  }
}

/* ---------- Journal Equity Curve Render ---------- */
let equityTab = "equity";
function showEquityTab(tab) {
  equityTab = tab;
  const eqCanvas = $("equity-chart");
  const distCanvas = $("distribution-chart");
  const eqToggles = document.querySelector(".eq-toggles");
  if (tab === "equity") {
    eqCanvas.style.display = "block";
    distCanvas.style.display = "none";
    eqToggles.querySelectorAll(".check").forEach(c => c.style.display = "");
    renderEquityChart();
  } else {
    eqCanvas.style.display = "none";
    distCanvas.style.display = "block";
    eqToggles.querySelectorAll(".check").forEach(c => c.style.display = "none");
    renderDistributionChart();
  }
  document.getElementById("eq-tab-equity").classList.toggle("active", tab === "equity");
  document.getElementById("eq-tab-dist").classList.toggle("active", tab === "dist");
}

function renderEquityChart() {
  const eq = state.journalEquity;
  const canvas = $("equity-chart");
  if (!eq || !eq.points?.length) return;
  const ctx2 = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = Math.max(200, rect.width * dpr);
  canvas.height = Math.max(200, rect.height * dpr);
  ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = canvas.width / dpr;
  const H = canvas.height / dpr;
  ctx2.clearRect(0, 0, W, H);

  const stats = $("equity-stats");
  const n = eq.points.length;
  const padL = 12, padT = 22, padB = 18;
  const plotW = W - padL - 6;
  const plotH = H - padT - padB;

  const cum = eq.points.map(p => p.cumulative);
  const minC = Math.min(...cum);
  const maxC = Math.max(...cum);
  const span = maxC - minC || 1;

  const x = (i) => 12 + (i + 0.5) * (W - 18) / Math.max(1, n - 1);
  const y = (v) => H - 18 - ((v - Math.min(...cum)) / (maxC - minC || 1)) * (H - 40);

  /* drawdown shading */
  let peak = -Infinity;
  const ddFill = [];
  for (const v of eq.points.map(p => p.cumulative)) {
    peak = Math.max(peak, v);
    ddFill.push([v, peak]);
  }
  if (ddFill.some(([v, pk]) => pk - v > 0)) {
    ctx2.fillStyle = "rgba(239,83,80,0.10)";
    ctx2.beginPath();
    ddFill.forEach(([v], i) => {
      const py = H - 18 - ((v - Math.min(...cum)) / (maxC - minC || 1)) * (H - 40);
      i === 0 ? ctx2.moveTo(12 + (i + 0.5) * (W - 18) / Math.max(1, n - 1), py) : ctx2.lineTo(12 + (i + 0.5) * (W - 18) / Math.max(1, n - 1), py);
    });
    ddFill.slice().reverse().forEach(([, pk], j) => {
      const i = n - 1 - j;
      ctx2.lineTo(12 + (i + 0.5) * (W - 18) / Math.max(1, n - 1),
        H - 18 - ((pk - Math.min(...cum)) / (maxC - minC || 1)) * (H - 40));
    });
    ctx2.closePath();
    ctx2.fill();
  }

  /* grid */
  ctx2.strokeStyle = C.border;
  ctx2.fillStyle = C.dim;
  ctx2.font = "9px Consolas";
  ctx2.textAlign = "left";
  for (let k = 0; k <= 4; k++) {
    const v = Math.min(...cum) + (Math.max(...cum) - Math.min(...cum)) * k / 4;
    const y = H - 18 - ((v - Math.min(...cum)) / (maxC - minC || 1)) * (H - 40);
    ctx2.beginPath();
    ctx2.moveTo(12, y);
    ctx2.lineTo(W - 6, y);
    ctx2.stroke();
    ctx2.fillText(v.toFixed(0), 0, y + 3);
  }

  /* equity curve */
  ctx2.strokeStyle = "#26a69a";
  ctx2.lineWidth = 2;
  ctx2.beginPath();
  eq.points.forEach((p, i) => {
    const px = 12 + (i + 0.5) * (W - 18) / Math.max(1, n - 1);
    const py = H - 18 - ((p.cumulative - Math.min(...cum)) / (maxC - minC || 1)) * (H - 40);
    if (i === 0) ctx2.moveTo(px, py);
    else ctx2.lineTo(px, py);
  });
  ctx2.stroke();

  /* trade markers */
  eq.points.forEach((p, i) => {
    if (p.pnl !== 0) {
      const px = 12 + (i + 0.5) * (W - 18) / Math.max(1, n - 1);
      const py = H - 18 - ((p.cumulative - Math.min(...cum)) / (maxC - minC || 1)) * (H - 40);
      ctx2.fillStyle = p.pnl > 0 ? C.up : C.down;
      ctx2.beginPath();
      ctx2.arc(px, py, 3, 0, Math.PI * 2);
      ctx2.fill();
    }
  });

  /* stats */
  stats.innerHTML = `
    <span>Net <b class="${eq.net_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(eq.net_pnl)}</b></span>
    <span>Max DD <b class="neg">${fmtMoney(eq.max_dd)}</b></span>
    <span>Max Runup <b class="pos">${fmtMoney(eq.max_runup)}</b></span>
    <span>Trades <b>${eq.total_trades}</b></span>
  `;
}

/* ---------- Journal Distribution Render ---------- */
function renderDistributionChart() {
  const dist = state.journalDistribution;
  if (!dist || !dist.pnl_histogram?.bins?.length) return;

  const canvas = $("distribution-chart");
  if (!canvas) return;
  const ctx2 = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = Math.max(200, rect.width * dpr);
  canvas.height = Math.max(200, rect.height * dpr);
  ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = canvas.width / dpr;
  const H = canvas.height / dpr;
  ctx2.clearRect(0, 0, W, H);

  const bins = dist.pnl_histogram.bins;
  const counts = dist.pnl_histogram.counts;
  const maxCount = Math.max(...counts);
  const n = bins.length;
  const padL = 50, padT = 20, padB = 40, padR = 10;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const x = (i) => 50 + (i + 0.5) * (W - 60) / n;
  const y = (c) => H - 40 - (c / (Math.max(...counts) || 1)) * (H - 60);

  ctx2.fillStyle = "#26a69a";
  ctx2.strokeStyle = "rgba(38, 166, 154, 0.5)";
  for (let i = 0; i < n; i++) {
    if (counts[i] === 0) continue;
    const barW = Math.max(1, (canvas.width / n) * 0.7);
    const h = (counts[i] / (Math.max(...counts) || 1)) * (H - 60);
    const px = 50 + i * (W - 60) / n;
    const py = H - 40 - h;
    ctx2.fillRect(px, py, barW, h);
  }

  ctx2.fillStyle = "#d1d4dc";
  ctx2.font = "10px Consolas";
  ctx2.textAlign = "center";
  for (let i = 0; i < n; i += Math.max(1, Math.round(n / 10))) {
    ctx2.fillText(bins[i].toFixed(2), 50 + (i + 0.5) * (W - 60) / n, H - 20);
  }

  const stats = $("equity-stats");
  stats.innerHTML = `
    <span>Trades <b>${dist.total_trades || 0}</b></span>
    <span>Win Rate <b>${dist.win_rate?.toFixed(1)}%</b></span>
    <span>Avg Win <b class="pos">${fmtMoney(dist.avg_win)}</b></span>
    <span>Avg Loss <b class="neg">${fmtMoney(dist.avg_loss)}</b></span>
    <span>Profit Factor <b>${dist.profit_factor?.toFixed(2)}</b></span>
    <span>Avg R <b>${dist.avg_r?.toFixed(2)}</b></span>
    <span>Largest Win <b class="pos">${fmtMoney(dist.largest_win)}</b></span>
    <span>Largest Loss <b class="neg">${fmtMoney(dist.largest_loss)}</b></span>
  `;
}

