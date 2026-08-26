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
        headers: {
          "Content-Type": "application/json",
          ...(AUTH_TOKEN ? { "X-Auth-Token": AUTH_TOKEN } : {}),
        },
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
  indicators: { ema: true, rsi: true, macd: true, sd: true, overlay: false },
  bot: { on: true, data: null },
  data: null,
  positions: [],
  plan: null,
  overlay: null,
  watchlist: [],
  equity: null,
  equityOpts: { balance: true, floating: false, ma5: false, dd: false },
  journal: [],
  journalFilters: { symbol: "", setup: "", outcome: "" },
  lastBar: null,
  quoteTimer: null,
  btGenerated: null,
  btOverlay: { name: null, symbol: null, trades: [] },
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
  const headers = AUTH_TOKEN ? { "X-Auth-Token": AUTH_TOKEN } : {};
  const res = await fetch(path, { headers });
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
const AUTH_TOKEN = document.querySelector('meta[name="auth-token"]')?.content || "";

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

let wsLatency = 0;
let wsLastPong = 0;
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
  const touch = window.matchMedia("(pointer: coarse)").matches;
  for (const s of state.watchlist) {
    const chip = document.createElement("span");
    chip.className = "wl-chip" + (s === state.symbol ? " active" : "");
    chip.textContent = s;
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
let btSlSeries = null, btTpSeries = null;
let rsiSeries = null, macdLineSeries = null, macdSignalSeries = null, macdHistSeries = null;
let zonePriceLines = [];
let zoneSeries = [];
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
  btSlSeries = priceChart.addLineSeries({ color: "rgba(239,83,80,0.7)", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  btTpSeries = priceChart.addLineSeries({ color: "rgba(38,166,154,0.7)", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });

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
  if (state.signals && state.signals.length) {
    const mid = Math.floor(d.times.length / 2);
    for (const s of state.signals) {
      if (s.type !== "parameter") continue;
      const color = s.strength > 0.5 ? C.up : C.down;
      markers.push({ time: d.times[mid], position: "belowBar", color, shape: "triangleDown", text: `${s.confidence_floor}% floor`, size: 8 });
      markers.push({ time: d.times[mid], position: "aboveBar", color, shape: "triangleUp", text: `${s.size_multiplier}x size`, size: 8 });
    }
  }
  appendBtMarkers(markers, d);
  setMarkersCommon(markers);
}

/* ---------- Backtest trades overlay ---------- */

function btOverlayOn() {
  const cb = $("chk-bt");
  return !!(cb && cb.checked && state.btOverlay && state.btOverlay.trades.length);
}

function _barIdx(ts, times) {
  if (ts == null || !times.length) return -1;
  if (ts < times[0] || ts > times[times.length - 1]) return -1;
  let lo = 0, hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= ts) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

function appendBtMarkers(markers, d) {
  if (!btOverlayOn()) return;
  const times = d.times;
  const trades = state.btOverlay.trades.slice(-200);
  const byBar = new Map();
  for (const t of trades) {
    if (t.entry_ts == null || t.exit_ts == null) continue;
    const e = _barIdx(t.entry_ts, times);
    const x = _barIdx(t.exit_ts, times);
    if (e < 0 && x < 0) continue;
    if (e >= 0 && !byBar.has("e" + e)) {
      byBar.set("e" + e, true);
      markers.push({
        time: times[e],
        position: t.action === "BUY" ? "belowBar" : "aboveBar",
        color: t.action === "BUY" ? C.up : C.down,
        shape: t.action === "BUY" ? "arrowUp" : "arrowDown",
        text: "E",
        size: 1,
      });
    }
    if (x >= 0 && !byBar.has("x" + x)) {
      byBar.set("x" + x, true);
      const win = t.pnl >= 0;
      markers.push({
        time: times[x],
        position: win ? "aboveBar" : "belowBar",
        color: win ? C.up : C.down,
        shape: "circle",
        text: (win ? "+" : "") + Math.round(Math.abs(t.pnl)),
        size: 1,
      });
    }
  }
}

function drawBtSegments() {
  if (!btOverlayOn()) {
    if (btSlSeries) { btSlSeries.setData([]); btTpSeries.setData([]); }
    return;
  }
  const times = state.data.times;
  const trades = state.btOverlay.trades.slice(-120);
  const build = (key) => {
    const pts = [];
    let prevX = -1;
    for (const t of trades) {
      if (t.entry_ts == null || t.exit_ts == null) continue;
      const v = t[key];
      if (v == null) continue;
      let e = _barIdx(t.entry_ts, times);
      let x = _barIdx(t.exit_ts, times);
      if (e < 0 || x < 0) continue;
      if (e > x) { const tmp = e; e = x; x = tmp; }
      if (pts.length && e > prevX) pts.push({ time: times[e] });
      if (e === x) pts.push({ time: times[e], value: v });
      else pts.push({ time: times[e], value: v }, { time: times[x], value: v });
      prevX = x;
    }
    return pts;
  };
  btSlSeries.setData(build("sl"));
  btTpSeries.setData(build("tp"));
}

async function loadBtOverlay(symbol) {
  const ov = state.btOverlay;
  if (!ov || !ov.name) { ov.trades = []; return; }
  if (ov.symbol !== (symbol || state.symbol)) { ov.trades = []; return; }
  try {
    const res = await api(`/api/backtest/trades?name=${encodeURIComponent(ov.name)}`);
    ov.trades = (res && res.trades) || [];
    if (res && res.symbol) ov.symbol = res.symbol;
  } catch (e) {
    ov.trades = [];
    console.warn("Could not load backtest overlay:", e);
  }
}

async function showBtOnChart(d) {
  const name = d.saved || d.name || "";
  if (!name) { toast("Run has no saved result - run a backtest first", "down"); return; }
  state.btOverlay = { name, symbol: d.params.symbol, trades: [] };
  const cb = $("chk-bt");
  if (cb) cb.checked = true;
  if (state.symbol !== state.btOverlay.symbol) {
    state.symbol = state.btOverlay.symbol;
    $("symbol-input").value = state.symbol;
    await loadChart();
  } else {
    await loadBtOverlay();
    drawMarkers();
    drawBtSegments();
  }
  toast(`Showing ${state.btOverlay.trades.length} trade(s) on chart`);
}

function clearPriceLines() {
  for (const [series, line] of zonePriceLines) {
    try { series.removePriceLine(line); } catch (e) { /* already removed */ }
  }
  zonePriceLines = [];
  for (const s of zoneSeries) {
    try { priceChart.removeSeries(s); } catch (e) { /* already removed */ }
  }
  zoneSeries = [];
}

function hexToRgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

function drawZoneRect(z, d) {
  const n = d.count;
  if (n === 0) return;
  const tFrom = z.formation_index >= 0 && z.formation_index < n ? d.times[z.formation_index] : d.times[0];
  let tTo = tFrom;
  if (z.last_touch_index >= (z.formation_index >= 0 ? z.formation_index : 0) && z.last_touch_index < n) tTo = d.times[z.last_touch_index];
  else tTo = d.times[n - 1];
  if (tTo < tFrom) tTo = d.times[n - 1];

  const base = z.type === "supply" ? C.down : C.up;
  const strengthFrac = Math.max(0.1, Math.min(1, z.strength / 100));
  const alpha = z.broken ? 0.06 : 0.10 + 0.22 * strengthFrac;
  const fill = hexToRgba(base, alpha);

  const s = priceChart.addBaselineSeries({
    baseValue: { type: "price", price: z.lower },
    topLineColor: z.broken ? "rgba(120,123,134,0.25)" : hexToRgba(base, 0.35 + 0.5 * strengthFrac),
    bottomLineColor: "rgba(0,0,0,0)",
    topFillColor1: fill,
    topFillColor2: fill,
    bottomFillColor1: "rgba(0,0,0,0)",
    bottomFillColor2: "rgba(0,0,0,0)",
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => null,
  });
  s.setData([
    { time: tFrom, value: z.upper },
    { time: tTo, value: z.upper },
  ]);
  zoneSeries.push(s);
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

  // bot zones: filled rectangles spanning formation -> last touch, alpha by strength
  if (state.indicators.sd && bot && bot.zones.length) {
    const zones = [...bot.zones].sort((a, b) => b.strength - a.strength);
    const unbroken = zones.filter((z) => !z.broken).slice(0, 10);
    const broken = zones.filter((z) => z.broken).slice(0, 4);
    for (const z of [...unbroken, ...broken]) drawZoneRect(z, d);
    const strong = unbroken.slice(0, 3);
    for (const z of strong) {
      const color = z.type === "supply" ? "rgba(239,83,80,0.9)" : "rgba(38,166,154,0.9)";
      const mid = (z.upper + z.lower) / 2;
      addPriceLine(candleSeries, mid, color, 1, `${z.type === "supply" ? "S" : "D"} ${Math.round(z.strength)} ${z.touches}t`);
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

  // live strategy overlay: Donchian range, H1 EMA200 trend, breakout/spike setup
  const o = state.overlay;
  if (state.indicators.overlay && o && !o.error) {
    const rangeColor = o.range
      ? (o.breakout ? (o.breakout.side === "BUY" ? "rgba(38,166,154,0.8)" : "rgba(239,83,80,0.8)") : "rgba(120,123,134,0.85)")
      : "rgba(120,123,134,0.85)";
    if (o.range) {
      addPriceLine(candleSeries, o.range.high, rangeColor, 4, `RNG HI c${o.range.compactness != null ? Math.round(o.range.compactness) : ""}`);
      addPriceLine(candleSeries, o.range.low, rangeColor, 4, "RNG LO");
    }
    if (o.trend && o.trend.ema200) {
      const tc = o.trend.direction === "bull" ? "rgba(38,166,154,0.9)" : o.trend.direction === "bear" ? "rgba(239,83,80,0.9)" : "rgba(120,123,134,0.9)";
      addPriceLine(candleSeries, o.trend.ema200, tc, 2, `H1 EMA200 ${o.trend.direction}`);
    }
    if (o.breakout && state.timeframe === "M15") {
      const bc = o.breakout.side === "BUY" ? C.up : C.down;
      addPriceLine(candleSeries, o.breakout.entry, bc, 4, `BO ${o.breakout.side} ${Math.round(o.breakout.confidence)}%`);
      addPriceLine(candleSeries, o.breakout.sl, C.down, 4, "BO SL");
      addPriceLine(candleSeries, o.breakout.tp, C.up, 4, "BO TP");
    }
    if (o.spike) {
      const sc = o.spike.direction === "up" ? C.up : C.down;
      if (o.spike.entry_price) addPriceLine(candleSeries, o.spike.entry_price, sc, 4, `SPIKE ${String(o.spike.direction).toUpperCase()} ${Math.round(o.spike.confidence)}%`);
      if (o.spike.sl_price) addPriceLine(candleSeries, o.spike.sl_price, C.down, 4, "SPIKE SL");
      if (o.spike.tp_price) addPriceLine(candleSeries, o.spike.tp_price, C.up, 4, "SPIKE TP");
    }
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
  if (btOverlayOn()) {
    const hits = state.btOverlay.trades.filter((t) =>
      (_barIdx(t.entry_ts, d.times) === i || _barIdx(t.exit_ts, d.times) === i));
    for (const t of hits.slice(-3)) {
      rows.push(`<div class="tt-row" style="color:${t.pnl >= 0 ? C.up : C.down}">BT ${t.action}` +
        ` E ${fmtPrice(t.entry)} X ${fmtPrice(t.exit_price)}` +
        ` ${t.pnl >= 0 ? "+" : ""}${Math.round(t.pnl)}` +
        `${t.reason ? " (" + esc(t.reason) + ")" : ""}</div>`);
    }
  }
  tooltip.innerHTML = rows.join("");
  tooltip.classList.add("visible");
  const left = param.point.x + 14;
  tooltip.style.left = (left + tooltip.offsetWidth > chartWrap.clientWidth ? param.point.x - tooltip.offsetWidth - 14 : left) + "px";
  // Track the crosshair vertically, clamped to the chart area (item #9).
  const tipH = tooltip.offsetHeight;
  const y = Math.min(Math.max(param.point.y - tipH / 2, 4), chartWrap.clientHeight - tipH - 4);
  tooltip.style.top = y + "px";
}

/* ---------- Live strategy overlay ---------- */
async function loadOverlay() {
  if (!state.indicators.overlay) {
    state.overlay = null;
    drawPriceLines();
    return;
  }
  try {
    const o = await api(`/api/live-overlay?symbol=${encodeURIComponent(state.symbol)}&range_bars=24`);
    state.overlay = o || null;
    drawPriceLines();
  } catch (e) {
    state.overlay = null;
  }
}

async function loadChart() {
  const symbol = state.symbol.trim() || "EURUSD";
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

    // Auto-connect WebSocket on page load (after a short delay to let MT5 init)
    setTimeout(() => {
      if (!ws) connectWs();
    }, 3000);

    // Fetch trading signals, bt overlay, and chart overlay in parallel
    const [sigResp, , ] = await Promise.all([
      api(`/api/signals?symbol=${encodeURIComponent(state.symbol)}&timeframe=${state.timeframe}&count=${state.count}`).catch(() => ({ signals: [] })),
      loadBtOverlay(symbol),
      loadOverlay(),
    ]);
    state.signals = sigResp.signals || [];
    setSeriesData();
    drawBtSegments();
    loadPlan();
    startQuoteTimer();
  } catch (e) {
    $("chart-tick").textContent = "No data for " + symbol;
  }
}

async function updatePriceDisplay() {
  if (!state.data || !state.symbol) return;
  
  try {
    const priceResp = await api("/api/price?symbol=" + encodeURIComponent(state.symbol));
    if (priceResp && priceResp.bid > 0) {
      const t = priceResp;
      $("chart-tick").textContent = "Bid " + fmtPrice(t.bid) + " Ask " + fmtPrice(t.ask) + " Spread " + t.spread;
      
      // Update the last candle on the chart if data exists
      if (candleSeries && state.data.times && state.data.times.length > 0) {
        const lastIdx = state.data.times.length - 1;
        state.lastTick = {
          bid: t.bid,
          ask: t.ask,
          spread: t.spread,
          time: Date.now()
        };
      }
    }
  } catch (e) {
    console.debug("Price update failed:", e);
  }
}

// Schedule periodic price updates
function startQuoteTimer() {
  if (state.quoteTimer) clearInterval(state.quoteTimer);
  state.quoteTimer = setInterval(async () => {
    if (document.hidden) return;
    try {
      const t = await api("/api/quote?symbol=" + encodeURIComponent(state.symbol));
      $("chart-tick").textContent = "Bid " + fmtPrice(t.bid) + " Ask " + fmtPrice(t.ask) + " Spread " + t.spread;
      if (state.lastBar && candleSeries && state.data) {
        const close = state.symbol.indexOf("XAU") >= 0 || state.symbol.indexOf("XAG") >= 0 ? t.ask : t.bid;
        candleSeries.update({
          ...state.lastBar,
          high: Math.max(state.lastBar.high, close),
          low: Math.min(state.lastBar.low, close),
          close,
        });
      }
    } catch (e) {
      $("chart-tick").textContent = "No data for " + state.symbol;
    }
  }, 3000);
}

function updateBotBadge() {
  const el = $("bot-badge");
  const t = state.bot.data?.trend;
  if (!t) { el.className = "bot-badge hidden"; return; }
  el.textContent = `BOT ${t.direction.toUpperCase()} ${(t.confidence * 100).toFixed(0)}% Â· HH${t.hh} HL${t.hl} LH${t.lh} LL${t.ll}`;
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
  if (!input) return;
  const applySymbol = () => {
    state.symbol = input.value.trim().toUpperCase() || "EURUSD";
    loadChart();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") applySymbol(); });
  input.addEventListener("change", applySymbol);

  const cfg = { ema: "chk-ema", rsi: "chk-rsi", macd: "chk-macd", bot: "chk-bot", sd: "chk-sd", overlay: "chk-strat" };
  for (const key in cfg) {
    const el = $(cfg[key]);
    el.checked = key === "bot" ? state.bot.on : !!state.indicators[key];
    el.addEventListener("change", (e) => {
      if (key === "bot") state.bot.on = e.target.checked;
      else state.indicators[key] = e.target.checked;
      savePrefs();
      if (key === "ema" || key === "rsi" || key === "macd") { setSeriesData(); layoutPanes(); }
      else if (key === "overlay") loadOverlay();
      else { drawMarkers(); drawPriceLines(); }
    });
  }

  $("count-select").addEventListener("change", (e) => {
    state.count = parseInt(e.target.value, 10);
    savePrefs();
    loadChart();
  });

  $("chk-bt").addEventListener("change", (e) => {
    if (!e.target.checked) {
      state.btOverlay = { name: null, symbol: null, trades: [] };
      drawMarkers();
      drawBtSegments();
      return;
    }
    if (bt.detail && bt.detail.params) showBtOnChart(bt.detail);
    else {
      toast("Open a backtest result first (Backtests tab), then Show on chart", "down");
      e.target.checked = false;
    }
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

  if ($("aid-run")) $("aid-run").addEventListener("click", loadAiDetection);

  // Backtest runner controls
  if ($("#bt-run")) $("#bt-run").addEventListener("click", runBacktest);
  if ($("#bt-refresh")) $("#bt-refresh").addEventListener("click", loadBacktests);
  if ($("#bt-symbol")) $("#bt-symbol").addEventListener("keydown", (e) => { if (e.key === "Enter") runBacktest(); });
  if ($("#bt-since")) $("#bt-since").addEventListener("change", () => {
    const out = $("#bt-out");
    if ($("#bt-since").value) out.value = "forward";
  });
  if ($("#bt-out")) $("#bt-out").addEventListener("change", () => {
    if ($("#bt-out").value === "forward") {
      if ($("#bt-since")) { $("#bt-since").required = true; $("#bt-since").disabled = false; }
    } else {
      if ($("#bt-since")) { $("#bt-since").required = false; $("#bt-since").disabled = true; $("#bt-since").value = ""; }
    }
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

init();