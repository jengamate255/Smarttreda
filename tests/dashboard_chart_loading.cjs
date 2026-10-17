const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/app.chart.js'), 'utf8');
const core = fs.readFileSync(path.join(root, 'static/app.core.js'), 'utf8');
const button = {};
const lookup = vm.createContext({ document: { getElementById: id => id === 'bt-run' ? button : null } });
vm.runInContext(core.slice(core.indexOf('const $ ='), core.indexOf('/* ---------- Local prefs')), lookup);
assert.equal(vm.runInContext('$("#bt-run")', lookup), button);
assert.equal(vm.runInContext('$("bt-run")', lookup), button);

const sync = vm.createContext({ state: { timeIndex: new Map([[10, 0]]) }, Number });
vm.runInContext(source.slice(source.indexOf('function syncChartCrosshair'), source.indexOf('function setupCharts')), sync);
let position;
const chart = {setCrosshairPosition(...args) {position = args;}, clearCrosshairPosition() {position = null;},
  timeScale: () => ({timeToCoordinate: () => 1})};
const series = {priceToCoordinate: () => 1};
sync.syncChartCrosshair(chart, series, [42], {time: 10, point: {x: 1, y: 2}});
assert.deepEqual(position, [42, 10, series]);
sync.syncChartCrosshair(chart, series, [null], {time: 10, point: {x: 1, y: 2}});
assert.equal(position, null);
series.priceToCoordinate = () => null;
sync.syncChartCrosshair(chart, series, [42], {time: 10, point: {x: 1, y: 2}});
assert.equal(position, null, 'empty or hidden panes cannot receive a crosshair');

(async () => {
  let renderCount = 0;
  let statusHidden = false;
  let finishAnalysis;
  const analysis = new Promise(resolve => {finishAnalysis = resolve;});
  const context = vm.createContext({
    chartLoadSeq: 0, chartDisplayedKey: '', chartLoading: false,
    state: {symbol: 'EURUSD', timeframe: 'H1', count: 1, bot: {}},
    chartApi: async url => url.includes('/api/rates') ? {symbol: 'EURUSD', count: 1, times: [10]} : analysis,
    api: async () => ({signals: []}),
    $: () => ({}), showChartStatus() {}, hideChartStatus() {statusHidden = true;},
    updateBotBadge() {}, savePrefs() {}, renderWatchlist() {},
    setSeriesData() {renderCount++;}, loadPlan() {}, startQuoteTimer() {},
    loadBtOverlay() {}, loadOverlay() {}, drawMarkers() {}, drawPriceLines() {}, drawBtSegments() {},
    setTimeout() {}, encodeURIComponent, Promise,
  });
  vm.runInContext(source.slice(source.indexOf('async function loadChart('), source.indexOf('async function updatePriceDisplay')), context);
  const pending = context.loadChart();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(renderCount, 1, 'candles render while analysis is still pending');
  assert.equal(statusHidden, true, 'optional analysis does not cover loaded candles');
  await context.loadChart({background: true});
  assert.equal(renderCount, 1, 'background refresh cannot overlap pending analysis');
  context.chartLoadSeq++;
  finishAnalysis({symbol: 'OLD'});
  await pending;
  assert.equal(context.state.bot.data, null, 'superseded analysis cannot overwrite the new chart');
  console.log('PASS: ID lookup, crosshair, immediate candles and stale analysis');
})().catch(error => {console.error(error); process.exitCode = 1;});
