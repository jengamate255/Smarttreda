/* Split from app.js - loaded in order; shared global scope. */
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

const jeCancel = $("je-cancel");
const jeSave = $("je-save");
if (jeCancel) jeCancel.addEventListener("click", closeJournalEditor);
if (jeSave) jeSave.addEventListener("click", async () => {
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

function populateJournalFilters() {
  const entries = state.journal || [];
  const symbols = [...new Set(entries.map((t) => t.symbol).filter(Boolean))].sort();
  const setups = [...new Set(entries.map((t) => t.setup || "manual").filter(Boolean))].sort();
  const outcomes = [...new Set(entries.map((t) => t.outcome).filter(Boolean))].sort();
  const symSel = $("jf-symbol");
  const setupSel = $("jf-setup");
  const outSel = $("jf-outcome");
  if (symSel) {
    const cur = symSel.value;
    symSel.innerHTML = '<option value="">All symbols</option>' + symbols.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    symSel.value = cur;
  }
  if (setupSel) {
    const cur = setupSel.value;
    setupSel.innerHTML = '<option value="">All setups</option>' + setups.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    setupSel.value = cur;
  }
  if (outSel) {
    const cur = outSel.value;
    outSel.innerHTML = '<option value="">All outcomes</option>' + outcomes.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    outSel.value = cur;
  }
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

const _wlLastPx = {};
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
   Chart (TradingView lightweight-charts v4)
   ============================================================ */
const chartWrap = document.querySelector(".chart-container");
const priceEl = $("price-chart");
const rsiEl = $("rsi-chart");
const macdEl = $("macd-chart");
const tooltip = $("tooltip");
const chartStatus = document.createElement("div");
chartStatus.className = "chart-status hidden";
if (chartWrap) chartWrap.appendChild(chartStatus);

let priceChart = null, rsiChart = null, macdChart = null;
let candleSeries = null, ema50Series = null, ema200Series = null;
let btSlSeries = null, btTpSeries = null;
let rsiSeries = null, macdLineSeries = null, macdSignalSeries = null, macdHistSeries = null;
let zonePriceLines = [];
let zoneSeries = [];
let syncGuard = false;
let chartLoadSeq = 0;
let chartDisplayedKey = "";
let chartLoading = false;
let quotePending = false;

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
  if (!chartWrap) return { mainH: 0, rsiH: 0, macdH: 0 };
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
    el.classList.toggle("hidden", hidden);
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

function showChartStatus(message, kind = "loading", compact = false) {
  chartStatus.textContent = message;
  chartStatus.className = `chart-status ${kind}${compact ? " compact" : ""}`;
  chartStatus.setAttribute("role", "status");
}

function hideChartStatus() {
  chartStatus.className = "chart-status hidden";
  chartStatus.textContent = "";
}

async function chartApi(path, timeoutMs = 20000) {
  return api(path, { timeoutMs });
}

function syncChartCrosshair(chart, series, values, param) {
  const index = state.timeIndex?.get(param.time);
  const value = index == null ? null : values?.[index];
  if (!param.point || param.time == null || !Number.isFinite(value) ||
      series.priceToCoordinate(value) == null || chart.timeScale().timeToCoordinate(param.time) == null) {
    chart.clearCrosshairPosition();
    return;
  }
  chart.setCrosshairPosition(value, param.time, series);
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
      try {
        for (const o of others) {
          const series = o === priceChart ? candleSeries : o === rsiChart ? rsiSeries : macdLineSeries;
          const values = o === priceChart ? state.data?.close : o === rsiChart ? state.data?.rsi : state.data?.macd;
          syncChartCrosshair(o, series, values, param);
        }
      } finally { syncGuard = false; }
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
        chart.priceScale("right").applyOptions({ autoScale: true });
      });
    }
  });

  // enhanced tooltip with indicators
  priceChart.subscribeCrosshairMove((param) => updateTooltip(param));

  layoutPanes();
  if (chartWrap) new ResizeObserver(() => { layoutPanes(); }).observe(chartWrap);
  window.addEventListener("resize", () => { layoutPanes(); renderEquity(state.equity); });
}

/* Strict finite-number check for /api/rates indicator arrays. Warm-up bars
   arrive as JSON null (NaN sanitized server-side); global isNaN(null) is
   false, so a plain !isNaN() test would push null values into the chart
   series. Number.isFinite performs no coercion. */
function isFin(v) {
  return v != null && Number.isFinite(v);
}

function setSeriesData(fit = false) {
  const d = state.data;
  if (!d || !candleSeries) return;
  state.timeIndex = new Map(d.times.map((t, idx) => [t, idx]));
  const precision = Number.isInteger(d.digits) ? Math.max(0, Math.min(8, d.digits)) : 5;
  const minMove = isFin(d.point) && d.point > 0 ? d.point : 10 ** -precision;
  const priceFormat = { type: "price", precision, minMove };
  for (const series of [candleSeries, ema50Series, ema200Series, btSlSeries, btTpSeries]) {
    series.applyOptions({ priceFormat });
  }
  const macdPrecision = Math.min(8, precision + 1);
  for (const series of [macdLineSeries, macdSignalSeries, macdHistSeries]) {
    series.applyOptions({ priceFormat: { type: "price", precision: macdPrecision, minMove: 10 ** -macdPrecision } });
  }
  const n = Math.min(d.count, d.times.length, d.open.length, d.high.length, d.low.length, d.close.length);
  const candles = [];
  const ema50 = [], ema200 = [], rsi = [], macdL = [], macdS = [], macdH = [];
  let emaStarted = false;
  for (let i = 0; i < n; i++) {
    const t = d.times[i];
    if (![t, d.open[i], d.high[i], d.low[i], d.close[i]].every(isFin)) continue;
    if (candles.length && t <= candles[candles.length - 1].time) continue;
    candles.push({ time: t, open: d.open[i], high: d.high[i], low: d.low[i], close: d.close[i] });
    const e50 = d.ema50[i], e200 = d.ema200[i];
    if (isFin(e50) && isFin(e200)) { emaStarted = true; }
    if (emaStarted) {
      if (isFin(e50)) ema50.push({ time: t, value: e50 });
      if (isFin(e200)) ema200.push({ time: t, value: e200 });
    }
    if (state.indicators.rsi && isFin(d.rsi[i])) rsi.push({ time: t, value: d.rsi[i] });
    if (state.indicators.macd) {
      if (isFin(d.macd[i])) macdL.push({ time: t, value: d.macd[i] });
      if (isFin(d.macd_signal[i])) macdS.push({ time: t, value: d.macd_signal[i] });
      if (isFin(d.macd_hist[i])) {
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

  state.lastBar = candles.length ? candles[candles.length - 1] : null;
  state.candleTimes = new Set(candles.map(candle => candle.time));
  layoutPanes();
  if (fit) {
    priceChart.timeScale().fitContent();
    if (rsiChart) rsiChart.timeScale().fitContent();
    if (macdChart) macdChart.timeScale().fitContent();
  }
  drawMarkers();
  drawPriceLines();
}

function setMarkersCommon(markers) {
  if (candleSeries) candleSeries.setMarkers(markers.filter(marker => state.candleTimes?.has(marker.time)).sort((a, b) => a.time - b.time));
}

/* Bot-analysis bars can outnumber chart bars: the bot indexes the last
   `count` bars of a wide 1000-bar fetch, while the time-anchored /api/rates
   fetch loses bars to market gaps (weekends). Align via bar-open times
   (bot.times, epoch seconds) so markers/zones land on the correct bar.
   Returns -1 when the event predates the window or cannot be resolved;
   callers must skip those. */
function botChartIndex(i) {
  const d = state.data, bot = state.bot.data;
  if (!d || i == null || i < 0) return -1;
  const t = bot && Array.isArray(bot.times) ? bot.times[i] : null;
  if (t != null) {
    const idx = state.timeIndex ? state.timeIndex.get(t) : d.times.indexOf(t);
    return idx === undefined ? -1 : idx;
  }
  return i < d.count ? i : -1; /* legacy response without a times array */
}

function drawMarkers() {
  const d = state.data, bot = state.bot.data;
  const markers = [];
  if (bot) {
    for (const sw of bot.swings.highs) {
      const idx = botChartIndex(sw.index);
      if (idx < 0) continue;
      markers.push({ time: d.times[idx], position: "belowBar", color: sw.label === "H" ? C.dim : C.up, shape: "arrowUp", text: sw.label });
    }
    for (const sw of bot.swings.lows) {
      const idx = botChartIndex(sw.index);
      if (idx < 0) continue;
      markers.push({ time: d.times[idx], position: "aboveBar", color: sw.label === "L" ? C.dim : C.down, shape: "arrowDown", text: sw.label });
    }
    const STRUCT_SHORT = { BOS: "BOS", CHOCH: "CH", liquidity_sweep: "SWP", fake_breakout: "FB" };
    const seen = new Set();
    for (const p of bot.patterns) {
      if (p.direction === "neutral" || p.confidence < 0.6) continue;
      const idx = botChartIndex(p.index);
      if (idx < 0) continue;
      const isStruct = STRUCT_SHORT[p.pattern];
      const recent = d.count - idx <= 25;
      if (!isStruct && !recent) continue;
      if (seen.has(idx)) continue;
      seen.add(idx);
      const bullish = p.direction === "bullish";
      markers.push({
        time: d.times[idx],
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
      markers.push({ time: d.times[mid], position: "belowBar", color, shape: "arrowDown", text: `${s.confidence_floor}% floor`, size: 8 });
      markers.push({ time: d.times[mid], position: "aboveBar", color, shape: "arrowUp", text: `${s.size_multiplier}x size`, size: 8 });
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
  const seq = chartLoadSeq;
  if (!ov || !ov.name) { ov.trades = []; return; }
  if (ov.symbol !== (symbol || state.symbol)) { ov.trades = []; return; }
  try {
    const res = await api(`/api/backtest/trades?name=${encodeURIComponent(ov.name)}`);
    if (seq !== chartLoadSeq || ov !== state.btOverlay) return;
    ov.trades = (res && res.trades) || [];
    if (res && res.symbol) ov.symbol = res.symbol;
  } catch (e) {
    if (seq !== chartLoadSeq || ov !== state.btOverlay) return;
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
  if (![z.lower, z.upper].every(isFin) || z.upper < z.lower) return;
  const n = d.count;
  if (n === 0) return;
  const fromIdx = botChartIndex(z.formation_index);
  const tFrom = fromIdx >= 0 ? d.times[fromIdx] : d.times[0];
  const fromRef = fromIdx >= 0 ? fromIdx : 0;
  let tTo = tFrom;
  const toIdx = botChartIndex(z.last_touch_index);
  if (toIdx >= fromRef && toIdx < n) tTo = d.times[toIdx];
  else tTo = d.times[n - 1];
  if (tTo < tFrom) tTo = d.times[n - 1];
  if (!isFin(tFrom) || !isFin(tTo) || tTo <= tFrom) return;

  const base = z.type === "supply" ? C.down : C.up;
  const strengthFrac = Math.max(0.1, Math.min(1, z.strength / 100));
  const alpha = z.broken ? 0.06 : 0.10 + 0.22 * strengthFrac;
  const fill = hexToRgba(base, alpha);

  const s = priceChart.addBaselineSeries({
    autoscaleInfoProvider: () => null,
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
    // Auto-trader positions get an AT: prefix on their labels
    const atTag = p.is_auto || (p.magic === 235010) ? "🤖AT " : "";
    const cmt = p.comment ? " " + String(p.comment).slice(0, 12) : "";
    addPriceLine(candleSeries, p.price_open, entryColor, 2, `${atTag}${p.type} ${p.volume}${cmt}`);
    if (p.sl) addPriceLine(candleSeries, p.sl, C.down, 4, `${atTag}SL`);
    if (p.tp) addPriceLine(candleSeries, p.tp, C.up, 4, `${atTag}TP`);
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
  if (state.indicators.rsi) rows.push(`<div class="tt-row" style="color:${C.rsi}">RSI ${fmtNum(d.rsi[i], 1)}</div>`);
  if (state.indicators.macd) {
    rows.push(`<div class="tt-row" style="color:${C.macd}">MACD ${fmtNum(d.macd[i], 5)}  <span style="color:${C.signal}">Sig ${fmtNum(d.macd_signal[i], 5)}</span></div>`);
  }
  const bot = state.bot.data;
  if (bot) {
    const barPatterns = bot.patterns.filter((p) => botChartIndex(p.index) === i && p.confidence >= 0.6);
    if (barPatterns.length) {
      rows.push(`<div class="tt-row" style="color:${C.purple}">` +
        barPatterns.map((p) => `${p.pattern} ${p.direction} ${fmtNum(p.confidence != null ? p.confidence * 100 : null, 0)}%`).join(" | ") + `</div>`);
    }
    const close = d.close[i];
    const covering = bot.zones.filter((z) => close >= z.lower && close <= z.upper);
    if (covering.length) {
      rows.push(`<div class="tt-row" style="color:${C.yellow}">Zone: ` +
        covering.map((z) => `${z.type} ${fmtNum(z.strength, 0)}/100 ${z.touches}t${z.broken ? " broken" : ""}`).join(" | ") + `</div>`);
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
  tooltip.classList.remove("hidden");
  tooltip.classList.add("visible");
  if (!chartWrap) return;
  const left = param.point.x + 14;
  tooltip.style.left = (left + tooltip.offsetWidth > chartWrap.clientWidth ? param.point.x - tooltip.offsetWidth - 14 : left) + "px";
  // Track the crosshair vertically, clamped to the chart area (item #9).
  const tipH = tooltip.offsetHeight;
  const y = Math.min(Math.max(param.point.y - tipH / 2, 4), chartWrap.clientHeight - tipH - 4);
  tooltip.style.top = y + "px";
}

/* ---------- Live strategy overlay ---------- */
async function loadOverlay(seq = chartLoadSeq) {
  if (!state.indicators.overlay) {
    state.overlay = null;
    drawPriceLines();
    return;
  }
  try {
    const o = await api(`/api/live-overlay?symbol=${encodeURIComponent(state.symbol)}&range_bars=24`);
    if (seq !== chartLoadSeq) return;
    state.overlay = o || null;
    drawPriceLines();
  } catch (e) {
    if (seq !== chartLoadSeq) return;
    state.overlay = null;
  }
}

async function loadChart({ background = false } = {}) {
  if (background && chartLoading) return;
  chartLoading = true;
  const seq = ++chartLoadSeq;
  const symbol = state.symbol.trim() || "EURUSD";
  const key = `${symbol}|${state.timeframe}|${state.count}`;
  const hasCandles = chartDisplayedKey === key && !!state.lastBar;
  showChartStatus(hasCandles ? "Updating candles..." : `Loading ${symbol} ${state.timeframe} candles...`, "loading", hasCandles);
  try {
    const q = `symbol=${encodeURIComponent(symbol)}&timeframe=${state.timeframe}&count=${state.count}`;
    const d = await chartApi(`/api/rates?${q}`);
    if (seq !== chartLoadSeq) return;
    if (!d || !d.count || !Array.isArray(d.times) || !d.times.length) {
      throw new Error(`No candle data returned for ${symbol}`);
    }
    state.data = d;
    state.bot.data = null;
    state.signals = [];
    state.overlay = null;
    state.symbol = d.symbol;
    $("symbol-input").value = d.symbol;
    const t = d.tick;
    $("chart-tick").textContent = t ? `Bid ${fmtPrice(t.bid)}  Ask ${fmtPrice(t.ask)}  Spread ${t.spread}` : `${symbol} ${state.timeframe}`;
    updateBotBadge();
    savePrefs();
    renderWatchlist();
    setSeriesData(chartDisplayedKey !== key);
    chartDisplayedKey = key;
    hideChartStatus();
    loadPlan();
    startQuoteTimer();

    // Auto-connect WebSocket on page load (after a short delay to let MT5 init)
    setTimeout(() => {
      if (!ws) connectWs();
    }, 3000);

    // Fetch trading signals, bt overlay, and chart overlay in parallel
    const [sigResp, bot] = await Promise.all([
      api(`/api/signals?symbol=${encodeURIComponent(state.symbol)}&timeframe=${state.timeframe}&count=${state.count}`).catch(() => ({ signals: [] })),
      chartApi(`/api/bot-analysis?${q}`).catch(() => null),
      loadBtOverlay(symbol),
      loadOverlay(seq),
    ]);
    if (seq !== chartLoadSeq) return;
    state.signals = sigResp.signals || [];
    state.bot.data = bot;
    updateBotBadge();
    drawMarkers();
    drawPriceLines();
    drawBtSegments();
  } catch (e) {
    if (seq !== chartLoadSeq) return;
    const message = e && e.message ? e.message : String(e);
    if (!hasCandles) $("chart-tick").textContent = "No data for " + symbol;
    showChartStatus(hasCandles ? `Update failed. Showing last loaded candles. ${message}` : `${symbol} chart unavailable: ${message}`, "error", hasCandles);
  } finally {
    if (seq === chartLoadSeq) chartLoading = false;
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
    if (document.hidden || quotePending) return;
    const seq = chartLoadSeq;
    const symbol = state.symbol;
    quotePending = true;
    try {
      const t = await api("/api/quote?symbol=" + encodeURIComponent(symbol));
      if (seq !== chartLoadSeq || symbol !== state.symbol) return;
      if (!t || !Number.isFinite(t.bid) || !Number.isFinite(t.ask) || t.bid <= 0 || t.ask <= 0) return;
      $("chart-tick").textContent = "Bid " + fmtPrice(t.bid) + " Ask " + fmtPrice(t.ask) + " Spread " + t.spread;
      if (state.lastBar && candleSeries && state.data) {
        const close = state.symbol.indexOf("XAU") >= 0 || state.symbol.indexOf("XAG") >= 0 ? t.ask : t.bid;
        state.lastBar = {
          ...state.lastBar,
          high: Math.max(state.lastBar.high, close),
          low: Math.min(state.lastBar.low, close),
          close,
        };
        candleSeries.update(state.lastBar);
      }
    } catch (e) {
      /* Transient quote failures (MT5 reconnecting, symbol switch) must not
         wipe the last known tick - overwriting with "No data for ..." made a
         healthy chart look broken. The next successful poll refreshes it. */
    } finally { quotePending = false; }
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
  group.replaceChildren();
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
    if (!el) continue; // toggle not present in this page variant
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

  const countSelect2 = $("count-select");
  if (countSelect2) {
    countSelect2.addEventListener("change", (e) => {
      state.count = parseInt(e.target.value, 10);
      savePrefs();
      loadChart();
    });
  }

  const chkBt2 = $("chk-bt");
  if (chkBt2) {
    chkBt2.addEventListener("change", (e) => {
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
  }

  const eqBalance2 = $("eq-balance");
  if (eqBalance2) eqBalance2.addEventListener("change", (e) => toggleEquityOpt("balance", e.target));
  const eqFloating2 = $("eq-floating");
  if (eqFloating2) eqFloating2.addEventListener("change", (e) => toggleEquityOpt("floating", e.target));
  const eqMa52 = $("eq-ma5");
  if (eqMa52) eqMa52.addEventListener("change", (e) => toggleEquityOpt("ma5", e.target));
  const eqDd2 = $("eq-dd");
  if (eqDd2) eqDd2.addEventListener("change", (e) => toggleEquityOpt("dd", e.target));

  const wlInput2 = $("wl-add");
  if (wlInput2) wlInput2.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      addToWatchlist(wlInput2.value);
      wlInput2.value = "";
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
