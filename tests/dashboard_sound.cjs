const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.sound.js'), 'utf8');
let notes = 0;
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id,{textContent:'', handlers:{},setAttribute(){},addEventListener(name,fn){this.handlers[name]=fn;}});
  return elements.get(id);
};
const stored = new Map();
class Audio {
  constructor(){this.state='suspended';this.currentTime=0;this.destination={};}
  async resume(){this.state='running';}
  createOscillator(){return {frequency:{setValueAtTime(){}},connect(){},disconnect(){},start(){notes++;},stop(){}};}
  createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}
}
const context = vm.createContext({window:{AudioContext:Audio},Date,Number,Set,Map,
  localStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)},
  document:{readyState:'complete',getElementById:element,addEventListener(){}},
});
vm.runInContext(source+'\nglobalThis.sound=TradeSound;',context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  assert.equal(element('trade-sound-toggle').textContent,'Enable sound');
  await element('trade-sound-test').handlers.click();
  assert.equal(notes,3,'test chime has three notes');
  assert.equal(element('trade-sound-toggle').textContent,'Sound on');
  const event={id:1,kind:'fill',ts:new Date().toISOString()};
  context.sound.onEvent(event); await settle(); assert.equal(notes,6);
  context.sound.onEvent(event); await settle(); assert.equal(notes,6,'duplicate fill stays silent');
  context.sound.onEvent({...event,id:2,kind:'filled'}); await settle(); assert.equal(notes,9);
  context.sound.onEvent({...event,id:3,kind:'order'}); await settle(); assert.equal(notes,9,'pending order stays silent');
  context.sound.onEvent({...event,id:4,ts:'2020-01-01T00:00:00Z'}); await settle(); assert.equal(notes,9,'history stays silent');
  await element('trade-sound-toggle').handlers.click();
  assert.equal(stored.get('mt5dash.tradeSound'),'off');
  context.sound.onEvent({...event,id:5}); await settle(); assert.equal(notes,9,'muted fills stay silent');
  await element('trade-sound-test').handlers.click(); assert.equal(notes,12);
  assert.equal(stored.get('mt5dash.tradeSound'),'on');
  context.sound.observePositions([{ticket:1,symbol:'EURUSD'}],123); await settle(); assert.equal(notes,12,'initial positions stay silent');
  context.sound.observePositions([{ticket:1,symbol:'EURUSD'},{ticket:2,symbol:'EURUSD'}],123); await settle(); assert.equal(notes,15,'new desk position sounds');
  context.sound.onEvent({...event,id:6,symbol:'EURUSD'}); await settle(); assert.equal(notes,15,'event and position do not double chime');
  context.sound.observePositions([{ticket:1,symbol:'EURUSD'},{ticket:2,symbol:'EURUSD'}],123); await settle(); assert.equal(notes,15,'same snapshot stays silent');
  context.sound.observePositions([{ticket:3,symbol:'EURUSD'}],456); await settle(); assert.equal(notes,15,'account switch stays silent');
  const blocked = vm.createContext({window:{}, Date, Number, Set,Map,localStorage:{getItem(){throw Error('blocked');}},document:{readyState:'complete',getElementById:element,addEventListener(){}}});
  vm.runInContext(source+'\nglobalThis.sound=TradeSound;',blocked);
  assert.equal(await blocked.sound.play(),false,'unsupported browser fails safely');
  console.log('PASS: sound activation, chime, fill aliases, duplicate/history/order filtering, mute persistence, test and unsupported browser');
})().catch(error=>{console.error(error);process.exitCode=1;});
