"use strict";

const TradeSound = (() => {
  const key = "mt5dash.tradeSound";
  const startedAt = Date.now();
  const seen = new Set();
  const sounded = new Map();
  let positionTickets = null, positionAccount = null;
  let enabled = true, context = null, initialized = false;
  try { enabled = localStorage.getItem(key) !== "off"; } catch (_) {}

  function render() {
    const button = document.getElementById("trade-sound-toggle");
    const status = document.getElementById("trade-sound-status");
    const supported = !!(window.AudioContext || window.webkitAudioContext);
    if (button) {
      button.textContent = !supported ? "Sound unavailable" : !enabled ? "Sound off" : context?.state === "running" ? "Sound on" : "Enable sound";
      button.setAttribute("aria-pressed", String(enabled));
    }
    if (status) status.textContent = !supported ? "This browser cannot play trade sounds." : !enabled ? "Trade sounds muted" : context?.state === "running" ? "Ready for new trade fills" : "Click Enable sound to allow trade sounds";
  }

  async function unlock() {
    if (!enabled) return false;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) { render(); return false; }
      if (!context || context.state === "closed") {
        context = new Audio();
        context.onstatechange = render;
      }
      if (context.state !== "running") await context.resume();
      render();
      return context.state === "running";
    } catch (_) { render(); return false; }
  }

  async function play() {
    if (!await unlock()) return false;
    try {
      // Distinct three-note chime, generated locally without downloads.
      const start = context.currentTime;
      [659.25, 830.61, 987.77].forEach((frequency, index) => {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        const at = start + index * 0.18;
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(frequency, at);
        gain.gain.setValueAtTime(0, at);
        gain.gain.linearRampToValueAtTime(0.24, at + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.001, at + 0.28);
        oscillator.connect(gain);
        gain.connect(context.destination);
        oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
        oscillator.start(at);
        oscillator.stop(at + 0.3);
      });
      return true;
    } catch (_) { render(); return false; }
  }

  function onEvent(event) {
    if (!event || !["fill", "filled"].includes(String(event.kind).toLowerCase())) return;
    const time = Date.parse(event.ts);
    if (!Number.isFinite(time) || time < startedAt || time > Date.now() + 60000 || event.id == null) return;
    const id = String(event.id);
    if (seen.has(id)) return;
    seen.add(id);
    if (seen.size > 500) seen.delete(seen.values().next().value);
    confirmFill(event.symbol, "event");
  }

  function confirmFill(symbol, source = "manual") {
    const key = String(symbol || "").toUpperCase();
    const previous = sounded.get(key);
    // A fill can arrive through both positions and the event log.
    if (previous && previous.source !== source && Date.now() - previous.time < 30000) return;
    sounded.set(key, {source, time:Date.now()});
    if (sounded.size > 500) sounded.delete(sounded.keys().next().value);
    void play();
  }

  function observePositions(positions, account) {
    if (!Array.isArray(positions)) return;
    const tickets = new Set(positions.filter(position => position.ticket != null).map(position => String(position.ticket)));
    if (positionTickets === null || positionAccount !== account) {
      positionTickets = tickets;
      positionAccount = account;
      return; // Existing positions on page load/account switch are history.
    }
    positions.forEach(position => {
      if (position.ticket != null && !positionTickets.has(String(position.ticket))) confirmFill(position.symbol, "position");
    });
    tickets.forEach(ticket => positionTickets.add(ticket));
  }

  function init() {
    if (initialized) return;
    initialized = true;
    document.getElementById("trade-sound-toggle")?.addEventListener("click", async () => {
      enabled = !enabled || context?.state !== "running";
      try { localStorage.setItem(key, enabled ? "on" : "off"); } catch (_) {}
      if (enabled) await play();
      render();
    });
    document.getElementById("trade-sound-test")?.addEventListener("click", async () => {
      enabled = true;
      try { localStorage.setItem(key, "on"); } catch (_) {}
      await play();
      render();
    });
    // Audio activation must run inside a real user interaction.
    const activate = event => {
      if (!event.target?.closest?.(".trade-sound-controls")) void unlock();
    };
    document.addEventListener("pointerdown", activate, {passive:true});
    document.addEventListener("keydown", activate);
    render();
  }
  return {init, play, onEvent, confirmFill, observePositions};
})();

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", TradeSound.init);
else TradeSound.init();
