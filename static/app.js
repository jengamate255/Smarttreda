"use strict";

/* ---------- Error Reporting ---------- */
const errorReport = {
  queue: [],
  maxQueue: 50,
  endpoint: "/api/client-error",
  flushTimer: null,

  capture(message, source, lineno, colno, error) {
    const entry = {
      timestamp: new Date().toISOString(),
      message: String(message),
      source: source || "",
      lineno: lineno || 0,
      colno: colno || 0,
      stack: error?.stack || "",
      url: location.href,
      userAgent: navigator.userAgent,
      sessionId: sessionStorage.getItem("sessionId") || this.genSessionId(),
    };
    this.queue.push(entry);
    if (this.queue.length > this.maxQueue) this.queue.shift();
    this.scheduleFlush();
    return false; // don't prevent default
  },

  genSessionId() {
    const id = crypto.randomUUID();
    sessionStorage.setItem("sessionId", id);
    return id;
  },

  scheduleFlush() {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => this.flush(), 5000);
  },

  async flush() {
    this.flushTimer = null;
    if (!this.queue.length) return;
    const payload = { errors: this.queue.splice(0, this.queue.length) };
    try {
      await fetch(this.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true,
      });
    } catch (e) {
      // silent fail - don't loop
    }
  },

  log(level, ...args) {
    const entry = { timestamp: new Date().toISOString(), level, args: args.map(a => this.serialize(a)) };
    console[level](...args);
    this.queue.push({ ...entry, type: "log" });
    if (this.queue.length > this.maxQueue) this.queue.shift();
  },

  serialize(val) {
    if (val instanceof Error) return { name: val.name, message: val.message, stack: val.stack };
    if (typeof val === "object") {
      try { return JSON.parse(JSON.stringify(val)); } catch { return String(val); }
    }
    return val;
  },
};

// Inject CSS to prevent TradingView logo layout shifts BEFORE charts render
const tvStyle = document.createElement('style');
tvStyle.textContent = `
  .lc-pane a[href*="tradingview"],
  .lc-pane a[href*="tradingview"] *,
  .lc-pane .tv-attr-logo,
  .lc-pane [data-tv-attr-logo],
  .lc-pane a[href*="tradingview.com"] {
    position: absolute !important;
    bottom: 4px !important;
    right: 4px !important;
    opacity: 0.5 !important;
    z-index: 100 !important;
    pointer-events: none !important;
    contain: layout !important;
  }
`;
document.head.appendChild(tvStyle);

window.addEventListener("error", (e) => errorReport.capture(e.message, e.filename, e.lineno, e.colno, e.error));
window.addEventListener("unhandledrejection", (e) => errorReport.capture(e.reason?.message || String(e.reason), "", 0, 0, e.reason));

// Patch console for structured logging
["log", "warn", "error"].forEach((level) => {
  const orig = console[level];
  console[level] = (...args) => {
    errorReport.log(level, ...args);
    orig.apply(console, args);
  };
});

/* ---------- Theme ---------- */
const C = {
  bg: "#131722",
  panel2: "#181c25",
  border: "#2a2e39",
  text: "#d1d4dc",
  dim: "#787b86",
  up: "#26a69a",
  down: "#ef5350",
  ema50: "#ffd479",
  ema200: "#2962ff",
  rsi: "#9b59b6",
  macd: "#2962ff",
  signal: "#f39c12",
  accent: "#2962ff",
  purple: "#9b59b6",
  yellow: "#ffd479",
  orange: "#f39c12",
};

/* ---------- State ---------- */
const state = {
  symbol: "EURUSD",
  timeframe: "H1",
  count: 300,
  indicators: { ema: true, rsi: true, macd: true },
  bot: { on: true, data: null },
  data: null,
  positions: [],
  plan: null,
  watchlist: [],
  equity: null,
  equityOpts: { balance: true, floating: false, ma5: false, dd: false },
  journal: [],
  journalFilters: { symbol: "", setup: "", outcome: "" },
  lastBar: null,
  quoteTimer: null,
  btGenerated: null,
};

const TFs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"];
const DEFAULT_WATCH = ["EURUSD", "XAUUSD", "GBPUSD", "USDJPY", "USDCAD"];
const POLL_MS = 5000;
const CHART_MS = 30000;
const SLOW_MS = 60000;
const NEWS_MS = 300000;
const QUOTE_MS = 2500;
const PREFS_KEY = "mt5dash.prefs";

const $ = (id) => document.getElementById(id);

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
async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.detail) detail = j.detail;
    } catch (e) { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

/* ---------- Toasts & notifications ---------- */
const toastQueue = [];
const TOAST_MAX = 5;
let toastPermissionAsked = false;

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
const SHORTCUTS = [
  { key: "1", desc: "Focus symbol input", action: () => $("symbol-input").focus() },
  { key: "2", desc: "Toggle chart panel", action: () => $("chart-wrap").classList.toggle("hidden") },
  { key: "3", desc: "Focus watchlist", action: () => $("wl-add").focus() },
  { key: "4", desc: "Open journal", action: () => { document.querySelector(".journal-panel")?.scrollIntoView({ behavior: "smooth" }); } },
  { key: "5", desc: "Open backtesting", action: () => { document.querySelector(".backtest-panel")?.scrollIntoView({ behavior: "smooth" }); } },
  { key: "r", desc: "Refresh all data", action: () => { loadSummary(); loadEquity(); loadJournal(); loadRisk(); loadSessions(); loadNews(); loadBacktests(); toast("Refreshing...", "info"); } },
  { key: "h", desc: "Show help", action: showHelp },
  { key: "Escape", desc: "Close modals", action: closeModals },
  { key: "?", desc: "Show help", action: showHelp, shift: true },
];

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

function hideSkeleton(el) {
  if (el) el.innerHTML = "";
}

/* ---------- Table sorting & pagination ---------- */
const tableState = new Map();

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
let ws = null;
let wsAliveAt = 0;

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  try {
    ws = new WebSocket(`${proto}://${location.host}/ws`);
  } catch (e) { return; }
  ws.onopen = () => {
    setStatus(true, "Live");
    startWsLatency();
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
  }

  function updateThemeIcon(theme) {
    btn.querySelectorAll("svg").forEach((svg) => svg.style.display = "none");
    const icon = btn.querySelector(`.theme-icon-${theme === "dark" ? "moon" : theme === "light" ? "sun" : "auto"}`);
    if (icon) icon.style.display = "block";
  }
}

let wsLatency = 0;
let wsLastPong = 0;
function startWsLatency() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const start = performance.now();
  ws.send(JSON.stringify({ type: "ping" }));
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "pong") {
        wsLatency = Math.round(performance.now() - start);
        wsLastPong = Date.now();
        updateConnectionMenu();
      }
    } catch {}
  };
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
  updateConnectionMenu();
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

function renderPositions(positions) {
  state.positions = positions || [];
  $("pos-badge").textContent = state.positions.length;
  const body = $("positions-body");
  body.innerHTML = "";

  if (!state.positions.length) {
    body.innerHTML = '<tr class="empty-row"><td colspan="9">No open positions</td></tr>';
    return;
  }

  let totalPnl = 0;
  for (const p of state.positions) {
    totalPnl += p.total;
    const tr = document.createElement("tr");
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
        <button class="btn tiny" data-act="sltp" data-ticket="${p.ticket}" title="Apply SL/TP">SL/TP</button>
        <button class="btn tiny danger" data-act="close" data-ticket="${p.ticket}" title="Close position">✕</button>
      </td>`;
    body.appendChild(tr);
  }

  const tfoot = document.createElement("tr");
  tfoot.innerHTML = `
    <td colspan="8" style="text-align:right;color:${C.dim};font-weight:600">Total</td>
    <td class="${totalPnl >= 0 ? "pos" : "neg"}" style="font-weight:600">${fmtMoney(totalPnl)}</td>`;
  body.appendChild(tfoot);

  if (!window._positionsSortable) {
    window._positionsSortable = makeSortable($("positions-table"), {
      pageSize: 25,
      numericCols: [2, 3, 4, 5, 6, 7, 8],
    });
  }
  window._positionsSortable.setRows($("positions-body").querySelectorAll("tr:not(.empty-row):not(tfoot tr)"));
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
    body.innerHTML = '<tr class="empty-row"><td colspan="8">No closed trades in period</td></tr>';
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
  tf.textContent = `${plan.symbol} ${plan.timeframe} · score ${plan.trade_score ?? "--"}`;

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
      ? `<div class="plan-warn">⚠ ${esc(plan.warnings.join(" · "))}</div>` : "") +
    (plan.errors && plan.errors.length
      ? `<div class="plan-err">${esc(plan.errors.join(" · "))}</div>` : "") +
    `<div class="plan-zone">${zoneLine(plan)}</div>` +
    `<button id="plan-execute" class="btn full ${buy ? "go" : "sell"}" ${plan.valid ? "" : "disabled"}>` +
    `Execute ${plan.action} · ${plan.lots ?? "--"} lots</button>`;
}

function zoneLine(plan) {
  if (!plan || !plan.zone_type || !plan.zone_upper) return "";
  const supply = plan.zone_type === "supply";
  return `<div class="plan-zone-txt" style="color:${supply ? C.down : C.up}">` +
    `Zone: ${plan.zone_type} ${plan.zone_strength ?? "--"}/100 · ` +
    `${fmtPrice(plan.zone_lower)} – ${fmtPrice(plan.zone_upper)}</div>`;
}

$("plan-body").addEventListener("click", async (e) => {
  if (e.target.id !== "plan-execute") return;
  const plan = state.plan;
  if (!plan || plan.action === "NONE") return;
  if (!confirm(`Execute ${plan.action} ${plan.symbol} ${plan.lots} lots?\nEntry ${fmtPrice(plan.entry)} · SL ${fmtPrice(plan.sl)} · TP ${fmtPrice(plan.tp)}`)) return;
  try {
    const r = await apiPost("/api/execute-plan", {
      symbol: plan.symbol,
      timeframe: plan.timeframe || state.timeframe,
    });
    toast(r.accepted ? `Order sent — ticket #${r.ticket}` : `Rejected: ${r.error || r.retcode}`, r.accepted ? "up" : "down");
  } catch (err) {
    toast(`Execute failed: ${err.message}`, "down");
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
    $("h-risk-badge").textContent = r.warnings.length ? "⚠ RISK" : "";

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
      `<div class="risk-extra">Open positions ${r.open_positions}/${r.max_positions ?? "∞"} · Exposure ${r.total_exposure?.toLocaleString?.() ?? r.total_exposure}</div>` +
      (r.warnings.length ? `<div class="plan-warn">${esc(r.warnings.join(" · "))}</div>` : "");
  } catch (e) {
    errorReport.capture(e.message || String(e), "loadRisk", 0, 0, e);
    $("risk-body").innerHTML = `<div class="plan-empty">Risk unavailable: ${esc(e.message)}</div>`;
  }
}

/* ---------- Journal insights ---------- */
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
        `<div class="insight-row"><span>${esc(x.setup)}</span><span class="${x.net_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(x.net_pnl)} · ${x.win_rate.toFixed(0)}%</span></div>`) +
      col("Best hours (UTC)", sessions, (x) =>
        `<div class="insight-row"><span>${x.hour}:00</span><span class="${x.net_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(x.net_pnl)} · ${x.win_rate.toFixed(0)}%</span></div>`) +
      col("Best pairs", pairs, (x) =>
        `<div class="insight-row"><span>${esc(x.symbol)}</span><span class="${x.net_pnl >= 0 ? "pos" : "neg"}">${fmtMoney(x.net_pnl)} · ${x.win_rate.toFixed(0)}%</span></div>`);
  } catch (e) {
    $("sessions-body").innerHTML = `<div class="plan-empty">Insights unavailable: ${esc(e.message)}</div>`;
  }
}

/* ---------- News ---------- */
let lastImminent = false;
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
        .map((e) => `${e.currency} ${e.title}`).join(" · ");
      if (!lastImminent) {
        notify("High-impact news imminent", n.imminent_events.map((e) => `${e.currency} ${e.title}`).join(", "));
        toast("High-impact news within the hour — be careful", "down");
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

function populateJournalFilters() {
  const syms = [...new Set(state.journal.map((t) => t.symbol || "?"))].sort();
  const setups = [...new Set(state.journal.map((t) => t.setup || "manual"))].sort();
  const sel = (id, items, key) => {
    const el = $(id);
    const cur = state.journalFilters[key];
    el.innerHTML = `<option value="">All ${key === "outcome" ? "outcomes" : key === "setup" ? "setups" : "symbols"}</option>` +
      items.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    el.value = cur || "";
  };
  sel("jf-symbol", syms, "symbol");
  sel("jf-setup", setups, "setup");
  sel("jf-outcome", ["win", "loss", "breakeven"], "outcome");
}

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
    body.innerHTML = '<tr class="empty-row"><td colspan="10">Journal is empty</td></tr>';
    return;
  }
  for (const t of rows) {
    const tr = document.createElement("tr");
    const side = String(t.side || "").toUpperCase();
    const outcome = t.outcome || "--";
    const notes = [t.reason, t.mistakes, t.lessons].filter(Boolean).join(" · ");
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
        <button class="btn tiny" data-ticket="${t.ticket}" title="Edit notes">✎</button>
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
  window._journalSortable.setRows($("journal-body").querySelectorAll("tr:not(.empty-row)"));
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
  $("journal-editor").classList.add("hidden");
}

$("je-cancel").addEventListener("click", closeJournalEditor);
$("je-save").addEventListener("click", async () => {
  const ed = $("journal-editor");
  const ticket = parseInt(ed.dataset.ticket, 10);
  try {
    await apiPost(`/api/journal/${ticket}/notes`, {
      reason: $("je-reason").value.trim(),
      mistakes: $("je-mistakes").value.trim(),
      lessons: $("je-lessons").value.trim(),
    });
    toast(`Notes saved for #${ticket}`, "up");
  } catch (err) {
    toast(`Save failed: ${err.message}`, "down");
  }
  closeJournalEditor();
  loadJournal();
});

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
  $("jf-symbol").addEventListener("change", (e) => { state.journalFilters.symbol = e.target.value; renderJournal(); });
  $("jf-setup").addEventListener("change", (e) => { state.journalFilters.setup = e.target.value; renderJournal(); });
  $("jf-outcome").addEventListener("change", (e) => { state.journalFilters.outcome = e.target.value; renderJournal(); });
  $("jf-csv").addEventListener("click", exportJournalCsv);
  $("jf-sync").addEventListener("click", async () => {
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
  for (const s of state.watchlist) {
    const chip = document.createElement("span");
    chip.className = "wl-chip" + (s === state.symbol ? " active" : "");
    chip.textContent = s;
    chip.draggable = true;
    chip.dataset.symbol = s;
    chip.title = "click: load · shift+click: remove · drag: reorder";
    chip.onclick = (e) => {
      if (e.shiftKey) {
        state.watchlist = state.watchlist.filter((x) => x !== s);
        savePrefs();
        renderWatchlist();
        return;
      }
      state.symbol = s;
      $("symbol-input").value = s;
      loadChart();
    };
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
}

/* ============================================================
   Chart (TradingView lightweight-charts v4)
   ============================================================ */
const chartWrap = document.querySelector(".chart-wrap");
const priceEl = $("price-chart");
const rsiEl = $("rsi-chart");
const macdEl = $("macd-chart");
const tooltip = $("tooltip");

let priceChart = null, rsiChart = null, macdChart = null;
let candleSeries = null, ema50Series = null, ema200Series = null;
let rsiSeries = null, macdLineSeries = null, macdSignalSeries = null, macdHistSeries = null;
let zonePriceLines = [];
let syncGuard = false;

const CHART_OPTS = {
  layout: { background: { type: "solid", color: C.bg }, textColor: C.dim, fontSize: 11 },
  grid: { vertLines: { color: "rgba(42,46,57,0.5)" }, horzLines: { color: "rgba(42,46,57,0.5)" } },
  crosshair: { mode: 1, vertLine: { color: C.dim, style: 3, labelBackgroundColor: C.border }, horzLine: { color: C.dim, style: 3, labelBackgroundColor: C.border } },
  rightPriceScale: { borderColor: C.border },
  timeScale: { borderColor: C.border, rightOffset: 6, barSpacing: 7 },
  handleScroll: { mouseWheel: true, pressedMouseMove: true },
  handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
};

function paneSizes() {
  const h = chartWrap.getBoundingClientRect().height;
  const showRsi = state.indicators.rsi;
  const showMacd = state.indicators.macd;
  let mainH = h;
  if (showRsi && showMacd) mainH = h * 0.62;
  else if (showRsi || showMacd) mainH = h * 0.74;
  return {
    price: Math.max(180, mainH - 10),
    rsi: showRsi ? Math.max(60, (h - mainH) * 0.45 - 8) : 0,
    macd: showMacd ? Math.max(70, (h - mainH) * 0.55 - 8) : 0,
  };
}

function layoutPanes() {
  const s = paneSizes();
  const apply = (chart, el, h, hidden) => {
    if (!chart) return;
    if (hidden) {
      el.style.height = "0px";
      el.style.visibility = "hidden";
      el.style.overflow = "hidden";
    } else {
      el.style.height = h + "px";
      el.style.visibility = "visible";
      el.style.overflow = "visible";
      chart.applyOptions({ width: el.clientWidth, height: h });
    }
  };
  apply(priceChart, priceEl, s.price, false);
  apply(rsiChart, rsiEl, s.rsi, !s.rsi);
  apply(macdChart, macdEl, s.macd, !s.macd);
}

function setupCharts() {
  priceChart = LightweightCharts.createChart(priceEl, { ...CHART_OPTS });
  candleSeries = priceChart.addCandlestickSeries({
    upColor: C.up, downColor: C.down,
    borderUpColor: C.up, borderDownColor: C.down,
    wickUpColor: C.up, wickDownColor: C.down,
  });
  ema50Series = priceChart.addLineSeries({ color: C.ema50, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  ema200Series = priceChart.addLineSeries({ color: C.ema200, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

  rsiChart = LightweightCharts.createChart(rsiEl, { ...CHART_OPTS });
  rsiSeries = rsiChart.addLineSeries({ color: C.rsi, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  rsiSeries.createPriceLine({ price: 70, color: "rgba(120,123,134,0.5)", lineStyle: 2, lineWidth: 1, title: "" });
  rsiSeries.createPriceLine({ price: 30, color: "rgba(120,123,134,0.5)", lineStyle: 2, lineWidth: 1, title: "" });

  macdChart = LightweightCharts.createChart(macdEl, { ...CHART_OPTS });
  macdHistSeries = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
  macdHistSeries.createPriceLine({ price: 0, color: "rgba(120,123,134,0.5)", lineStyle: 0, lineWidth: 1, title: "" });
  macdLineSeries = macdChart.addLineSeries({ color: C.macd, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  macdSignalSeries = macdChart.addLineSeries({ color: C.signal, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

  // pane crosshair sync (visible range + crosshair)
  let syncGuard = false;
  for (const [src, others] of [[priceChart, [rsiChart, macdChart]], [rsiChart, [priceChart, macdChart]], [macdChart, [priceChart, rsiChart]]]) {
    src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncGuard) return;
      syncGuard = true;
      for (const o of others) o && o.timeScale().setVisibleLogicalRange(range);
      syncGuard = false;
    });
    src.subscribeCrosshairMove((param) => {
      if (syncGuard) return;
      syncGuard = true;
      for (const o of others) o && o.setCrosshairPosition(param.point, param.time);
      syncGuard = false;
    });
  }

  // double-click to reset zoom
  const panes = [
    { chart: priceChart, el: priceEl },
    { chart: rsiChart, el: rsiEl },
    { chart: macdChart, el: macdEl },
  ];
  panes.forEach(({ chart, el }) => {
    if (chart && el) {
      el.addEventListener("dblclick", () => {
        chart.timeScale().resetTimeScale();
        chart.priceScale().resetPriceScale();
      });
    }
  });

  // enhanced tooltip with indicators
  priceChart.subscribeCrosshairMove((param) => updateTooltip(param));

  layoutPanes();
  new ResizeObserver(() => { layoutPanes(); }).observe(chartWrap);
  window.addEventListener("resize", () => { layoutPanes(); renderEquity(state.equity); });
}

function setSeriesData() {
  const d = state.data;
  if (!d || !candleSeries) return;
  const n = d.count;
  const candles = [];
  const ema50 = [], ema200 = [], rsi = [], macdL = [], macdS = [], macdH = [];
  let emaStarted = false;
  for (let i = 0; i < n; i++) {
    const t = d.times[i];
    candles.push({ time: t, open: d.open[i], high: d.high[i], low: d.low[i], close: d.close[i] });
    const e50 = d.ema50[i], e200 = d.ema200[i];
    if (!isNaN(e50) && !isNaN(e200)) { emaStarted = true; }
    if (emaStarted) {
      if (!isNaN(e50)) ema50.push({ time: t, value: e50 });
      if (!isNaN(e200)) ema200.push({ time: t, value: e200 });
    }
    if (state.indicators.rsi && !isNaN(d.rsi[i])) rsi.push({ time: t, value: d.rsi[i] });
    if (state.indicators.macd) {
      if (!isNaN(d.macd[i])) macdL.push({ time: t, value: d.macd[i] });
      if (!isNaN(d.macd_signal[i])) macdS.push({ time: t, value: d.macd_signal[i] });
      if (!isNaN(d.macd_hist[i])) {
        macdH.push({ time: t, value: d.macd_hist[i], color: d.macd_hist[i] >= 0 ? "rgba(38,166,154,0.65)" : "rgba(239,83,80,0.65)" });
      }
    }
  }
  candleSeries.setData(candles);
  if (state.indicators.ema) { ema50Series.setData(ema50); ema200Series.setData(ema200); }
  else { ema50Series.setData([]); ema200Series.setData([]); }
  if (state.indicators.rsi) rsiSeries.setData(rsi);
  else rsiSeries.setData([]);
  if (state.indicators.macd) { macdLineSeries.setData(macdL); macdSignalSeries.setData(macdS); macdHistSeries.setData(macdH); }
  else { macdLineSeries.setData([]); macdSignalSeries.setData([]); macdHistSeries.setData([]); }

  state.lastBar = { time: d.times[n - 1], open: d.open[n - 1], high: d.high[n - 1], low: d.low[n - 1], close: d.close[n - 1] };
  layoutPanes();
  drawMarkers();
  drawPriceLines();
}

function setMarkersCommon(markers) {
  if (candleSeries) candleSeries.setMarkers(markers);
}

function drawMarkers() {
  const d = state.data, bot = state.bot.data;
  const markers = [];
  if (bot) {
    for (const sw of bot.swings.highs) {
      markers.push({ time: d.times[sw.index], position: "belowBar", color: sw.label === "H" ? C.dim : C.up, shape: "arrowUp", text: sw.label });
    }
    for (const sw of bot.swings.lows) {
      markers.push({ time: d.times[sw.index], position: "aboveBar", color: sw.label === "L" ? C.dim : C.down, shape: "arrowDown", text: sw.label });
    }
    const STRUCT_SHORT = { BOS: "BOS", CHOCH: "CH", liquidity_sweep: "SWP", fake_breakout: "FB" };
    const seen = new Set();
    for (const p of bot.patterns) {
      if (p.direction === "neutral" || p.confidence < 0.6) continue;
      const isStruct = STRUCT_SHORT[p.pattern];
      const recent = d.count - p.index <= 25;
      if (!isStruct && !recent) continue;
      if (seen.has(p.index)) continue;
      seen.add(p.index);
      const bullish = p.direction === "bullish";
      markers.push({
        time: d.times[p.index],
        position: bullish ? "belowBar" : "aboveBar",
        color: bullish ? C.up : C.down,
        shape: "circle",
        text: isStruct || p.pattern === "engulfing" || p.pattern === "pin_bar" || p.pattern === "hammer" || p.pattern === "shooting_star"
          ? (STRUCT_SHORT[p.pattern] || p.pattern.split("_")[0])
          : "",
      });
    }
  }
  setMarkersCommon(markers);
}

function clearPriceLines() {
  for (const [series, line] of zonePriceLines) {
    try { series.removePriceLine(line); } catch (e) { /* already removed */ }
  }
  zonePriceLines = [];
}

function addPriceLine(series, price, color, dash, title) {
  try {
    const line = series.createPriceLine({ price, color, lineWidth: 1, lineStyle: dash, axisLabelVisible: true, title: title || "" });
    zonePriceLines.push([series, line]);
  } catch (e) { /* out of range - ignore */ }
}

function drawPriceLines() {
  clearPriceLines();
  const d = state.data, bot = state.bot.data;
  if (!d) return;

  // bot zones: strongest unbroken first, cap 6; faded for broken
  if (bot && bot.zones.length) {
    const zones = [...bot.zones].sort((a, b) => b.strength - a.strength);
    const unbroken = zones.filter((z) => !z.broken).slice(0, 6);
    const broken = zones.filter((z) => z.broken).slice(0, 2);
    for (const z of unbroken) {
      const color = z.type === "supply" ? "rgba(239,83,80,0.75)" : "rgba(38,166,154,0.75)";
      addPriceLine(candleSeries, z.upper, color, 2, "Z");
      addPriceLine(candleSeries, z.lower, color, 2, "Z");
    }
    for (const z of broken) {
      addPriceLine(candleSeries, z.upper, "rgba(120,123,134,0.5)", 4, "ZB");
      addPriceLine(candleSeries, z.lower, "rgba(120,123,134,0.5)", 4, "ZB");
    }
  }

  // plan zone + entry/SL/TP
  const plan = state.plan;
  if (plan && plan.action !== "NONE") {
    if (plan.zone_upper && plan.zone_lower) {
      const zc = plan.zone_type === "supply" ? "rgba(239,83,80,0.9)" : "rgba(38,166,154,0.9)";
      addPriceLine(candleSeries, plan.zone_upper, zc, 2, "PLAN Z");
      addPriceLine(candleSeries, plan.zone_lower, zc, 2, "PLAN Z");
    }
    const buy = plan.action === "BUY";
    if (plan.entry) addPriceLine(candleSeries, plan.entry, buy ? C.up : C.down, 2, "ENTRY");
    if (plan.sl) addPriceLine(candleSeries, plan.sl, C.down, 4, "SL");
    if (plan.tp) addPriceLine(candleSeries, plan.tp, C.up, 4, "TP");
  }

  // open positions on this symbol
  for (const p of state.positions) {
    if (p.symbol !== state.symbol) continue;
    const entryColor = p.total >= 0 ? C.up : C.down;
    addPriceLine(candleSeries, p.price_open, entryColor, 2, `${p.type} ${p.volume}`);
    if (p.sl) addPriceLine(candleSeries, p.sl, C.down, 4, "SL");
    if (p.tp) addPriceLine(candleSeries, p.tp, C.up, 4, "TP");
  }
}

function updateTooltip(param) {
  const d = state.data;
  if (!d || !param.time || param.point === undefined) {
    tooltip.classList.remove("visible");
    return;
  }
  const i = d.times.indexOf(param.time);
  if (i < 0) { tooltip.classList.remove("visible"); return; }
  const o = d.open[i], h = d.high[i], l = d.low[i], c = d.close[i];
  const rows = [
    `<div class="tt-title">${new Date(d.times[i] * 1000).toLocaleString()}  ${state.symbol} ${state.timeframe}</div>`,
    `<div class="tt-row">O ${fmtPrice(o)}  H ${fmtPrice(h)}</div>`,
    `<div class="tt-row">L ${fmtPrice(l)}  C <span style="color:${c >= o ? C.up : C.down}">${fmtPrice(c)}</span></div>`,
  ];
  if (state.indicators.ema) {
    rows.push(`<div class="tt-row" style="color:${C.ema50}">EMA50 ${fmtPrice(d.ema50[i])}  <span style="color:${C.ema200}">EMA200 ${fmtPrice(d.ema200[i])}</span></div>`);
  }
  if (state.indicators.rsi) rows.push(`<div class="tt-row" style="color:${C.rsi}">RSI ${d.rsi[i].toFixed(1)}</div>`);
  if (state.indicators.macd) {
    rows.push(`<div class="tt-row" style="color:${C.macd}">MACD ${d.macd[i].toFixed(5)}  <span style="color:${C.signal}">Sig ${d.macd_signal[i].toFixed(5)}</span></div>`);
  }
  const bot = state.bot.data;
  if (bot) {
    const barPatterns = bot.patterns.filter((p) => p.index === i && p.confidence >= 0.6);
    if (barPatterns.length) {
      rows.push(`<div class="tt-row" style="color:${C.purple}">` +
        barPatterns.map((p) => `${p.pattern} ${p.direction} ${(p.confidence * 100).toFixed(0)}%`).join(" | ") + `</div>`);
    }
    const close = d.close[i];
    const covering = bot.zones.filter((z) => close >= z.lower && close <= z.upper);
    if (covering.length) {
      rows.push(`<div class="tt-row" style="color:${C.yellow}">Zone: ` +
        covering.map((z) => `${z.type} ${z.strength.toFixed(0)}/100 ${z.touches}t${z.broken ? " broken" : ""}`).join(" | ") + `</div>`);
    }
  }
  const plan = state.plan;
  if (plan && plan.action !== "NONE" && plan.entry != null) {
    rows.push(`<div class="tt-row" style="color:${plan.action === "BUY" ? C.up : C.down}">PLAN ${plan.action}` +
      ` E ${fmtPrice(plan.entry)} SL ${fmtPrice(plan.sl)} TP ${fmtPrice(plan.tp)}` +
      ` RR ${plan.risk_reward ?? "--"} conf ${plan.confidence ?? "--"}%</div>`);
  }
  tooltip.innerHTML = rows.join("");
  tooltip.classList.add("visible");
  const left = param.point.x + 14;
  tooltip.style.left = (left + tooltip.offsetWidth > chartWrap.clientWidth ? param.point.x - tooltip.offsetWidth - 14 : left) + "px";
  tooltip.style.top = "8px";
}

async function loadChart() {
  const symbol = state.symbol.trim().toUpperCase() || "EURUSD";
  try {
    const q = `symbol=${encodeURIComponent(symbol)}&timeframe=${state.timeframe}&count=${state.count}`;
    const [d, bot] = await Promise.all([
      api(`/api/rates?${q}`),
      api(`/api/bot-analysis?${q}`).catch(() => null),
    ]);
    state.data = d;
    state.bot.data = bot;
    state.symbol = d.symbol;
    $("symbol-input").value = d.symbol;
    const t = d.tick;
    $("chart-tick").textContent = `Bid ${fmtPrice(t.bid)}  Ask ${fmtPrice(t.ask)}  Spread ${t.spread}`;
    updateBotBadge();
    savePrefs();
    renderWatchlist();
    setSeriesData();
    loadPlan();
    startQuoteTimer();
  } catch (e) {
    $("chart-tick").textContent = "No data for " + symbol;
  }
}

function startQuoteTimer() {
  if (state.quoteTimer) clearInterval(state.quoteTimer);
  state.quoteTimer = setInterval(async () => {
    if (document.hidden) return;
    try {
      const t = await api(`/api/quote?symbol=${encodeURIComponent(state.symbol)}`);
      $("chart-tick").textContent = `Bid ${fmtPrice(t.bid)}  Ask ${fmtPrice(t.ask)}  Spread ${t.spread}`;
      if (state.lastBar && candleSeries && state.data) {
        const close = state.symbol.indexOf("XAU") >= 0 || state.symbol.indexOf("XAG") >= 0 ? t.ask : t.bid;
        candleSeries.update({
          ...state.lastBar,
          high: Math.max(state.lastBar.high, close),
          low: Math.min(state.lastBar.low, close),
          close,
        });
      }
    } catch (e) { /* symbol may be invalid - ignore */ }
  }, QUOTE_MS);
}

function updateBotBadge() {
  const el = $("bot-badge");
  const t = state.bot.data?.trend;
  if (!t) { el.className = "bot-badge hidden"; return; }
  el.textContent = `BOT ${t.direction.toUpperCase()} ${(t.confidence * 100).toFixed(0)}% · HH${t.hh} HL${t.hl} LH${t.lh} LL${t.ll}`;
  el.className = `bot-badge ${t.direction}`;
}

/* ---------- Symbols ---------- */
async function loadSymbols() {
  try {
    const { symbols } = await api("/api/symbols");
    const dl = $("symbol-list");
    dl.innerHTML = symbols.slice(0, 1000).map((s) => `<option value="${esc(s)}">`).join("");
  } catch (e) { /* offline - ignore */ }
}

/* ---------- Controls ---------- */
function setTf(tf) {
  state.timeframe = tf;
  document.querySelectorAll(".tf-btn").forEach((b) => b.classList.toggle("active", b.dataset.tf === tf));
  savePrefs();
  loadChart();
}

function buildTfButtons() {
  const group = $("tf-group");
  for (const tf of TFs) {
    const btn = document.createElement("button");
    btn.className = "tf-btn" + (tf === state.timeframe ? " active" : "");
    btn.dataset.tf = tf;
    btn.textContent = tf;
    btn.onclick = () => setTf(tf);
    group.appendChild(btn);
  }
}

function initControls() {
  const input = $("symbol-input");
  const applySymbol = () => {
    state.symbol = input.value.trim().toUpperCase() || "EURUSD";
    loadChart();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") applySymbol(); });
  input.addEventListener("change", applySymbol);

  const cfg = { ema: "chk-ema", rsi: "chk-rsi", macd: "chk-macd", bot: "chk-bot" };
  for (const key in cfg) {
    $(cfg[key]).addEventListener("change", (e) => {
      if (key === "bot") state.bot.on = e.target.checked;
      else state.indicators[key] = e.target.checked;
      savePrefs();
      if (key === "ema" || key === "rsi" || key === "macd") { setSeriesData(); layoutPanes(); }
      else { drawMarkers(); drawPriceLines(); }
    });
  }

  $("count-select").addEventListener("change", (e) => {
    state.count = parseInt(e.target.value, 10);
    savePrefs();
    loadChart();
  });

  $("eq-balance").addEventListener("change", (e) => toggleEquityOpt("balance", e.target));
  $("eq-floating").addEventListener("change", (e) => toggleEquityOpt("floating", e.target));
  $("eq-ma5").addEventListener("change", (e) => toggleEquityOpt("ma5", e.target));
  $("eq-dd").addEventListener("change", (e) => toggleEquityOpt("dd", e.target));

  const wlInput = $("wl-add");
  wlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      addToWatchlist(wlInput.value);
      wlInput.value = "";
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const idx = ["1", "2", "3", "4", "5", "6", "7", "8"].indexOf(e.key);
    if (idx >= 0) setTf(TFs[idx]);
  });
}

/* ---------- Boot ---------- */
/* ---------- Backtesting ---------- */
const bt = {
  groups: null,
  mc: null,
  group: "month",
  detail: null,
  running: false,
};

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

/* ---------- Market Intelligence Agent ---------- */

async function loadMarketIntel() {
  showSkeleton($("mi-body"), "cards", 3);
  try {
    const scan = await api("/api/market-scan?timeframe=H1&min_score=0&max_results=15");
    const primary = await api(`/api/market-intelligence/${encodeURIComponent(state.symbol)}`);
    state.marketIntel = { scan, primary };
    renderMarketIntel(scan, primary);
  } catch (e) { /* market intel is best-effort */ }
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
     <div class="mi-regime dim">${esc(p.regime || "--")} · ${esc(p.trend || "--")}</div></div>`,
    `<div class="mi-card"><div class="mi-title">Best Candidates</div>${scan.ranked.slice(0, 8).map((r) => `
       <div class="mi-row">
         <span class="mi-sym">${esc(r.symbol)}</span>
         <span class="mi-hs" style="color:${r.health_score >= 70 ? C.up : r.health_score >= 50 ? C.yellow : C.down}">${r.health_score}/100</span>
         <span class="mi-reg">${esc(r.regime)}</span>
         <span class="mi-trend" style="color:${r.trend === "bullish" ? C.up : r.trend === "bearish" ? C.down : C.dim}">${esc(r.trend)}</span>
         ${r.abnormal_volatility ? '<span class="mi-vol-warn" title="Abnormal volatility">⚡</span>' : ""}
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
  } catch (e) { /* research is best-effort */ }
}

async function loadWeeklyResearch() {
  showSkeleton($("rch-body"), "cards", 1);
  try {
    const report = await api("/api/weekly-research-report?save=true");
    state.research = report;
    renderResearch(report);
  } catch (e) { /* research is best-effort */ }
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
  } catch (e) { /* coach is best-effort */ }
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
       <div class="ch-title">#${review.ticket} · ${esc(review.symbol)} ${esc(review.side)} ${esc(review.setup || "")}</div>
       <div class="ch-verdict" style="color:${verdictColor}">${esc(review.verdict)}</div>
       <div class="ch-meta dim">${esc(review.close_time || "")} · RR ${review.rr ?? "--"} · conf ${review.confidence ?? "--"}% · score ${review.trade_score ?? "--"}</div>
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
    <input id="bt-zscore" type="number" step="1" value="40" title="Min zone score">
    <input id="bt-rr" type="number" step="0.5" value="3" title="Min RR">
    <input id="bt-tpmin" type="number" value="80" title="TP min (pips)">
    <input id="bt-tpmax" type="number" value="100" title="TP max (pips)">
    <input id="bt-slmin" type="number" value="20" title="SL min (pips)">
    <input id="bt-risk" type="number" step="0.1" value="0.5" title="Risk %">
    <input id="bt-bars" type="number" value="1500" title="Bars (confirmation timeframe)">
    <input id="bt-since" placeholder="Since YYYY-MM-DD" title="Forward window start (optional)">
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

function renderBacktests() {
  $("bt-sub").textContent = `saved results, updated ${fmtWhen(state.btGenerated)}`;
  const rows = (bt.groups && bt.groups[bt.group]) || [];
  const mc = bt.group === "month" ? bt.mc : null;
  $("bt-body").innerHTML =
    btFormHtml() +
    `<div class="bt-tabs">${["month", "forward", "all"].map((g) =>
      `<button class="btn small bt-tab${g === bt.group ? " active" : ""}" data-bt="tab" data-g="${g}">${g}</button>`).join("")}
    </div>` +
    (mc ? mcHtml(mc) : "") +
    rowsTable(rows);

  // init sortable + row selection after innerHTML
  setTimeout(() => {
    const table = $("bt-body").querySelector("table.data-table");
    if (table && !window._backtestsSortable) {
      window._backtestsSortable = makeSortable(table, {
        pageSize: 25,
        numericCols: [2, 3, 4, 5, 6, 7, 8],
        dateCols: [9],
      });
    }
    if (window._backtestsSortable) {
      window._backtestsSortable.setRows(table.querySelectorAll("tbody tr"));
    }
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
  $("bt-body").innerHTML =
    `<div class="bt-detail-actions">
      <button class="btn small" data-bt="back">&#8592; Back to results</button>
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
  const btn = forward ? $("bt-forward") : $("bt-run");
  bt.running = true;
  btn.disabled = true;
  btn.textContent = forward ? "Running forward test..." : "Running backtest...";
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
    btn.disabled = false;
    btn.textContent = forward ? "Forward test" : "Run backtest";
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
        <button class="btn small" data-bt="back">← Back</button>
      </div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </div>`;
  $("bt-body").innerHTML = html;
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
  loadChart();
  loadSymbols();
  loadHealth();
  loadEquity();
  loadJournal();
  loadRisk();
  loadSessions();
  loadNews();
  loadBacktests();
  loadMarketIntel();
  loadResearch();
  loadCoach();

  const tick = (fn) => () => { if (!document.hidden) fn(); };
  setInterval(tick(loadSummary), POLL_MS);
  setInterval(tick(loadChart), CHART_MS);
  setInterval(tick(loadPlan), SLOW_MS);
  setInterval(tick(loadEquity), SLOW_MS);
  setInterval(tick(loadJournal), SLOW_MS);
  setInterval(tick(loadRisk), SLOW_MS);
  setInterval(tick(loadSessions), SLOW_MS * 2);
  setInterval(tick(loadNews), NEWS_MS);
  setInterval(tick(loadHealth), 15000);
}

init();
