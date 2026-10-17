const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.panels.js'), 'utf8');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', value:'', disabled:false, addEventListener(){}});
  return elements.get(id);
};
let response, calls = [];
const context = vm.createContext({
  state:{}, Date, Number, Map,
  $:element,
  esc:s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;'),
  api:async (url, options) => {calls.push({url, options}); if(response instanceof Error) throw response; return response;},
  toast(){}, errorReport:{capture(){}},
});
vm.runInContext(source.slice(source.indexOf('async function loadAlerts()'), source.indexOf('/* ---------- News ---------- */')), context);
(async () => {
  response = {events:[{id:1,kind:'filled',symbol:'EURUSD',message:'<script>bad</script>',ts:'2026-10-12T12:00:00Z'}, {id:2,kind:'closed',message:'newest'}]};
  await context.loadAlertEvents();
  assert.equal(calls.at(-1).url, '/api/events?since=0&limit=100');
  assert.equal(context.state.alertEvents[0].id, 2);
  assert.ok(element('alerts-events-list').innerHTML.includes('&lt;script&gt;'));
  assert.ok(!element('alerts-events-list').innerHTML.includes('<script>'));
  const history = element('alerts-events-list').innerHTML;
  response = new Error('offline');
  await context.loadAlertEvents();
  assert.equal(element('alerts-events-list').innerHTML, history);
  assert.match(element('alerts-events-status').textContent, /offline.*Refresh/);
  await context.loadAlerts();
  assert.match(element('alerts-full-list').innerHTML, /Unable to load/);
  assert.match(element('alerts-price-status').textContent, /offline/);
  response = {alerts:[{id:'test',symbol:'EURUSD',price:'1.1',current_price:'1.2',active:true,triggered:true,triggered_at:'today',name:'<b>test</b>'}]};
  await context.loadAlerts();
  assert.match(element('alerts-full-list').innerHTML, /Triggered/);
  assert.match(element('alerts-full-list').innerHTML, /&lt;b&gt;/);
  const prices = element('alerts-full-list').innerHTML;
  response = new Error('timeout');
  await context.loadAlerts();
  assert.equal(element('alerts-full-list').innerHTML, prices);
  response = {alerts:[]};
  await context.loadAlerts();
  assert.match(element('alerts-full-list').innerHTML, /No alerts configured/);
  element('alert-symbol').value='EURUSD';
  element('alert-price').value='-1';
  element('alert-condition').value='above';
  const before=calls.length;
  await context.addAlert();
  assert.equal(calls.length,before,'invalid price never submitted');
  assert.ok(fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8').includes('id="alert-form"'));
  console.log('PASS: alerts history, ordering, escaping, failure/retry, retained data, triggered state and input validation');
})().catch(error=>{console.error(error);process.exitCode=1;});
