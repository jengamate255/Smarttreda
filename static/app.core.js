/* Split from app.js - loaded in order; shared global scope. */
"use strict";

/* ---------- Error Reporting ---------- */
const errorReport = {
  queue: [],
  maxQueue: 50,
  endpoint: "/api/client-error",
  flushTimer: null,
  _rawConsole: { log: console.log, warn: console.warn, error: console.error },

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
    // Use raw console to avoid infinite loop when console is patched
    const raw = this._rawConsole[level];
    if (raw) raw.call(console, ...args);
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
function getThemeColors() {
  const s = getComputedStyle(document.documentElement);
  return {
    bg: s.getPropertyValue("--bg").trim() || "#131722",
    panel2: s.getPropertyValue("--panel2").trim() || "#181c25",
    border: s.getPropertyValue("--border").trim() || "#2a2e39",
    text: s.getPropertyValue("--text").trim() || "#d1d4dc",
    dim: s.getPropertyValue("--dim").trim() || "#787b86",
    up: s.getPropertyValue("--green").trim() || "#26a69a",
    down: s.getPropertyValue("--red").trim() || "#ef5350",
    ema50: s.getPropertyValue("--yellow").trim() || "#ffd479",
    ema200: s.getPropertyValue("--accent").trim() || "#2962ff",
    rsi: s.getPropertyValue("--purple").trim() || "#9b59b6",
    macd: s.getPropertyValue("--accent").trim() || "#2962ff",
    signal: s.getPropertyValue("--orange").trim() || "#f39c12",
    accent: s.getPropertyValue("--accent").trim() || "#2962ff",
    purple: s.getPropertyValue("--purple").trim() || "#9b59b6",
    yellow: s.getPropertyValue("--yellow").trim() || "#ffd479",
    orange: s.getPropertyValue("--orange").trim() || "#f39c12",
  };
}
let C = getThemeColors();
// Refresh colors when theme changes
const _themeObserver = new MutationObserver(() => { C = getThemeColors(); });
_themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

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

async function apiDelete(path) {
  const headers = {};
  if (AUTH_TOKEN) headers["X-Auth-Token"] = AUTH_TOKEN;
  const res = await fetch(path, { method: "DELETE", headers });
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

let _lastFocusedElement = null;

function showHelp() {
  const el = $("help-modal") || createHelpModal();
  _lastFocusedElement = document.activeElement;
  el.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  // Focus first focusable element
  setTimeout(() => {
    const focusable = el.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    focusable?.focus();
  }, 0);
  // Trap focus
  el.addEventListener('keydown', trapFocus);
}

function closeModals() {
  document.querySelectorAll(".modal:not(.hidden)").forEach((m) => {
    m.classList.add("hidden");
    m.removeEventListener('keydown', trapFocus);
  });
  document.body.style.overflow = "";
  const sb = $("sidebar");
  if (sb && sb.classList.contains("open")) {
    sb.classList.remove("open");
    $("sidebar-backdrop")?.classList.add("hidden");
  }
  // Restore focus
  if (_lastFocusedElement) {
    _lastFocusedElement.focus();
    _lastFocusedElement = null;
  }
}

function trapFocus(e) {
  if (e.key !== 'Tab') return;
  const modal = e.currentTarget;
  const focusable = modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
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

/* Null-safe fixed-decimal formatter. /api/rates returns JSON null for
   indicator warm-up bars (backend _sanitize maps NaN -> null), so values
   from d.rsi/d.macd/etc. can be null. Global isNaN(null) is false, so
   the explicit null check is required here. */
function fmtNum(v, digits) {
  if (v == null || isNaN(v)) return "--";
  return v.toFixed(digits);
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

