"use strict";

/* ---------- Error Reporting ---------- */
// errorReport is defined in app.core.js (loaded first)

// tvStyle is defined in app.core.js (loaded first)

// C is defined in app.core.js (loaded first)

window.addEventListener("error", (e) => errorReport.capture(e.message, e.filename, e.lineno, e.colno, e.error));
window.addEventListener("unhandledrejection", (e) => errorReport.capture(e.reason?.message || String(e.reason), "", 0, 0, e.reason));

// All const declarations (state, TFs, $, api, etc.) are in app.core.js (loaded first)

/* ---------- Local prefs ---------- */
function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    if (p.symbol) state.symbol = p.symbol;
    if (TFs.includes(p.timeframe)) state.timeframe = p.timeframe;
    if (p.count) state.count = p.count;
    if (p.bot != null) state.bot.on = p.bot;
    if (p.indicators) Object.assign(state.indicators, p.indicators);
    if (p.equityOpts) Object.assign(state.equityOpts, p.equityOpts);
    if (Array.isArray(p.watchlist) && p.watchlist.length) state.watchlist = p.watchlist;
  } catch (e) { /* corrupt prefs - ignore */ }
}

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      symbol: state.symbol,
      timeframe: state.timeframe,
      count: state.count,
      bot: state.bot.on,
      indicators: state.indicators,
      equityOpts: state.equityOpts,
      watchlist: state.watchlist,
    }));
  } catch (e) { /* storage unavailable - ignore */ }
}

/* ---------- API ---------- */
async function api(path, opts) {
  // opts: { method, body } - GET by default; body is JSON-encoded.
  const o = opts || {};
  const method = (o.method || "GET").toUpperCase();
  const headers = AUTH_TOKEN ? { "X-Auth-Token": AUTH_TOKEN } : {};
  let body;
  if (o.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = typeof o.body === "string" ? o.body : JSON.stringify(o.body);
  }
  const res = await fetch(path, { method, headers, body });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && (j.detail || j.error)) detail = j.detail || j.error;
    } catch (e) { /* non-JSON error body */ }
    throw new Error(detail);
  }
  try { return await res.json(); } catch (e) { return null; }
}

async function apiPost(path, body) {
  const headers = { "Content-Type": "application/json" };
  if (AUTH_TOKEN) headers["X-Auth-Token"] = AUTH_TOKEN;
  const res = await fetch(path, {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j = null;
  try { j = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    throw new Error((j && (j.detail || j.error)) || `HTTP ${res.status}`);
  }
  return j;
}

function setStatus(ok, text) {
  $("status-dot").className = `dot ${ok ? "on" : "off"}`;
  $("status-text").textContent = text;
}

function esc(s) {
  return s == null ? "" : String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

/* ---------- Toasts & notifications ---------- */
// toastQueue, TOAST_MAX are in app.core.js (loaded first)
// toastPermissionAsked is defined in app.core.js (loaded first)

function toast(msg, kind = "info", opts = {}) {
  const { persist = false, timeout = 5000, action } = opts;
  const container = $("toast-container") || createToastContainer();
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.setAttribute("role", "alert");
  el.setAttribute("aria-live", "polite");
  el.innerHTML = `<span>${esc(msg)}</span>`;
  if (action) {
    const btn = document.createElement("button");
    btn.className = "toast-action";
    btn.textContent = action.label;
    btn.onclick = () => { action.fn(); dismissToast(el); };
    el.appendChild(btn);
  }
  if (!persist) {
    const timer = setTimeout(() => dismissToast(el), timeout);
    el.dataset.timer = timer;
  }
  el.onclick = (e) => { if (e.target === el) dismissToast(el); };
  container.appendChild(el);
  toastQueue.push(el);
  if (toastQueue.length > TOAST_MAX) dismissToast(toastQueue[0]);
  requestAnimationFrame(() => el.classList.add("show"));
  if (kind === "error" || kind === "warning") notify(msg, "");
}

function dismissToast(el) {
  const timer = el.dataset.timer;
  if (timer) clearTimeout(timer);
  el.classList.remove("show");
  el.addEventListener("transitionend", () => el.remove(), { once: true });
  const idx = toastQueue.indexOf(el);
  if (idx >= 0) toastQueue.splice(idx, 1);
}

function createToastContainer() {
  const c = document.createElement("div");
  c.id = "toast-container";
  c.setAttribute("aria-live", "polite");
  c.setAttribute("aria-atomic", "true");
  document.body.appendChild(c);
  return c;
}

function notify(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(title, { body }); } catch (e) { /* unsupported */ }
}

document.addEventListener("click", () => {
  if (!toastPermissionAsked && "Notification" in window && Notification.permission === "default") {
    toastPermissionAsked = true;
    Notification.requestPermission().catch(() => {});
  }
});

/* ---------- Keyboard shortcuts ---------- */
// SHORTCUTS is in app.core.js (loaded first)

function registerShortcuts() {
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    const sc = SHORTCUTS.find((s) => s.key.toLowerCase() === e.key.toLowerCase() && !!s.shift === e.shiftKey);
    if (sc) {
      e.preventDefault();
      sc.action();
    }
  });
}

function showHelp() {
  const el = $("help-modal") || createHelpModal();
  el.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeModals() {
  document.querySelectorAll(".modal:not(.hidden)").forEach((m) => m.classList.add("hidden"));
  document.body.style.overflow = "";
  const sb = $("sidebar");
  if (sb && sb.classList.contains("open")) {
    sb.classList.remove("open");
    $("sidebar-backdrop")?.classList.add("hidden");
  }
}

function createHelpModal() {
  const rows = SHORTCUTS.map((s) =>
    `<tr><td><kbd>${s.shift ? "Shift+" : ""}${s.key.toUpperCase()}</kbd></td><td>${s.desc}</td></tr>`
  ).join("");
  const html = `
    <div id="help-modal" class="modal hidden" role="dialog" aria-modal="true" aria-labelledby="help-title">
      <div class="modal-backdrop" onclick="closeModals()"></div>
      <div class="modal-content">
        <div class="modal-header">
          <h3 id="help-title">Keyboard Shortcuts</h3>
          <button class="modal-close" onclick="closeModals()" aria-label="Close">&times;</button>
        </div>
        <table class="data-table">
          <thead><tr><th>Key</th><th>Action</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
  const wrapper = document.createElement("div");
  wrapper.innerHTML = html;
  document.body.appendChild(wrapper.firstElementChild);
  return $("help-modal");
}

/* ---------- Formatting ---------- */
function fmtPrice(p) {
  if (p == null || isNaN(p)) return "--";
  const a = Math.abs(p);
  const d = a >= 1000 ? 2 : a >= 100 ? 3 : 5;
  return p.toFixed(d);
}

function fmtMoney(v) {
  if (v == null || isNaN(v)) return "--";
  return (v >= 0 ? "+" : "") + v.toFixed(2);
}

function setMoney(el, v) {
  el.textContent = fmtMoney(v);
  el.classList.toggle("pos", v >= 0);
  el.classList.toggle("neg", v < 0);
}

function fmtWhen(iso) {
  if (!iso) return "--";
  try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
}

/* ---------- Skeleton loading ---------- */
function showSkeleton(el, type = "rows", count = 5) {
  if (!el) return;
  if (type === "rows") {
    el.innerHTML = Array.from({ length: count }, () =>
      `<tr class="skeleton-row"><td colspan="9"><div class="skeleton skeleton-row"></div></td></tr>`
    ).join("");
  } else if (type === "cards") {
    el.innerHTML = Array.from({ length: count }, () =>
      `<div class="skeleton skeleton-card"></div>`
    ).join("");
  } else if (type === "bars") {
    el.innerHTML = Array.from({ length: count }, () =>
      `<div class="skeleton skeleton-bar"></div>`
    ).join("");
  } else if (type === "stats") {
    el.innerHTML = Array.from({ length: 6 }, () =>
      `<div class="stat"><div class="skeleton skeleton-text short"></div><div class="skeleton skeleton-value"></div></div>`
    ).join("");
  } else if (type === "chart") {
    el.innerHTML = `<div class="skeleton" style="height:100%;width:100%"></div>`;
  } else {
    el.innerHTML = `<div class="skeleton skeleton-text"></div>`;
  }
}

function hideSkeleton(el) {
  if (el) el.innerHTML = "";
}

function showPanelError(elId, err) {
  const el = typeof elId === "string" ? $(elId) : elId;
  if (!el) return;
  const msg = (err && err.message ? err.message : String(err || "unknown error")).slice(0, 140);
  el.innerHTML =
    `<div class="panel-error" role="alert">âš  ${esc(msg)}` +
    ` <button class="btn tiny" onclick="loadSummary()">Retry</button></div>`;
}

/* ---------- Table sorting & pagination ---------- */
// tableState is in app.core.js (loaded first)

function makeSortable(tableEl, options = {}) {
  const { pageSize = 50, numericCols = [], dateCols = [] } = options;
  const key = tableEl.id || "table-" + Math.random().toString(36).slice(2);
  const state = { sortCol: null, sortDir: 1, page: 1, pageSize, rows: [] };
  tableState.set(key, state);

  const thead = tableEl.querySelector("thead");
  const tbody = tableEl.querySelector("tbody");
  if (!thead || !tbody) return;

  // Make headers clickable
  thead.querySelectorAll("th").forEach((th, idx) => {
    th.style.cursor = "pointer";
    th.style.userSelect = "none";
    th.addEventListener("click", () => {
      if (state.sortCol === idx) {
        state.sortDir *= -1;
      } else {
        state.sortCol = idx;
        state.sortDir = 1;
      }
      state.page = 1;
      renderTable(key);
    });
  });

  // Pagination controls
  const pagWrap = document.createElement("div");
  pagWrap.className = "table-pagination";
  pagWrap.style.cssText = "display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap;";
  pagWrap.innerHTML = `
    <button class="btn small pag-first" title="First" disabled>&laquo;&laquo;</button>
    <button class="btn small pag-prev" title="Previous" disabled>&laquo;</button>
    <span class="pag-info" style="font-size:12px;color:var(--dim)"></span>
    <button class="btn small pag-next" title="Next" disabled>&raquo;</button>
    <button class="btn small pag-last" title="Last" disabled>&raquo;&raquo;</button>
    <select class="pag-size" style="background:var(--panel2);border:1px solid var(--border);color:var(--text);padding:3px 6px;border-radius:3px;font-size:11px;">
      <option value="25">25</option>
      <option value="50" selected>50</option>
      <option value="100">100</option>
      <option value="200">200</option>
    </select>
  `;
  tableEl.parentElement.appendChild(pagWrap);

  pagWrap.querySelector(".pag-first").onclick = () => { state.page = 1; renderTable(key); };
  pagWrap.querySelector(".pag-prev").onclick = () => { state.page = Math.max(1, state.page - 1); renderTable(key); };
  pagWrap.querySelector(".pag-next").onclick = () => { state.page = Math.min(state.pages, state.page + 1); renderTable(key); };
  pagWrap.querySelector(".pag-last").onclick = () => { state.page = state.pages; renderTable(key); };
  pagWrap.querySelector(".pag-size").onchange = (e) => { state.pageSize = +e.target.value; state.page = 1; renderTable(key); };

  function renderTable(key) {
    const state = tableState.get(key);
    if (!state) return;
    const rows = [...state.rows];
    if (state.sortCol !== null) {
      rows.sort((a, b) => {
        let va = a.cells[state.sortCol]?.textContent || "";
        let vb = b.cells[state.sortCol]?.textContent || "";
        if (numericCols.includes(state.sortCol)) {
          va = parseFloat(va.replace(/[^0-9.-]/g, "")) || 0;
          vb = parseFloat(vb.replace(/[^0-9.-]/g, "")) || 0;
        } else if (dateCols.includes(state.sortCol)) {
          va = new Date(va).getTime() || 0;
          vb = new Date(vb).getTime() || 0;
        }
        return (va > vb ? 1 : va < vb ? -1 : 0) * state.sortDir;
      });
    }
    state.pages = Math.ceil(rows.length / state.pageSize) || 1;
    state.page = Math.min(state.page, state.pages);
    const start = (state.page - 1) * state.pageSize;
    const pageRows = rows.slice(start, start + state.pageSize);
    tbody.innerHTML = pageRows.map((r) => r.outerHTML).join("");
    // Update header sort indicators
    thead.querySelectorAll("th").forEach((th, i) => {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (i === state.sortCol) th.classList.add(state.sortDir === 1 ? "sorted-asc" : "sorted-desc");
    });
    // Update pagination
    pagWrap.querySelector(".pag-info").textContent = `Page ${state.page} / ${state.pages} (${rows.length} rows)`;
    pagWrap.querySelector(".pag-first").disabled = state.page === 1;
    pagWrap.querySelector(".pag-prev").disabled = state.page === 1;
    pagWrap.querySelector(".pag-next").disabled = state.page === state.pages;
    pagWrap.querySelector(".pag-last").disabled = state.page === state.pages;
    pagWrap.querySelector(".pag-size").value = state.pageSize;
  }

  return {
    setRows(rows) {
      state.rows = Array.from(rows);
      state.page = 1;
      renderTable(key);
    },
    getState() { return { ...state }; }
  };
}

/* ---------- WebSocket ---------- */
// ws is defined in app.core.js (loaded first)
// wsAliveAt is defined in app.core.js (loaded first)
// AUTH_TOKEN is in app.core.js (loaded first)

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  try {
    const url = AUTH_TOKEN
      ? `${proto}://${location.host}/ws?token=${encodeURIComponent(AUTH_TOKEN)}`
      : `${proto}://${location.host}/ws`;
    ws = new WebSocket(url);
  } catch (e) { return; }
  ws.onopen = () => {
    setStatus(true, "Live");
    startWsLatency();
    ws.send(JSON.stringify({ type: "subscribe", channel: "forex-breakout" }));
  };
  ws.onmessage = (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch (err) { return; }
    if (m.type === "summary") {
      wsAliveAt = Date.now();
      if (m.connected) {
        setStatus(true, "Live");
        applyAccount(m.account);
        renderPositions(m.positions);
      } else {
        setStatus(false, "MT5 disconnected");
      }
      return;
    }
    if (m.type === "pong") {
      wsLatency = Math.round(performance.now() - ws._pingStart);
      wsLastPong = Date.now();
      updateConnectionMenu();
      return;
    }
    if (m.type === "notify") {
      if (m.kind === "position_closed") {
        toast(`Position closed (ticket ${m.tickets.join(", ")})`, "info");
        notify("Position closed", `Ticket ${m.tickets.join(", ")}`);
      } else if (m.kind === "plan_changed") {
        const txt = `${m.symbol}: new signal ${m.action}`;
        toast(txt, m.action === "BUY" ? "up" : m.action === "SELL" ? "down" : "info");
        notify("Trade plan changed", txt);
      }
    }
  };
  ws.onclose = () => {
    ws = null;
    setTimeout(connectWs, 5000);
  };
}

function initConnectionDropdown() {
  const btn = $("status-btn");
  const menu = $("status-menu");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = menu.classList.toggle("hidden");
    btn.setAttribute("aria-expanded", !open);
  });
  document.addEventListener("click", () => {
    if (!menu.classList.contains("hidden")) {
      menu.classList.add("hidden");
      btn.setAttribute("aria-expanded", "false");
    }
  });
  $("status-reconnect").addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Reconnecting...";
    try {
      await apiPost("/api/health/reconnect");
      toast("Reconnect requested", "info");
    } catch (e) {
      toast(e.message || "Reconnect failed", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Reconnect MT5";
    }
  });
  $("status-ws-reconnect").addEventListener("click", () => {
    if (ws) ws.close();
    connectWs();
    toast("WebSocket reconnecting", "info");
  });
}

/* ---------- Theme toggle ---------- */
function initTheme() {
  const btn = $("theme-toggle");
  const root = document.documentElement;
  const themes = ["dark", "light", "auto"];
  let current = localStorage.getItem("theme") || "dark";
  if (current === "auto") current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  applyTheme(current);

  btn.addEventListener("click", () => {
    const idx = themes.indexOf(current);
    current = themes[(idx + 1) % themes.length];
    localStorage.setItem("theme", current);
    applyTheme(current);
  });

  if (current === "auto") {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
      if (localStorage.getItem("theme") === "auto") applyTheme(e.matches ? "dark" : "light");
    });
  }

  function applyTheme(theme) {
    current = theme;
    root.dataset.theme = theme;
    updateThemeIcon(theme);
    // Force chart re-render for theme change
    if (priceChart) { priceChart.applyOptions({ layout: { background: { type: "solid", color: getComputedStyle(root).getPropertyValue("--bg").trim() } } }); }
    if (rsiChart) rsiChart.applyOptions({ layout: { background: { type: "solid", color: getComputedStyle(root).getPropertyValue("--bg").trim() } } });
    if (macdChart) macdChart.applyOptions({ layout: { background: { type: "solid", color: getComputedStyle(root).getPropertyValue("--bg").trim() } } });
    // Re-render equity/distribution charts with new theme colors
    if (equityTab === "equity" && state.journalEquity) renderEquityChart();
    else if (equityTab === "dist" && state.journalDistribution) renderDistributionChart();
  }

  function updateThemeIcon(theme) {
    btn.querySelectorAll("svg").forEach((svg) => svg.style.display = "none");
    const icon = btn.querySelector(`.theme-icon-${theme === "dark" ? "moon" : theme === "light" ? "sun" : "auto"}`);
    if (icon) icon.style.display = "block";
  }
}

// wsLatency is defined in app.core.js (loaded first)
// wsLastPong is defined in app.core.js (loaded first)
function startWsLatency() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const start = performance.now();
  ws._pingStart = start;
  ws.send(JSON.stringify({ type: "ping" }));
  setTimeout(startWsLatency, 10000);
}

function updateConnectionMenu() {
  const term = $("status-term");
  const build = $("status-build");
  const lat = $("status-latency");
  const hb = $("status-heartbeat");
  if (term.dataset.value) term.textContent = term.dataset.value;
  if (build.dataset.value) build.textContent = build.dataset.value;
  lat.textContent = wsLatency ? `${wsLatency} ms` : "--";
  hb.textContent = wsLastPong ? fmtWhen(new Date(wsLastPong).toISOString()) : "--";
}

function applyAccount(acc) {
  $("h-login").textContent = acc.login;
  $("h-server-time").textContent = acc.server_time;
  $("h-pos-count").textContent = acc.daily_trades + " trades today";
  $("c-balance").textContent = acc.balance.toFixed(2);
  $("c-equity").textContent = acc.equity.toFixed(2);
  $("c-margin").textContent = acc.free_margin.toFixed(2);
  $("c-margin-level").textContent =
    acc.margin_level > 0 ? acc.margin_level.toFixed(0) + "%" : "--";
  $("c-daily-sub").textContent = `${acc.currency} | realized ${acc.daily_realized.toFixed(2)}`;
  setMoney($("c-daily"), acc.daily_pnl);
  setMoney($("c-floating"), acc.floating_pnl);
  $("status-term").dataset.value = acc.terminal?.company || "--";
  $("status-build").dataset.value = acc.terminal?.build ? "b" + acc.terminal.build : "--";
  const sel = $("h-account-select");
  if (sel && sel.value !== String(acc.login)) {
    sel.dataset.prev = sel.value;
    sel.value = String(acc.login);
    sel.style.color = "";
  }
  updateConnectionMenu();
}

/* ---------- Account switcher ---------- */
async function loadAccounts() {
  const sel = $("h-account-select");
  if (!sel) return;
  try {
    const list = await api("/api/accounts");
    const cur = String((await api("/api/account")).login);
    sel.innerHTML = "";
    for (const a of list) {
      const opt = document.createElement("option");
      opt.value = String(a.login);
      opt.textContent = `${a.login} Â· ${a.name || a.server || ""}`;
      sel.appendChild(opt);
    }
    sel.value = list.some((a) => String(a.login) === cur) ? cur : "";
    sel.style.color = "var(--accent, #4f8cff)";
  } catch (e) {
    sel.innerHTML = "";
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "--";
    sel.appendChild(opt);
  }
  sel.addEventListener("change", async () => {
    const login = sel.value;
    if (!login) return;
    const prev = sel.dataset.prev || "";
    try {
      sel.disabled = true;
      await apiPost("/api/account/switch", { login: Number(login) });
      const deadline = Date.now() + 120000;
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        let st;
        try {
          st = await api("/api/account/switch-status");
        } catch (e) { /* keep polling */ }
        if (!st || !st.running) {
          if (st && st.error) throw new Error(st.error);
          break;
        }
        if (Date.now() > deadline) throw new Error("switch timed out");
      }
      await loadSummary();
      await loadChart();
    } catch (e) {
      sel.value = prev;
      errorReport.capture(e.message || String(e), "accountSwitch", 0, 0, e);
    } finally {
      sel.disabled = false;
    }
  });
}

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
    const [acc, pos, hist] = await Promise.all([
      api("/api/account"),
      api("/api/positions"),
      api("/api/history?days=30"),
    ]);
    setStatus(true, "Connected");
    applyAccount(acc);
    renderPositions(pos.positions);
    renderStats(hist.stats);
    renderTrades(hist.recent);
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadSummary", 0, 0, e);
    const msg = e && e.message ? e.message : "";
    if (/not connected|terminal/i.test(msg)) setStatus(false, "MT5 disconnected");
    else if (/no rates|not found|symbol/i.test(msg)) setStatus(false, "Data unavailable: " + msg);
    else setStatus(false, "Offline");
  }
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
    renderPositionsList();
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

  const ptEl = document.getElementById("positions-table");
  if (ptEl) {
    if (!window._positionsSortable) {
      window._positionsSortable = makeSortable(ptEl, {
        pageSize: 25,
        numericCols: [2, 3, 4, 5, 6, 7, 8],
      });
    }
    if (window._positionsSortable) {
      const pb = document.getElementById("positions-body");
      window._positionsSortable.setRows(pb ? pb.querySelectorAll("tr[data-ticket]") : []);
    }
  }

  // Mirror into the visible positions-list panel (this layout has no positions-table/body).
  renderPositionsList();
}

function renderPositionsList() {
  const plist = document.getElementById("positions-list");
  if (!plist) return;
  const badge = document.getElementById("pos-count");
  if (badge) badge.textContent = state.positions.length;
  if (!state.positions.length) {
    plist.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3)">No open positions</div>';
  } else {
    plist.innerHTML = state.positions.map((p) => `
      <div class="position-item" data-ticket="${p.ticket}">
        <span class="position-symbol">${esc(p.symbol)}</span>
        <div class="position-details">
          <span>${esc(p.type)} ${p.volume}</span>
          <span>@ ${fmtPrice(p.price_open)} &rarr; ${fmtPrice(p.price_current)}</span>
          <span>SL ${p.sl ? fmtPrice(p.sl) : "--"} TP ${p.tp ? fmtPrice(p.tp) : "--"}</span>
        </div>
        <span class="position-pnl ${p.total >= 0 ? "profit" : "loss"}">${p.total >= 0 ? "+" : ""}${fmtMoney(p.total)}</span>
        <button class="position-close" data-close="${p.ticket}" title="Close position">✕</button>
      </div>`).join("");
  }
  if (!window._positionsCloseWired) {
    window._positionsCloseWired = true;
    plist.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-close]");
      if (!btn) return;
      const ticket = btn.getAttribute("data-close");
      if (!confirm(`Close position ${ticket}?`)) return;
      try {
        await apiPost(`/api/positions/${ticket}/close`);
        toast(`Position ${ticket} closed`, "up");
        loadPositionsOnly();
      } catch (err) {
        toast(`Close failed: ${err.message}`, "down");
      }
    });
  }
}

// positionsBody click handler is in app.panels.js

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
  if (window._tradesSortable) {
    window._tradesSortable.setRows($("trades-body").querySelectorAll("tr:not(.empty-row)"));
  }
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

// planBody click handler is in app.panels.js

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
  try {
    const a = await api("/api/alerts");
    if (a.error) throw new Error(a.error);
    state.alerts = a.alerts || [];
    renderAlerts();
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadAlerts", 0, 0, e);
    const el = $("alerts-list");
    if (el) el.innerHTML = `<div class="alert-item"><div class="alert-content"><div class="alert-title">Alerts unavailable: ${esc(e.message)}</div></div></div>`;
    const full = $("alerts-full-list");
    if (full) full.innerHTML = "";
  }
}

function _alertRow(a) {
  const digits = a.symbol.includes("JPY") ? 3 : 5;
  const dist = (a.current_price != null) ? Math.abs(a.price - a.current_price).toFixed(digits) : "--";
  const nowPx = (a.current_price != null) ? a.current_price.toFixed(digits) : "--";
  return `<div class="alert-item">
    <div class="alert-icon ${a.active ? "signal" : "warning"}">${a.active ? "📡" : "⏸"}</div>
    <div class="alert-content">
      <div class="alert-title">${esc(a.symbol)} ${esc(a.condition)} ${a.price.toFixed(digits)}${a.name ? " — " + esc(a.name) : ""}</div>
      <div class="alert-time">${a.active ? `now ${nowPx} · ${dist} away` : "disabled"}</div>
    </div>
    <div style="margin-left:auto;display:flex;gap:4px">
      <button class="btn tiny" onclick="toggleAlert('${esc(a.id)}')">${a.active ? "Off" : "On"}</button>
      <button class="btn tiny danger" onclick="deleteAlert('${esc(a.id)}')">×</button>
    </div>
  </div>`;
}

function renderAlerts() {
  const alerts = state.alerts || [];
  const list = $("alerts-list");
  if (list) {
    list.innerHTML = alerts.length
      ? alerts.map(_alertRow).join("")
      : '<div class="alert-item"><div class="alert-content"><div class="alert-title">No alerts configured</div><div class="alert-time">create one on the Alerts tab</div></div></div>';
  }
  const full = $("alerts-full-list");
  if (full) {
    full.innerHTML = alerts.length
      ? `<div class="alert-list">${alerts.map(_alertRow).join("")}</div>`
      : '<div style="padding:20px;text-align:center;color:var(--text-3)">No alerts configured.</div>';
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
  const symbol = ($("#alert-symbol") || {}).value;
  if (!symbol) { toast("Alert form not available", "down"); return; }
  const price = parseFloat($("#alert-price").value);
  const condition = $("#alert-condition").value;
  const name = $("#alert-name").value.trim();

  if (!symbol.trim() || isNaN(price)) {
    toast("Symbol and price required", "down");
    return;
  }

  try {
    const result = await api("/api/alerts", { method: "POST", body: { symbol: symbol.trim().toUpperCase(), price, condition, name } });
    if (result && result.error) throw new Error(result.error);
    toast("Alert added", "up");
    $("#alert-price").value = "";
    $("#alert-name").value = "";
    loadAlerts();
  } catch (e) {
    toast("Failed to add alert: " + e.message, "down");
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
function _newsImpactClass(imp) {
  return imp === "high" ? "high" : imp === "medium" ? "medium" : "low";
}

function _fmtNewsWhen(timeStr) {
  // API times are naive UTC strings "YYYY-MM-DD HH:MM"
  const d = new Date(String(timeStr || "").replace(" ", "T") + "Z");
  if (isNaN(d)) return { day: "", hhmm: String(timeStr || "") };
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((day - today) / 86400000);
  const dayLabel = diffDays === 0 ? "Today" : diffDays === 1 ? "Tomorrow"
    : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const hhmm = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return { day: dayLabel, hhmm };
}

function renderNewsPanels(n) {
  const provider = $("news-provider");
  if (provider) provider.textContent = n.provider || "";
  const events = n.events || [];
  const feed = $("news-feed");
  if (feed) {
    if (!events.length) {
      feed.innerHTML = '<div class="news-item"><div class="news-title" style="color:var(--text-3)">No upcoming events in the next 48h (weekly calendar)</div></div>';
    } else {
      feed.innerHTML = events.slice(0, 14).map((ev) => {
        const when = _fmtNewsWhen(ev.time);
        return `<div class="news-item">` +
          `<div class="news-time">${esc(when.day)} ${esc(when.hhmm)} · ${esc(ev.currency)}</div>` +
          `<div class="news-title">${esc(ev.title)}</div>` +
          `<div class="news-source">ForexFactory <span class="news-impact ${_newsImpactClass(ev.impact)}">${esc(String(ev.impact).toUpperCase())}</span></div>` +
          `</div>`;
      }).join("");
    }
  }
  const macro = $("macro-calendar");
  if (macro) {
    const majors = events.filter((ev) => ev.impact === "high" || ev.impact === "medium").slice(0, 8);
    if (!majors.length) {
      macro.innerHTML = '<div class="macro-item"><span class="macro-event" style="color:var(--text-3)">No high/medium impact events in the next 48h</span></div>';
    } else {
      macro.innerHTML = majors.map((ev) => {
        const when = _fmtNewsWhen(ev.time);
        return `<div class="macro-item">` +
          `<span class="macro-time">${esc(when.day)} ${esc(when.hhmm)}</span>` +
          `<span class="macro-event">${esc(ev.currency)} ${esc(ev.title)}</span>` +
          `<span class="macro-impact ${_newsImpactClass(ev.impact)}"></span>` +
          `</div>`;
      }).join("");
    }
  }
  const full = $("news-full-feed");
  if (full) {
    if (!events.length) {
      full.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3)">No upcoming events in the next 48h (weekly calendar)</div>';
    } else {
      full.innerHTML = events.map((ev) => {
        const when = _fmtNewsWhen(ev.time);
        return `<div class="news-item">` +
          `<div class="news-time">${esc(when.day)} ${esc(when.hhmm)} · ${esc(ev.currency)}</div>` +
          `<div class="news-title">${esc(ev.title)}</div>` +
          `<div class="news-source">ForexFactory <span class="news-impact ${_newsImpactClass(ev.impact)}">${esc(String(ev.impact).toUpperCase())}</span>` +
          `${ev.forecast ? ` · forecast: ${esc(ev.forecast)}` : ""}${ev.previous ? ` · previous: ${esc(ev.previous)}` : ""}</div>` +
          `</div>`;
      }).join("");
    }
  }
}

async function loadNews() {
  try {
    const n = await api("/api/news?horizon_hours=48");
    state.news = n;
    renderNewsPanels(n);
    const banner = $("news-banner");
    if (!banner) return;
    if (n.trading_blocked && n.imminent_events && n.imminent_events.length) {
      banner.style.display = "";
      $("news-banner-text").textContent = n.imminent_events
        .map((e) => `${e.currency} ${e.title}`).join(" Â· ");
      if (!lastImminent) {
        notify("High-impact news imminent", n.imminent_events.map((e) => `${e.currency} ${e.title}`).join(", "));
        toast("High-impact news within the hour â€” be careful", "down");
      }
      lastImminent = true;
    } else {
      banner.style.display = "none";
      lastImminent = false;
    }
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadNews", 0, 0, e);
    const feed = $("news-feed");
    if (feed) feed.innerHTML = `<div class="news-item"><div class="news-title" style="color:var(--text-3)">News unavailable: ${esc(e.message)}</div></div>`;
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
  const canvas = document.getElementById("equity-chart");
  if (!canvas || typeof canvas.getContext !== "function") return;
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
// equityTab is defined in app.panels.js (loaded first)
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

/* ---------- Sessions ---------- */

function filteredJournal() {
  const f = state.journalFilters;
  return state.journal.filter((t) =>
    (!f.symbol || (t.symbol || "") === f.symbol) &&
    (!f.setup || (t.setup || "manual") === f.setup) &&
    (!f.outcome || (t.outcome || "") === f.outcome)
  );
}

function renderJournal() {
  const body = $("journal-body");
  const rows = filteredJournal();
  body.innerHTML = "";
  if (!rows.length) {
    body.innerHTML = '<tr class="empty-row"><td colspan="10">📋 Journal is empty — closed trades appear here automatically</td></tr>';
    return;
  }
  for (const t of rows) {
    const tr = document.createElement("tr");
    const side = String(t.side || "").toUpperCase();
    const outcome = t.outcome || "--";
    const notes = [t.reason, t.mistakes, t.lessons].filter(Boolean).join(" Â· ");
    tr.innerHTML = `
      <td>${esc(fmtWhen(t.close_time))}</td>
      <td>${esc(t.symbol || "--")}</td>
      <td style="color:${side === "BUY" ? C.up : side === "SELL" ? C.down : C.dim}">${esc(side)}</td>
      <td>${esc(t.setup || "manual")}</td>
      <td>${fmtPrice(t.entry)}</td>
      <td>${fmtPrice(t.exit_price)}</td>
      <td>${t.lots ?? "--"}</td>
      <td class="${(t.pnl || 0) >= 0 ? "pos" : "neg"}">${fmtMoney(t.pnl)}</td>
      <td style="color:${outcome === "win" ? C.up : outcome === "loss" ? C.down : C.dim}">${esc(outcome)}</td>
      <td class="notes-cell">
        <span style="max-width:300px;overflow:hidden;text-overflow:ellipsis;display:inline-block;vertical-align:middle">${esc(notes)}</span>
        <button class="btn tiny" data-ticket="${t.ticket}" title="Edit notes">âœŽ</button>
      </td>`;
    body.appendChild(tr);
  }
  body.querySelectorAll("button[data-ticket]").forEach((btn) => {
    btn.addEventListener("click", () => openJournalEditor(parseInt(btn.dataset.ticket, 10)));
  });

  if (!window._journalSortable) {
    window._journalSortable = makeSortable($("journal-table"), {
      pageSize: 50,
      numericCols: [6, 8],
      dateCols: [0],
    });
  }
  if (window._journalSortable) {
    window._journalSortable.setRows($("journal-body").querySelectorAll("tr:not(.empty-row)"));
  }
}

function openJournalEditor(ticket) {
  const t = state.journal.find((x) => x.ticket === ticket);
  if (!t) return;
  const ed = $("journal-editor");
  ed.classList.remove("hidden");
  $("je-ticket").textContent = `#${ticket} ${t.symbol || ""} ${t.side || ""}`;
  $("je-reason").value = t.reason || "";
  $("je-mistakes").value = t.mistakes || "";
  $("je-lessons").value = t.lessons || "";
  ed.dataset.ticket = ticket;
}

function closeJournalEditor() {
  const je = $("journal-editor");
  if (!je) return;
  je.classList.add("hidden");
}

// journal editor handlers are in app.panels.js

function exportJournalCsv() {
  const rows = filteredJournal();
  if (!rows.length) { toast("Nothing to export", "info"); return; }
  const head = ["ticket", "symbol", "side", "setup", "entry", "exit", "lots", "pnl", "outcome", "open_time", "close_time", "reason", "mistakes", "lessons"];
  const csv = [head.join(",")].concat(rows.map((t) => head.map((k) => {
    const v = t[k];
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(","))).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `journal-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function initJournalTools() {
  const jfSymbol = $("jf-symbol");
  if (jfSymbol) jfSymbol.addEventListener("change", (e) => { state.journalFilters.symbol = e.target.value; renderJournal(); });
  const jfSetup = $("jf-setup");
  if (jfSetup) jfSetup.addEventListener("change", (e) => { state.journalFilters.setup = e.target.value; renderJournal(); });
  const jfOutcome = $("jf-outcome");
  if (jfOutcome) jfOutcome.addEventListener("change", (e) => { state.journalFilters.outcome = e.target.value; renderJournal(); });
  const jfCsv = $("jf-csv");
  if (jfCsv) jfCsv.addEventListener("click", exportJournalCsv);
  const jfSync = $("jf-sync");
  if (jfSync) jfSync.addEventListener("click", async () => {
    try {
      const r = await apiPost("/api/journal?limit=1&sync=true", undefined);
      toast(`Sync done (${r.entries?.length || 0} rows loaded)`, "up");
      loadJournal();
    } catch (err) {
      toast(`Sync failed: ${err.message}`, "down");
    }
  });
}

/* ---------- Watchlist ---------- */
function renderWatchlist() {
  const box = $("watchlist");
  box.innerHTML = "";
  const touch = window.matchMedia("(pointer: coarse)").matches;
  for (const s of state.watchlist) {
    const chip = document.createElement("span");
    chip.className = "wl-chip" + (s === state.symbol ? " active" : "");
    chip.innerHTML = `<span class="wl-sym">${esc(s)}</span><span class="wl-px" data-px="${esc(s)}">--</span>`;
    chip.draggable = !touch; // HTML5 DnD is desktop-only (item #10)
    chip.dataset.symbol = s;
    chip.title = touch
      ? "tap: load · x: remove"
      : "click: load · shift+click: remove · drag: reorder";
    chip.onclick = (e) => {
      if (e.shiftKey || e.target.classList.contains("wl-remove")) {
        state.watchlist = state.watchlist.filter((x) => x !== s);
        savePrefs();
        renderWatchlist();
        return;
      }
      state.symbol = s;
      $("symbol-input").value = s;
      loadChart();
    };
    if (touch && state.watchlist.length > 1) {
      // Touch fallback: explicit remove button (shift-click unavailable).
      const rm = document.createElement("button");
      rm.className = "wl-remove";
      rm.setAttribute("aria-label", "Remove " + s + " from watchlist");
      rm.textContent = "×";
      chip.appendChild(rm);
    }
    if (!touch) {
    chip.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", s);
      chip.classList.add("dragging");
    });
    chip.addEventListener("dragend", () => chip.classList.remove("dragging"));
    chip.addEventListener("dragover", (e) => { e.preventDefault(); chip.classList.add("drop-hint"); });
    chip.addEventListener("dragleave", () => chip.classList.remove("drop-hint"));
    chip.addEventListener("drop", (e) => {
      e.preventDefault();
      chip.classList.remove("drop-hint");
      const from = e.dataTransfer.getData("text/plain");
      const to = s;
      if (!from || from === to) return;
      const list = state.watchlist.filter((x) => x !== from);
      list.splice(list.indexOf(to), 0, from);
      state.watchlist = list;
      savePrefs();
      renderWatchlist();
    });
    }
    box.appendChild(chip);
  }
}

function addToWatchlist(sym) {
  sym = (sym || "").trim().toUpperCase();
  if (!sym || state.watchlist.includes(sym)) return;
  state.watchlist.push(sym);
  if (state.watchlist.length > 12) state.watchlist.shift();
  savePrefs();
  renderWatchlist();
  pollWatchQuotes();
}

/* Live top-of-book prices for the watchlist chips (/api/quote). */
async function pollWatchQuotes() {
  for (const s of state.watchlist) {
    try {
      const q = await api(`/api/quote?symbol=${encodeURIComponent(s)}`);
      const px = document.querySelector(`[data-px="${CSS.escape(s)}"]`);
      if (!px) continue;
      if (!q || !q.bid) { px.textContent = "--"; continue; }
      const bid = q.bid;
      const prev = _wlLastPx[s];
      px.textContent = fmtPrice(bid);
      if (prev != null && bid !== prev) {
        px.classList.remove("flash-up", "flash-down");
        void px.offsetWidth; // restart animation
        px.classList.add(bid > prev ? "flash-up" : "flash-down");
      }
      _wlLastPx[s] = bid;
    } catch (e) {
      const px = document.querySelector(`[data-px="${CSS.escape(s)}"]`);
      if (px) px.textContent = "--";
    }
  }
}

/* ============================================================
   Chart functions live in app.chart.js (loaded first).
   Do NOT duplicate them here — the fixed versions with proper
   null guards, isFin(), botChartIndex(), and sequence guards
   must take precedence.
   ============================================================ */


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
  renderWatchlist();
  pollWatchQuotes();
  loadAlerts();

  const tick = (fn) => () => { if (!document.hidden) fn(); };

  // Fast tier (5s): lightweight real-time data
  setInterval(tick(pollEvents), POLL_MS);
  setInterval(tick(loadPeer), POLL_MS);
  setInterval(tick(pollWatchQuotes), POLL_MS);
  setInterval(tick(loadAlerts), 30000);

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

  // News tier (2min)
  setInterval(tick(loadNews), NEWS_MS);

  // WS fallback: only poll summary if WS is dead
  setInterval(tick(loadSummary), POLL_MS);
}

init();