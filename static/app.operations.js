"use strict";

const Operations = {
  runtime: null,
  board: null,
  quality: null,
  runtimeAt: 0,
  boardAt: 0,
  qualityAt: 0,
  busy: false,
  fresh(at, ttl = 45000) { return at > 0 && Date.now() - at < ttl; },
  age(seconds) {
    if (!Number.isFinite(seconds)) return "no vote";
    return seconds < 60 ? `${Math.floor(seconds)}s` : `${Math.floor(seconds / 60)}m`;
  },
  init() {
    document.getElementById("ops-refresh").addEventListener("click", () => {
      this.refresh(); loadSummary(); loadPlan();
    });
    window.addEventListener("dashboard-account", () => this.render());
    window.addEventListener("offline", () => { setStatus(false, "Offline"); this.render(); });
    window.addEventListener("online", () => { this.refresh(); loadSummary(); });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { this.refresh(); loadSummary(); }
    });
    this.refresh();
    setInterval(() => { if (!document.hidden) this.refresh(); }, 30000);
    setInterval(() => { if (!document.hidden) this.render(); }, 5000);
  },
  async refresh() {
    if (this.busy) return;
    this.busy = true;
    document.getElementById("ops-refresh").disabled = true;
    const read = async (path, key) => {
      try {
        const data = await api(path);
        if (!data || data.error) throw new Error(data?.error || "Empty response");
        this[key] = data;
        this[key + "At"] = Date.now();
        this[key + "Error"] = false;
      } catch (_) { this[key + "Error"] = true; }
      this.render();
    };
    try {
      await Promise.allSettled([read("/api/diagnostic/runtime", "runtime"), read("/api/diagnostic/consensus", "board"), read("/api/diagnostic/quality", "quality")]);
    } finally {
      this.busy = false;
      document.getElementById("ops-refresh").disabled = false;
      this.render();
    }
  },
  render() {
    const live = this.fresh(state.accountAt, 20000) && navigator.onLine;
    document.body.classList.toggle("data-stale", !live);
    document.getElementById("ops-freshness").textContent = state.accountAt
      ? `${live ? "Account updated" : "Account stale · last update"} ${this.age((Date.now() - state.accountAt) / 1000)} ago`
      : "Waiting for live account data";
    if (!live && state.accountAt) setStatus(false, navigator.onLine ? "Stale data" : "Offline");
    const r = this.runtime;
    const policyFresh = this.fresh(this.runtimeAt) && !this.runtimeError;
    const boardFresh = this.fresh(this.boardAt) && !this.boardError;
    const comp = policyFresh && !r?.compliance?.error ? r.compliance : null;
    const consensus = policyFresh ? r.consensus : null;
    const configured = consensus?.allowed_symbols || [];
    const allowed = consensus?.enabled === false ? "Disabled" : (configured.length ? `${configured.length} symbols` : "Unrestricted");
    const item = (label, value, detail = "") => `<div class="ops-item"><span class="ops-label">${esc(label)}</span><div class="ops-value">${value}${detail ? `<small>${esc(detail)}</small>` : ""}</div></div>`;
    const account = state.account ? `${esc(state.account.login)} · ${esc(state.account.currency || "")}` : "—";
    const riskCap = comp?.per_trade_risk_cap_percent;
    const cap = Number.isFinite(riskCap) ? `${riskCap}%` : "—";
    const votes = this.board?.votes || {};
    const engines = ["desk", "confirm", "breakout", "analysis"].map(source => {
      const entries = Object.values(votes).flat().filter(v => v.source === source);
      const youngest = entries.length ? Math.min(...entries.map(v => v.age_sec)) + (Date.now() - this.boardAt) / 1000 : Infinity;
      const fresh = boardFresh && youngest <= (this.board?.ttl_sec ?? 1800);
      const label = !boardFresh ? "unavailable" : (youngest === Infinity ? "no vote" : `${fresh ? "" : "stale "}${this.age(youngest)}`);
      return `<span class="engine-vote ${fresh ? "fresh" : ""}" title="Latest consensus publication; this does not prove the process is running">${source} ${esc(label)}</span>`;
    }).join("");
    document.getElementById("ops-status").innerHTML =
      item("Account", account, live ? "Current account snapshot" : "Awaiting fresh snapshot") +
      item("Desk configuration", policyFresh ? esc(r.desk.mode) : "—", policyFresh ? (r.desk.enabled ? "Enabled in environment" : "Disabled in environment") : "Telemetry unavailable") +
      item("Compliance risk cap", cap, comp ? `${comp.mode} · ${r.risk.reject_orders === false ? "record only" : "enforced"}` : "Telemetry unavailable") +
      item("Consensus allowlist", consensus ? esc(allowed) : "—", consensus?.enabled !== false ? configured.join(" · ") : "Consensus checks disabled") +
      item("Latest engine votes", `<div class="engine-votes">${engines}</div>`);
    const attention = [];
    if (!policyFresh) attention.push("Runtime policy unavailable. Refresh to retry; last-known gate data is not current.");
    if (!boardFresh) attention.push("Consensus telemetry unavailable. Vote status cannot be verified.");
    if (policyFresh && r.risk.reject_orders === false) attention.push("Compliance is recording violations without rejecting orders. Consensus restrictions still apply.");
    if (policyFresh && r.settings_drift?.length) attention.push(`Settings differ from the dashboard's cached policy: ${r.settings_drift.join(", ")}. Executors may retain their own cached policy.`);
    if (comp?.halt_reason) attention.push(`Compliance: ${comp.halt_reason}`);
    if (comp?.news_blocked) attention.push(comp.news_blocked_reason || "News blackout active.");
    else if (comp?.news_stale) attention.push("Economic calendar is stale.");
    if (policyFresh && r.compliance?.error) attention.push("Compliance unavailable; remaining risk budget cannot be verified.");
    document.getElementById("ops-attention").innerHTML = attention.map(message => `<p>${esc(message)}</p>`).join("");
    this.renderRisk(comp);
    this.renderDecision();
    this.renderQuality();
  },
  renderQuality() {
    const root = document.getElementById("quality-summary");
    if (!root) return;
    const q = this.quality;
    const mode = document.getElementById("quality-mode");
    if (!q || !this.fresh(this.qualityAt) || this.qualityError) {
      if (mode) mode.textContent = "Policy unavailable";
      root.innerHTML = '<p class="ops-caption">Quality telemetry unavailable.</p>'; return;
    }
    if (mode) mode.textContent = q.enabled ? "Enforced" : "Off";
    const shadow = q.shadow || {};
    const counts = shadow.counts || {};
    const row = (label, value) => `<div class="budget-row"><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
    const paperOnly = this.fresh(this.runtimeAt) && !this.runtimeError &&
      this.runtime?.risk?.blocked_setup_prefixes?.some(prefix => prefix === "desk:" || prefix === "desk-");
    let html = row("Quality mode", q.enabled ? "Enforced" : "Off") + row("ML role", q.ml_role || "Unavailable");
    if (q.enabled) {
      html += row("Desk entries", paperOnly ? "Paper only" : "Final gates apply") +
        row("Paper outcomes", counts.closed || 0) + row("Paper awaiting / open", `${counts.waiting || 0} / ${counts.open || 0}`);
      const feed = shadow.feed;
      html += row("Paper feed", feed?.running ? "Updating" : feed?.last_completed_at ? new Date(feed.last_completed_at).toLocaleTimeString() : "Awaiting first update");
      if (feed?.last_error) html += `<p class="quality-warning">${esc(feed.last_error)}</p>`;
      for (const cohort of shadow.requalification || []) {
        html += `<div class="quality-cohort"><strong>${esc(cohort.symbol)} ${esc(cohort.side)}</strong><span>${esc(cohort.setup)}</span>` +
          row("Recovery sample", `${cohort.paper_trades} / ${q.paper_min_trades}`) +
          row("Net expectancy", `${cohort.paper_expectancy_r} R`) +
          row("Profit factor", cohort.paper_profit_factor ?? (cohort.paper_trades ? "No losses recorded" : "Awaiting outcomes")) + "</div>";
      }
      const recent = shadow.recent || [];
      if (recent.length) html += '<div class="quality-recent">' + recent.slice(0, 4).map(candidate => {
        let reasons;
        try { reasons = JSON.parse(candidate.reasons || "[]"); } catch (_) { reasons = ["Reason unavailable"]; }
        return `<div class="quality-candidate"><strong>${esc(candidate.symbol)} ${esc(candidate.side)} <small>${esc(candidate.status)}</small></strong><span>${esc(reasons.join("; ") || "Quality checks passed")}</span></div>`;
      }).join("") + '</div>';
    }
    root.innerHTML = html;
  },
  renderRisk(comp) {
    const root = document.getElementById("risk-summary");
    if (!comp) { root.innerHTML = '<p class="ops-caption">Risk data unavailable. Refresh to retry.</p>'; return; }
    const money = value => Number.isFinite(value) ? esc(fmtMoney(value)) : "—";
    const row = (label, value) => `<div class="budget-row"><span>${label}</span><b>${value}</b></div>`;
    const prop = comp.prop_risk?.enabled ? comp.prop_risk : null;
    const used = Number.isFinite(comp.daily_limit) && comp.daily_limit > 0 && Number.isFinite(comp.daily_pnl)
      ? Math.min(100, Math.max(0, -comp.daily_pnl / comp.daily_limit * 100)) : 0;
    root.innerHTML = row(prop ? "Daily risk available" : "Daily loss buffer", money(prop ? prop.remaining_daily_risk : comp.remaining_daily_buffer)) +
      `<div class="budget-bar" role="meter" aria-label="Daily loss budget used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${used}"><div style="width:${used}%"></div></div>` +
      row("Open risk / limit", `${money(comp.open_risk)} / ${money(comp.max_open_risk)}`) +
      row(prop ? "Overall risk available" : "Drawdown buffer", money(prop ? prop.remaining_overall_risk : comp.remaining_drawdown_buffer)) +
      row("Trades today / limit", `${esc(comp.trades_today ?? "—")} / ${esc(comp.max_trades_per_day ?? "—")}`);
    if (comp.performance_risk?.enabled) {
      const p = comp.performance_risk;
      root.innerHTML += row("Outcome risk state", esc(p.state)) +
        row("Target risk / trade", `${esc(p.risk_percent)}%`) +
        row("Completed sample", `${esc(p.trades)} / ${esc(p.minimum)}`);
    }
    if (prop) {
      root.innerHTML += row("Prop entry admission", esc(prop.entry_admission === "eligible" ? "Eligible · final gates apply" : "Paused")) +
        row("Opening baseline", prop.baseline_status === "verified" ? money(prop.opening_baseline) : "Unverified") +
        row("Prop protection", esc(prop.state)) +
        row("Effective risk ceiling", `${esc(comp.per_trade_risk_cap_percent ?? 0)}%`);
      if (prop.reset_utc) root.innerHTML += row("Risk reset (UTC)", esc(prop.reset_utc));
      if (prop.available) {
        root.innerHTML += row("Reserved stop risk", money(prop.reserved_stop_risk)) +
          row("Daily / overall floor", `${money(prop.daily_floor)} / ${money(prop.overall_floor)}`);
      } else {
        root.innerHTML += `<p class="quality-warning" role="status">${esc(prop.reason || "Account loss baseline unavailable")}</p>`;
        if (Number.isInteger(prop.open_positions)) root.innerHTML += row("Positions / pending entries", `${prop.open_positions} / ${esc(prop.pending_orders ?? "—")}`);
        const recoveryLabels = {waiting_for_flat:"Waiting until flat", waiting_for_flat_reset:"Waiting for a flat reset",
          history_unverified:"Broker history not verified", evidence_unavailable:"Account evidence unavailable"};
        root.innerHTML += row("Recovery status", esc(recoveryLabels[prop.recovery_status] || "Verification required"));
        root.innerHTML += `<p class="ops-caption">${esc(prop.recovery_action || "Refresh to retry verified account evidence.")}</p>` +
          '<p class="ops-caption">Recovery never initializes from current equity. Exits and protective stop tightening remain available.</p>';
        if (prop.next_reset_utc) root.innerHTML += row("Next risk reset (UTC)", esc(prop.next_reset_utc));
        root.innerHTML += '<button type="button" id="prop-risk-retry" class="btn-secondary">Recheck baseline evidence</button>';
        document.getElementById("prop-risk-retry").disabled = this.busy || !navigator.onLine;
        document.getElementById("prop-risk-retry").addEventListener("click", () => this.refresh());
      }
    }
  },
  renderDecision() {
    const root = document.getElementById("decision-trace");
    if (!root) return;
    const plan = state.plan;
    const current = plan && plan.symbol === state.symbol && plan.timeframe === state.timeframe && this.fresh(state.planAt, 90000);
    const step = (label, message, status = "") => `<div class="decision-step ${status}"><strong>${label}</strong><span>${esc(message)}</span></div>`;
    let html = step("Signal", current ? `${plan.symbol} · ${plan.action} · ${plan.timeframe}` : `${state.symbol} · awaiting current plan`);
    html += step("Strategy", current ? (plan.reason || plan.errors?.join("; ") || "No explanation supplied") : "No current strategy result");
    let consensusMessage = "Unavailable; refresh required", consensusStatus = "";
    if (current && this.fresh(this.runtimeAt) && !this.runtimeError && this.fresh(this.boardAt) && !this.boardError) {
      const cfg = this.runtime.consensus;
      const entries = (this.board.votes?.[plan.symbol] || []).filter(v => v.age_sec + (Date.now() - this.boardAt) / 1000 <= (this.board.ttl_sec ?? 1800));
      const allowed = cfg.allowed_symbols || [];
      const opposite = plan.action === "BUY" ? "SELL" : "BUY";
      const veto = entries.find(v => v.side === opposite && v.score >= cfg.veto_score);
      const agreement = entries.filter(v => v.side === plan.action && v.score >= (cfg.agree_min_score ?? 60)).length;
      if (!cfg.enabled) consensusMessage = "Disabled in configuration";
      else if (allowed.length && !allowed.includes(plan.symbol)) { consensusMessage = "Outside the trading allowlist"; consensusStatus = "blocked"; }
      else if (plan.action === "NONE") consensusMessage = "No entry side to check";
      else if (veto) { consensusMessage = `${veto.source} ${veto.side} ${veto.score} vetoes this side`; consensusStatus = "blocked"; }
      else if (agreement < (cfg.min_agree ?? 0)) { consensusMessage = `Agreement ${agreement}/${cfg.min_agree} required`; consensusStatus = "blocked"; }
      else { consensusMessage = "Allowlisted · no fresh opposing veto"; consensusStatus = "pass"; }
    }
    html += step("Consensus", consensusMessage, consensusStatus);
    const runtimeCurrent = this.fresh(this.runtimeAt) && !this.runtimeError;
    const comp = runtimeCurrent && !this.runtime?.compliance?.error ? this.runtime.compliance : null;
    const recorded = runtimeCurrent && this.runtime.risk.reject_orders === false;
    const restrictions = [];
    if (comp?.trading_allowed === false) restrictions.push(comp.halt_reason || "Trading halted");
    const propPaused = comp?.prop_risk?.enabled && (!comp.prop_risk.available || comp.prop_risk.new_trade_budget <= 0);
    if (propPaused) restrictions.push(comp.prop_risk.reason || "Prop entry admission paused");
    if (comp?.news_blocked) restrictions.push(comp.news_blocked_reason || "News blackout");
    if (comp && comp.trades_today >= comp.max_trades_per_day) restrictions.push("Daily trade limit reached");
    if (runtimeCurrent && this.runtime.risk.blocked_symbols?.includes(state.symbol)) restrictions.push("Symbol blocked by compliance policy");
    html += step("Compliance", comp ? `${recorded ? "Record only" : comp.mode}${restrictions.length ? " · " + restrictions.join("; ") : " · full order checks run at execution"}` : "Telemetry unavailable", restrictions.length ? "blocked" : "");
    html += step("Execution", !current ? "Waiting for a current plan" : !plan.valid || plan.action === "NONE" ? "No valid executable setup" : "Not submitted · server rechecks gates before any fill");
    html += '<p class="ops-caption">Preflight snapshot. Final checks include size, stops, exposure and current prices.</p>';
    root.innerHTML = html;
    const button = document.getElementById("plan-execute");
    if (button) {
      button.disabled = !current || !plan.valid || consensusStatus === "blocked" || !comp ||
        !this.fresh(this.boardAt) || this.boardError || !navigator.onLine ||
        !this.fresh(state.accountAt, 20000) || propPaused || (!recorded && restrictions.length > 0);
    }
  },
};
