const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const elements = new Map();
const element = () => ({innerHTML: '', textContent: '', disabled: false, listeners: {},
  classList: {toggle() {}}, addEventListener(name, handler) {this.listeners[name] = handler;}});
const now = Date.now();
const context = vm.createContext({
  state: {}, navigator: {onLine: true}, Date, Number, Math, Object,
  document: {getElementById(id) {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);},
    body: {classList: {toggle() {}}}},
  esc: value => String(value ?? '').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
  fmtMoney: value => Number(value).toFixed(2), setStatus() {},
});
vm.runInContext(fs.readFileSync(path.join(root, 'static/app.operations.js'), 'utf8'), context);
const ops = vm.runInContext('Operations', context);
ops.qualityAt = now;
ops.quality = {enabled:true,ml_role:'advisory',paper_min_trades:20,
  shadow:{counts:{closed:4,waiting:1},recent:[{symbol:'<img src=x>',side:'BUY',status:'observed',reasons:'["<script>bad</script>"]'}],
    requalification:[{symbol:'TEST',side:'SELL',setup:'desk:pattern',paper_trades:4,paper_expectancy_r:-0.2,paper_profit_factor:0.5}]}};
ops.renderQuality();
const qualityRoot = context.document.getElementById('quality-summary');
assert.ok(qualityRoot.innerHTML.includes('4 / 20'));
assert.ok(!qualityRoot.innerHTML.includes('<img') && !qualityRoot.innerHTML.includes('<script>'));
ops.qualityError = true;
ops.renderQuality();
assert.ok(qualityRoot.innerHTML.includes('unavailable'));
ops.qualityError = false;
const state = context.state;
Object.assign(state, {symbol:'BTCUSD', timeframe:'H1', accountAt:now, planAt:now,
  plan:{symbol:'BTCUSD', timeframe:'H1', action:'BUY', valid:true, reason:'<img src=x>'}});
ops.runtimeAt = ops.boardAt = now;
ops.board = {ttl_sec:1800, votes:{}};
ops.runtime = {risk:{reject_orders:true, blocked_symbols:[]},
  consensus:{enabled:true, allowed_symbols:['BTCUSD'], veto_score:75, min_agree:0},
  compliance:{mode:'normal',trading_allowed:true,trades_today:0,max_trades_per_day:3}};
const button = context.document.getElementById('plan-execute');
const trace = context.document.getElementById('decision-trace');
ops.renderDecision();
assert.equal(button.disabled, false, 'fresh valid preflight can proceed to server checks');
assert.ok(!trace.innerHTML.includes('<img'), 'untrusted plan reasons are escaped');
ops.board.votes.BTCUSD = [{source:'desk',side:'SELL',score:79,age_sec:0}];
ops.renderDecision();
assert.equal(button.disabled, true, 'fresh opposing veto disables execution');
ops.board.votes.BTCUSD[0].age_sec = 1900;
ops.renderDecision();
assert.equal(button.disabled, false, 'expired veto is ignored');
ops.runtime.consensus.min_agree = 1;
ops.renderDecision();
assert.equal(button.disabled, true, 'required agreement is enforced in preflight');
ops.runtime.consensus.enabled = false;
ops.renderDecision();
assert.equal(button.disabled, false, 'disabled consensus does not imply an active veto');
ops.runtime.compliance.trades_today = 3;
ops.renderDecision();
assert.equal(button.disabled, true, 'enforced daily limit disables execution');
ops.runtime.risk.reject_orders = false;
ops.renderDecision();
assert.equal(button.disabled, false, 'record-only compliance is not presented as enforcement');
assert.ok(trace.innerHTML.includes('Record only'));
ops.runtime.consensus.enabled = true;
ops.runtime.consensus.min_agree = 0;
ops.runtime.consensus.allowed_symbols = ['ETHUSD'];
ops.renderDecision();
assert.equal(button.disabled, true, 'record-only compliance never overrides consensus allowlist');
ops.runtime.consensus.allowed_symbols = ['BTCUSD'];
state.planAt = now - 91000;
ops.renderDecision();
assert.equal(button.disabled, true, 'expired plan cannot be executed');
state.planAt = now;
state.symbol = 'ETHUSD';
ops.renderDecision();
assert.equal(button.disabled, true, 'plan for previous symbol cannot be executed');
state.symbol = 'BTCUSD';
ops.runtimeError = true;
ops.renderDecision();
assert.equal(button.disabled, true, 'failed policy refresh invalidates last-known gate data');
ops.runtimeError = false;
ops.boardError = true;
ops.renderDecision();
assert.equal(button.disabled, true, 'unavailable consensus must not enable submission');
console.log('PASS: 12 decision/freshness/escaping cases');

ops.boardError = false;
ops.runtime.compliance.prop_risk = {enabled:true,available:false,state:'unavailable',
  entry_admission:'paused',baseline_status:'unverified',recovery_status:'waiting_for_flat',
  new_trade_budget:0,open_positions:2,pending_orders:0,
  reason:'<script>unverified</script>',recovery_action:'Wait until flat; refresh to verify history.',
  reset_utc:'2026-10-13T21:00:00+00:00',next_reset_utc:'2026-10-14T21:00:00+00:00'};
ops.renderDecision();
assert.equal(button.disabled, true, 'prop protection pauses entry even when compliance records only');
assert.ok(trace.innerHTML.includes('unverified') && !trace.innerHTML.includes('<script>'));
ops.renderRisk(ops.runtime.compliance);
const riskRoot = context.document.getElementById('risk-summary');
assert.ok(riskRoot.innerHTML.includes('Paused') && riskRoot.innerHTML.includes('Unverified'));
assert.ok(riskRoot.innerHTML.includes('2 / 0') && riskRoot.innerHTML.includes('Recheck baseline evidence'));
assert.ok(riskRoot.innerHTML.includes('never initializes from current equity'));
assert.ok(!riskRoot.innerHTML.includes('<script>'), 'broker error text is escaped');
let rechecks = 0;
const originalRefresh = ops.refresh;
ops.refresh = () => {rechecks++;};
context.document.getElementById('prop-risk-retry').listeners.click();
assert.equal(rechecks, 1, 'recovery action refreshes evidence without calling an execution action');
ops.refresh = originalRefresh;
ops.busy = true;
ops.renderRisk(ops.runtime.compliance);
assert.equal(context.document.getElementById('prop-risk-retry').disabled, true);
ops.busy = false;
ops.runtime.compliance.prop_risk.recovery_status = 'waiting_for_flat_reset';
ops.renderRisk(ops.runtime.compliance);
assert.ok(riskRoot.innerHTML.includes('Waiting for a flat reset'));
Object.assign(ops.runtime.compliance.prop_risk, {available:true,state:'ready',
  baseline_status:'verified',opening_baseline:10000,entry_admission:'eligible',new_trade_budget:390});
ops.renderRisk(ops.runtime.compliance);
assert.ok(riskRoot.innerHTML.includes('10000.00') && riskRoot.innerHTML.includes('Eligible'));
assert.ok(!riskRoot.innerHTML.includes('Recheck baseline evidence'));
ops.renderDecision();
assert.equal(button.disabled, false, 'verified positive prop budget leaves other gates in force');
ops.runtime.compliance.prop_risk.new_trade_budget = 0;
ops.renderDecision();
assert.equal(button.disabled, true, 'verified baseline cannot override exhausted loss buffer');
ops.runtimeAt = now - 50000;
ops.render();
assert.ok(riskRoot.innerHTML.includes('unavailable'), 'stale telemetry cannot show a verified baseline');
console.log('PASS: prop baseline pause, recovery, escaping, stale telemetry and loss-buffer cases');

// A second init invocation must not add listeners, sockets, charts or timers.
let setupCount = 0;
const bootContext = vm.createContext({window:{}, document:{hidden:false}, navigator:{},
  state:{watchlist:[]}, DEFAULT_WATCH:['BTCUSD'], POLL_MS:5000, CHART_MS:30000,
  SLOW_MS:60000, NEWS_MS:120000,
  Navigation:{init(){}}, Operations:{init(){}},
  $:()=>({dataset:{},addEventListener(){}}), setInterval(){}, setTimeout(){},
});
const agents = fs.readFileSync(path.join(root, 'static/app.agents.js'), 'utf8');
const initSource = agents.slice(agents.indexOf('function init() {'), agents.indexOf('/* ---------- Walk-Forward'));
for (const name of new Set([...initSource.matchAll(/\b(\w+)\(/g)].map(match=>match[1]))) {
  if (!(name in bootContext) && name !== 'init') bootContext[name] = ()=>{};
}
bootContext.setupCharts = ()=>{setupCount++;};
bootContext.loadPlan = ()=>{};
vm.runInContext(initSource + '\ninit(); init();', bootContext);
assert.equal(setupCount, 1, 'bootstrap runs once even if invoked twice');
console.log('PASS: bootstrap initializes charts once');

const chartSource = fs.readFileSync(path.join(root, 'static/app.chart.js'), 'utf8');
const chartContext = vm.createContext({
  state: {candleTimes: new Set([10, 20])},
  candleSeries: {setMarkers(markers) {
    assert.equal(JSON.stringify(markers.map(marker => marker.time)), '[10,20]');
  }},
});
vm.runInContext(chartSource.slice(chartSource.indexOf('function setMarkersCommon('), chartSource.indexOf('/* Bot-analysis')), chartContext);
vm.runInContext('setMarkersCommon([{time:20},{time:5},{time:10}]);', chartContext);
assert.ok(!chartSource.includes('shape: "triangleDown"') && !chartSource.includes('shape: "triangleUp"'));
console.log('PASS: chart markers use supported shapes and ordered candle timestamps');
