"""FastAPI dashboard routes for the Trading AI platform."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from trading_ai.config.settings import settings
from trading_ai.execution.paper import PaperBroker, Position, PositionSide

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# Global references (injected at startup)
paper_broker: PaperBroker | None = None


def set_paper_broker(broker: PaperBroker) -> None:
    global paper_broker
    paper_broker = broker


# ======================================================================
# SHARED TEMPLATE & NAVIGATION
# ======================================================================

def _nav_bar(active: str) -> str:
    pages = [
        ("approvals", "🔐 Approvals", "/dashboard/approvals"),
        ("trading", "📈 Trading", "/dashboard/trading"),
        ("performance", "📊 Performance", "/dashboard/performance"),
        ("research", "🔬 Research", "/dashboard/research"),
        ("system", "⚙️ System", "/dashboard/system"),
        ("strategy", "🧬 Strategy Lab", "/dashboard/strategy"),
    ]
    links = []
    for key, label, href in pages:
        cls = "nav-link active" if key == active else "nav-link"
        links.append(f'<a href="{href}" class="{cls}">{label}</a>')
    return f"""
<nav class="nav-bar">
    <div class="nav-brand">Trading AI</div>
    <div class="nav-links">{"".join(links)}</div>
</nav>
"""


# ======================================================================
# APPROVALS DASHBOARD (existing)
# ======================================================================

@router.get("/approvals", response_class=HTMLResponse)
async def approvals_dashboard() -> str:
    return _base_html(
        title="Approvals",
        content="Recursive Self-Improvement — Human-in-the-Loop Review Queue",
        active="approvals",
        body="""
        <div class="summary-bar" id="summary">
            <div class="summary-item summary-critical"><div class="summary-value" id="count-live">—</div><div class="summary-label">Need Live Approval</div></div>
            <div class="summary-item summary-warning"><div class="summary-value" id="count-paper">—</div><div class="summary-label">In Paper Trading</div></div>
            <div class="summary-item summary-info"><div class="summary-value" id="count-promoted">—</div><div class="summary-label">Recently Promoted</div></div>
            <div class="summary-item summary-success"><div class="summary-value" id="count-phase">—</div><div class="summary-label">Current Phase</div></div>
        </div>
        <div class="grid">
            <section class="card critical"><h2>🚨 Need Live Approval (<span id="live-count">0</span>)</h2><div id="live-pending-list"></div></section>
            <section class="card warning"><h2>📄 Paper Trading / Awaiting Confirmation (<span id="paper-count">0</span>)</h2><div id="paper-list"></div></section>
            <section class="card info"><h2>🚀 Recently Promoted to Paper (<span id="promoted-count">0</span>)</h2><div id="promoted-list"></div></section>
            <section class="card"><h2>⚠️ Safety Alerts (<span id="safety-count">0</span>)</h2><div id="safety-list"></div></section>
        </div>
        <div class="last-updated" id="last-updated">Last updated: —</div>
        <button class="refresh-btn" onclick="loadData()" title="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path></svg></button>
        <script>
            const API_BASE = '/evolution/self-learning';
            async function loadData() {
                document.body.classList.add('loading');
                try { const res = await fetch(`${API_BASE}/pending-approvals`); const data = await res.json(); render(data); }
                catch (e) { console.error(e); } finally { document.body.classList.remove('loading'); }
            }
            function render(data) {
                document.getElementById('count-live').textContent = data.summary.need_live_approval;
                document.getElementById('count-paper').textContent = data.summary.in_paper_trading;
                document.getElementById('count-promoted').textContent = Object.keys(data.recently_promoted_to_paper).length;
                document.getElementById('count-phase').textContent = data.summary.current_phase.toUpperCase();
                document.getElementById('live-count').textContent = data.summary.need_live_approval;
                document.getElementById('paper-count').textContent = data.summary.in_paper_trading;
                document.getElementById('promoted-count').textContent = Object.keys(data.recently_promoted_to_paper).length;
                document.getElementById('safety-count').textContent = data.safety_alerts.length;
                document.getElementById('last-updated').textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
                renderStrategyList('live-pending-list', data.live_pending, 'live-pending', true);
                renderStrategyList('paper-list', data.paper_awaiting_confirmation, 'paper', false);
                renderStrategyList('promoted-list', data.recently_promoted_to_paper, 'promoted', false);
                renderSafetyAlerts(data.safety_alerts);
            }
            function renderStrategyList(cid, records, type, showActions) {
                const c = document.getElementById(cid), entries = Object.entries(records);
                if (!entries.length) { c.innerHTML = `<div class="empty-state"><p>No ${type === 'live-pending' ? 'strategies awaiting live approval' : type === 'paper' ? 'strategies in paper trading' : 'recent promotions'}</p></div>`; return; }
                c.innerHTML = entries.map(([sid, info]) => `
                    <div class="strategy-item ${type}">
                        <div class="strategy-header"><span class="strategy-id">${sid}</span><span class="badge ${type === 'live-pending' ? 'badge-critical' : type === 'paper' ? 'badge-warning' : 'badge-info'}">${info.stage.toUpperCase()}</span></div>
                        <div class="strategy-meta"><span>Trades: ${info.paper_metrics?.total_trades ?? '—'}</span><span>Expectancy: ${info.paper_metrics?.expectancy?.toFixed(4) ?? '—'}</span><span>PF: ${info.paper_metrics?.profit_factor?.toFixed(2) ?? '—'}</span></div>
                        <div class="paper-metrics">Paper metrics: ${JSON.stringify(info.paper_metrics || {})}</div>
                        <div class="notes">${info.notes || '—'}</div>
                        ${showActions ? `<div class="action-buttons"><button class="btn btn-approve" onclick="approve('${sid}')">✅ Approve Live</button><button class="btn btn-reject" onclick="reject('${sid}')">❌ Reject</button></div>` : ''}
                    </div>`).join('');
            }
            function renderSafetyAlerts(alerts) {
                const c = document.getElementById('safety-list');
                if (!alerts.length) { c.innerHTML = `<div class="empty-state"><p>All systems nominal</p></div>`; return; }
                c.innerHTML = alerts.map(a => `<div class="strategy-item ${a.severity === 'critical' ? 'live-pending' : 'paper'}"><div class="strategy-header"><span class="strategy-id">${a.type.toUpperCase()}</span><span class="badge ${a.severity === 'critical' ? 'badge-critical' : 'badge-warning'}">${a.severity.toUpperCase()}</span></div><div class="notes">${a.message}</div></div>`).join('');
            }
            async function approve(sid) { if (!confirm(`Approve ${sid} for LIVE?`)) return; const btn = event.target; btn.disabled = true; btn.textContent = 'Approving...'; try { await fetch(`${API_BASE}/approve-live`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({strategy_id:sid, approver:'dashboard-user'})}); loadData(); } catch(e){ alert(e); btn.disabled=false; btn.textContent='✅ Approve Live'; } }
            async function reject(sid) { const reason = prompt(`Reject ${sid} — reason:`); if (!reason) return; const btn = event.target; btn.disabled = true; btn.textContent = 'Rejecting...'; try { await fetch(`${API_BASE}/reject-live`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({strategy_id:sid, reason})}); loadData(); } catch(e){ alert(e); btn.disabled=false; btn.textContent='❌ Reject'; } }
            setInterval(loadData, 30000); loadData();
        </script>
        """
    )


# ======================================================================
# TRADING DASHBOARD
# ======================================================================

@router.get("/trading", response_class=HTMLResponse)
async def trading_dashboard() -> str:
    return _base_html(
        title="Trading Dashboard",
        content="P&L, Open Positions, Equity Curve, Risk Metrics",
        active="trading",
        body="""
        <div class="summary-bar" id="summary">
            <div class="summary-item summary-positive"><div class="summary-value" id="equity">—</div><div class="summary-label">Equity</div></div>
            <div class="summary-item summary-info"><div class="summary-value" id="balance">—</div><div class="summary-label">Balance</div></div>
            <div class="summary-item" id="pnl-wrap"><div class="summary-value" id="unrealized-pnl">—</div><div class="summary-label">Unrealized P&L</div></div>
            <div class="summary-item"><div class="summary-value" id="open-pos">—</div><div class="summary-label">Open Positions</div></div>
        </div>
        <div class="grid-2">
            <section class="card"><h2>📈 Equity Curve</h2><div class="chart-placeholder" id="equity-chart">Loading equity chart...</div></section>
            <section class="card"><h2>⚠️ Risk Metrics</h2><div id="risk-metrics"><div class="metric-row"><span class="metric-label">Max Drawdown</span><span class="metric-value" id="max-dd">—</span></div><div class="metric-row"><span class="metric-label">Daily P&L</span><span class="metric-value" id="daily-pnl">—</span></div><div class="metric-row"><span class="metric-label">Margin Used</span><span class="metric-value" id="margin-used">—</span></div><div class="metric-row"><span class="metric-label">Free Margin</span><span class="metric-value" id="free-margin">—</span></div><div class="metric-row"><span class="metric-label">Margin Level</span><span class="metric-value" id="margin-level">—</span></div></div></section>
        </div>
        <section class="card"><h2>📋 Open Positions</h2><div id="positions-list"></div></section>
        <div class="last-updated" id="last-updated">Last updated: —</div>
        <button class="refresh-btn" onclick="loadData()" title="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path></svg></button>
        <script>
            async function loadData() {
                document.body.classList.add('loading');
                try {
                    const [acct, pos] = await Promise.all([
                        fetch('/paper/account').then(r => r.json()),
                        fetch('/paper/positions').then(r => r.json())
                    ]);
                    render(acct, pos);
                } catch (e) { console.error(e); }
                finally { document.body.classList.remove('loading'); }
            }
            function render(acct, positions) {
                document.getElementById('equity').textContent = '$' + (acct.equity ?? 0).toFixed(2);
                document.getElementById('balance').textContent = '$' + (acct.balance ?? 0).toFixed(2);
                const upnl = acct.unrealized_pnl ?? 0;
                document.getElementById('unrealized-pnl').textContent = '$' + upnl.toFixed(2);
                document.getElementById('unrealized-pnl').className = 'metric-value ' + (upnl >= 0 ? 'positive' : 'negative');
                document.getElementById('open-pos').textContent = positions.length;
                document.getElementById('max-dd').textContent = (acct.max_drawdown_pct ?? 0).toFixed(2) + '%';
                document.getElementById('daily-pnl').textContent = '$' + (acct.daily_pnl ?? 0).toFixed(2);
                document.getElementById('margin-used').textContent = '$' + (acct.margin_used ?? 0).toFixed(2);
                document.getElementById('free-margin').textContent = '$' + (acct.free_margin ?? 0).toFixed(2);
                document.getElementById('margin-level').textContent = (acct.margin_level ?? 0).toFixed(1) + '%';
                document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();

                // Positions table
                const c = document.getElementById('positions-list');
                if (!positions.length) { c.innerHTML = '<div class="empty-state"><p>No open positions</p></div>'; return; }
                c.innerHTML = positions.map(p => `
                    <div class="position-row ${p.side === 'long' ? 'long' : 'short'}">
                        <span class="badge ${p.side === 'long' ? 'badge-positive' : 'badge-negative'}">${p.side.toUpperCase()}</span>
                        <strong>${p.symbol}</strong> | Volume: ${p.volume.toFixed(2)} | Entry: ${p.entry_price.toFixed(5)}
                        <span class="metric-value ${p.unrealized_pnl >= 0 ? 'positive' : 'negative'}">$${p.unrealized_pnl.toFixed(2)}</span>
                    </div>`).join('');
            }
            setInterval(loadData, 10000); loadData();
        </script>
        """
    )


# ======================================================================
# PERFORMANCE ANALYTICS DASHBOARD
# ======================================================================

@router.get("/performance", response_class=HTMLResponse)
async def performance_dashboard() -> str:
    return _base_html(
        title="Performance Analytics",
        content="Sharpe, Drawdown, Win Rate by Symbol/Regime/Timeframe",
        active="performance",
        body="""
        <div class="summary-bar" id="summary">
            <div class="summary-item summary-info"><div class="summary-value" id="sharpe">—</div><div class="summary-label">Sharpe Ratio</div></div>
            <div class="summary-item summary-warning"><div class="summary-value" id="max-dd">—</div><div class="summary-label">Max Drawdown</div></div>
            <div class="summary-item summary-positive"><div class="summary-value" id="win-rate">—</div><div class="summary-label">Win Rate</div></div>
            <div class="summary-item"><div class="summary-value" id="profit-factor">—</div><div class="summary-label">Profit Factor</div></div>
        </div>
        <div class="grid-2">
            <section class="card"><h2>📊 By Symbol</h2><div id="by-symbol"></div></section>
            <section class="card"><h2>🌊 By Regime</h2><div id="by-regime"></div></section>
            <section class="card"><h2>⏱️ By Timeframe</h2><div id="by-tf"></div></section>
            <section class="card"><h2>📈 Equity & Drawdown</h2><div class="chart-placeholder" id="dd-chart">Equity/DD chart placeholder</div></section>
        </div>
        <div class="last-updated" id="last-updated">Last updated: —</div>
        <button class="refresh-btn" onclick="loadData()" title="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path></svg></button>
        <script>
            async function loadData() {
                document.body.classList.add('loading');
                try {
                    const res = await fetch('/performance/segmented');
                    const data = await res.json();
                    render(data);
                } catch (e) { console.error(e); }
                finally { document.body.classList.remove('loading'); }
            }
            function render(data) {
                const o = data.overall || {};
                document.getElementById('sharpe').textContent = (o.sharpe_ratio ?? 0).toFixed(2);
                document.getElementById('max-dd').textContent = (o.max_drawdown ?? 0).toFixed(2) + '%';
                document.getElementById('win-rate').textContent = ((o.win_rate ?? 0) * 100).toFixed(1) + '%';
                document.getElementById('profit-factor').textContent = (o.profit_factor ?? 0).toFixed(2);
                document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();

                const renderTable = (containerId, segData) => {
                    const c = document.getElementById(containerId);
                    if (!segData || !Object.keys(segData).length) { c.innerHTML = '<div class="empty-state"><p>No data</p></div>'; return; }
                    c.innerHTML = '<table class="table"><thead><tr><th>Segment</th><th>Trades</th><th>Win Rate</th><th>P&L</th><th>Avg P&L</th></tr></thead><tbody>' +
                        Object.entries(segData).map(([k, v]) => `<tr><td><strong>${k}</strong></td><td>${v.total_trades}</td><td>${(v.win_rate*100).toFixed(1)}%</td><td class="${v.total_pnl >= 0 ? 'positive' : 'negative'}">$${v.total_pnl.toFixed(2)}</td><td class="${v.avg_pnl >= 0 ? 'positive' : 'negative'}">$${v.avg_pnl.toFixed(2)}</td></tr>`).join('') +
                    '</tbody></table>';
                };
                renderTable('by-symbol', data.by_symbol);
                renderTable('by-regime', data.by_regime);
                renderTable('by-tf', data.by_timeframe);
            }
            setInterval(loadData, 30000); loadData();
        </script>
        """
    )


# ======================================================================
# RESEARCH / EXPERIMENT DASHBOARD
# ======================================================================

@router.get("/research", response_class=HTMLResponse)
async def research_dashboard() -> str:
    return _base_html(
        title="Research Dashboard",
        content="Hypothesis Tracker, Experiment Results, A/B Comparisons",
        active="research",
        body="""
        <div class="summary-bar" id="summary">
            <div class="summary-item summary-info"><div class="summary-value" id="hypo-count">—</div><div class="summary-label">Active Hypotheses</div></div>
            <div class="summary-item summary-warning"><div class="summary-value" id="exp-count">—</div><div class="summary-label">Running Experiments</div></div>
            <div class="summary-item summary-success"><div class="summary-value" id="completed">—</div><div class="summary-label">Completed</div></div>
            <div class="summary-item"><div class="summary-value" id="promoted">—</div><div class="summary-label">Promoted</div></div>
        </div>
        <div class="grid-2">
            <section class="card"><h2>💡 Hypotheses</h2><div id="hypotheses-list"></div></section>
            <section class="card"><h2>🧪 Experiments</h2><div id="experiments-list"></div></section>
        </div>
        <div class="last-updated" id="last-updated">Last updated: —</div>
        <button class="refresh-btn" onclick="loadData()" title="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path></svg></button>
        <script>
            async function loadData() {
                document.body.classList.add('loading');
                try {
                    const [hyps, exps] = await Promise.all([
                        fetch('/research/hypotheses').then(r => r.json()),
                        fetch('/research/experiments').then(r => r.json())
                    ]);
                    render(hyps, exps);
                } catch (e) { console.error(e); }
                finally { document.body.classList.remove('loading'); }
            }
            function render(hyps, exps) {
                document.getElementById('hypo-count').textContent = hyps.filter(h => h.status === 'proposed' || h.status === 'testing').length;
                document.getElementById('exp-count').textContent = exps.filter(e => e.status === 'running').length;
                document.getElementById('completed').textContent = exps.filter(e => e.status === 'completed').length;
                document.getElementById('promoted').textContent = exps.filter(e => e.gatekeeper_decision === 'promote').length;
                document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();

                const renderList = (cid, items, type) => {
                    const c = document.getElementById(cid);
                    if (!items.length) { c.innerHTML = '<div class="empty-state"><p>No ' + type + '</p></div>'; return; }
                    c.innerHTML = items.map(item => `
                        <div class="strategy-item">
                            <div class="strategy-header">
                                <span class="strategy-id">${item.id?.slice(0,12) || item.hypothesis_id?.slice(0,12) || '—'}</span>
                                <span class="badge badge-info">${item.status || '—'}</span>
                            </div>
                            <div class="strategy-meta">
                                <span>${item.description || item.name || item.hypothesis_id || ''}</span>
                            </div>
                            ${item.gatekeeper_decision ? `<div class="notes">Gatekeeper: <strong>${item.gatekeeper_decision}</strong></div>` : ''}
                            ${item.metrics ? `<div class="paper-metrics">Metrics: ${JSON.stringify(item.metrics)}</div>` : ''}
                        </div>`).join('');
                };
                renderList('hypotheses-list', hyps, 'hypotheses');
                renderList('experiments-list', exps, 'experiments');
            }
            setInterval(loadData, 30000); loadData();
        </script>
        """
    )


# ======================================================================
# SYSTEM MONITOR DASHBOARD
# ======================================================================

@router.get("/system", response_class=HTMLResponse)
async def system_dashboard() -> str:
    return _base_html(
        title="System Monitor",
        content="Health, Latency, Database Size, Error Rates",
        active="system",
        body="""
        <div class="summary-bar" id="summary">
            <div class="summary-item summary-success"><div class="summary-value" id="status">—</div><div class="summary-label">Overall Status</div></div>
            <div class="summary-item summary-info"><div class="summary-value" id="uptime">—</div><div class="summary-label">Uptime</div></div>
            <div class="summary-item summary-warning"><div class="summary-value" id="mem-pct">—</div><div class="summary-label">Memory</div></div>
            <div class="summary-item"><div class="summary-value" id="disk-pct">—</div><div class="summary-label">Disk</div></div>
        </div>
        <div class="grid">
            <section class="card"><h2>🩺 Component Health</h2><div id="components-list"></div></section>
            <section class="card"><h2>💾 Database Status</h2><div id="db-status"></div></section>
            <section class="card"><h2>📝 Recent Logs</h2><div id="recent-logs"></div></section>
            <section class="card"><h2>⚡ Latency Metrics</h2><div class="chart-placeholder" id="latency-chart">Latency chart placeholder</div></section>
        </div>
        <div class="last-updated" id="last-updated">Last updated: —</div>
        <button class="refresh-btn" onclick="loadData()" title="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path></svg></button>
        <script>
            async function loadData() {
                document.body.classList.add('loading');
                try {
                    const res = await fetch('/health');
                    const data = await res.json();
                    render(data);
                } catch (e) { console.error(e); }
                finally { document.body.classList.remove('loading'); }
            }
            function render(d) {
                document.getElementById('status').textContent = d.overall_status?.toUpperCase() || 'UNKNOWN';
                document.getElementById('status').className = 'summary-value ' + (d.overall_status === 'healthy' ? 'positive' : d.overall_status === 'degraded' ? '' : 'negative');
                document.getElementById('uptime').textContent = (d.uptime_seconds ? (d.uptime_seconds/3600).toFixed(1) + 'h' : '—');
                document.getElementById('mem-pct').textContent = (d.components?.memory?.metadata?.used_pct ?? '—') + '%';
                document.getElementById('disk-pct').textContent = (d.components?.disk?.metadata?.used_pct ?? '—') + '%';
                document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();

                // Components
                const comps = d.components || {};
                document.getElementById('components-list').innerHTML = Object.entries(comps).map(([k,v]) => `
                    <div class="metric-row"><span class="metric-label">${k}</span><span class="metric-value ${v.status === 'healthy' ? 'positive' : v.status === 'degraded' ? '' : 'negative'}">${v.status.toUpperCase()}</span></div>
                `).join('');

                // DB info (basic)
                document.getElementById('db-status').innerHTML = `
                    <div class="metric-row"><span class="metric-label">Status</span><span class="metric-value positive">${comps.database?.status?.toUpperCase() || 'UNKNOWN'}</span></div>
                    <div class="metric-row"><span class="metric-label">Latency</span><span class="metric-value">${comps.database?.latency_ms?.toFixed(1) || '—'} ms</span></div>
                `;

                // Logs placeholder
                document.getElementById('recent-logs').innerHTML = '<div class="empty-state"><p>Log integration pending</p></div>';
            }
            setInterval(loadData, 15000); loadData();
        </script>
        """
    )


# ======================================================================
# STRATEGY LAB DASHBOARD
# ======================================================================

@router.get("/strategy", response_class=HTMLResponse)
async def strategy_dashboard() -> str:
    return _base_html(
        title="Strategy Lab",
        content="Genome Viewer, Mutation History, Backtest Comparison",
        active="strategy",
        body="""
        <div class="summary-bar" id="summary">
            <div class="summary-item summary-info"><div class="summary-value" id="genome-count">—</div><div class="summary-label">Genomes</div></div>
            <div class="summary-item summary-warning"><div class="summary-value" id="active-count">—</div><div class="summary-label">Active</div></div>
            <div class="summary-item summary-success"><div class="summary-value" id="lineage-depth">—</div><div class="summary-label">Lineage Depth</div></div>
            <div class="summary-item"><div class="summary-value" id="mutations">—</div><div class="summary-label">Mutations</div></div>
        </div>
        <div class="grid-2">
            <section class="card"><h2>🧬 Current Genome</h2><div id="current-genome"></div></section>
            <section class="card"><h2>📜 Lineage Tree</h2><pre class="genome-tree" id="lineage-tree">Loading...</pre></section>
        </div>
        <section class="card"><h2>🔬 Mutation Credits</h2><div id="mutations-list"></div></section>
        <section class="card"><h2>⚙️ Best Genome Config</h2><pre class="genome-tree" id="best-config">Loading...</pre></section>
        <div class="last-updated" id="last-updated">Last updated: —</div>
        <button class="refresh-btn" onclick="loadData()" title="Refresh"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path></svg></button>
        <script>
            async function loadData() {
                document.body.classList.add('loading');
                try {
                    const [status, lineage, credits] = await Promise.all([
                        fetch('/evolution/self-learning/status').then(r => r.json()),
                        fetch('/evolution/lineage').then(r => r.json()),
                        fetch('/evolution/self-learning/credits').then(r => r.json())
                    ]);
                    render(status, lineage, credits);
                } catch (e) { console.error(e); }
                finally { document.body.classList.remove('loading'); }
            }
            function render(status, lineage, credits) {
                document.getElementById('genome-count').textContent = status.population_size || 0;
                document.getElementById('active-count').textContent = (status.live_approved || []).length;
                document.getElementById('lineage-depth').textContent = (status.history_tail || []).length;
                document.getElementById('mutations').textContent = credits.length;
                document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();

                const cg = status.best_genome_id || '—';
                document.getElementById('current-genome').innerHTML = `
                    <div class="metric-row"><span class="metric-label">Best Genome ID</span><span class="metric-value">${cg}</span></div>
                    <div class="metric-row"><span class="metric-label">Iteration</span><span class="metric-value">${status.iterations_completed}</span></div>
                    <div class="metric-row"><span class="metric-label">Promotions</span><span class="metric-value">${status.promotions}</span></div>
                    <div class="metric-row"><span class="metric-label">Phase</span><span class="metric-value">${status.safety?.phase || '—'}</span></div>
                `;

                const tree = lineage.tree || [];
                const renderTree = (nodes, indent = 0) => nodes.map(n => '  '.repeat(indent) + '├─ ' + n.strategy_id + (n.children?.length ? ' [' + n.children.length + ' children]' : '') + '\n' + (n.children ? renderTree(n.children, indent+1) : '')).join('');
                document.getElementById('lineage-tree').textContent = tree.length ? renderTree(lineage.roots.map(r => tree.find(t => t.strategy_id === r)).filter(Boolean)) : 'No lineage yet';

                document.getElementById('mutations-list').innerHTML = credits.length ? credits.map(c => `
                    <div class="strategy-item">
                        <div class="strategy-header"><span class="strategy-id">${c.mutation_type}</span><span class="badge badge-info">${(c.success_rate*100).toFixed(0)}% success</span></div>
                        <div class="strategy-meta"><span>Attempts: ${c.attempts}</span><span>Avg ΔPF: ${c.avg_delta_pf.toFixed(4)}</span><span>Weight: ${c.weight.toFixed(3)}</span></div>
                    </div>`).join('') : '<div class="empty-state"><p>No mutation data</p></div>';

                document.getElementById('best-config').textContent = JSON.stringify(status.best_metrics || {}, null, 2);
            }
            setInterval(loadData, 30000); loadData();
        </script>
        """
    )


# ======================================================================
# BASE HTML TEMPLATE
# ======================================================================

def _base_html(title: str, content: str, active: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading AI - {title}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 0; background: #f5f5f5; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
        .nav-bar {{ display: flex; justify-content: space-between; align-items: center; background: #1a1a2e; color: white; padding: 16px 24px; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }}
        .nav-brand {{ font-weight: 700; font-size: 1.2rem; letter-spacing: -0.5px; }}
        .nav-links {{ display: flex; gap: 8px; }}
        .nav-link {{ padding: 8px 16px; border-radius: 6px; color: #a0a0b0; text-decoration: none; font-size: 0.9rem; font-weight: 500; transition: all 0.2s; }}
        .nav-link:hover {{ background: rgba(255,255,255,0.1); color: white; }}
        .nav-link.active {{ background: #3b82f6; color: white; }}
        h1 {{ color: #1a1a2e; margin: 24px 0 8px; font-size: 1.5rem; }}
        .subtitle {{ color: #666; margin-bottom: 24px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 16px; }}
        .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 16px; }}
        .card {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border: 1px solid #eee; }}
        .card h2 {{ margin: 0 0 16px; font-size: 1.1rem; color: #333; display: flex; align-items: center; gap: 8px; }}
        .card h2::before {{ content: ''; width: 8px; height: 8px; border-radius: 50%; background: #3b82f6; }}
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
        .badge-positive {{ background: #e8f8f5; color: #1e8e49; }}
        .badge-negative {{ background: #fdeaea; color: #c0392b; }}
        .badge-neutral {{ background: #f0f0f0; color: #666; }}
        .badge-info {{ background: #eaf2f8; color: #2980b9; }}
        .badge-warning {{ background: #fef5e7; color: #b7950b; }}
        .badge-critical {{ background: #fdeaea; color: #c0392b; }}
        .metric-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f0f0f0; }}
        .metric-row:last-child {{ border-bottom: none; }}
        .metric-label {{ color: #666; }}
        .metric-value {{ font-weight: 600; font-family: monospace; }}
        .metric-value.positive {{ color: #1e8e49; }}
        .metric-value.negative {{ color: #c0392b; }}
        .chart-placeholder {{ height: 200px; background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #999; font-size: 0.9rem; }}
        .table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
        .table th {{ text-align: left; padding: 10px; color: #666; font-weight: 600; border-bottom: 2px solid #eee; }}
        .table td {{ padding: 10px; border-bottom: 1px solid #f0f0f0; }}
        .table tr:hover td {{ background: #fafafa; }}
        .summary-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }}
        .summary-item {{ background: white; padding: 16px; border-radius: 10px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .summary-value {{ font-size: 2rem; font-weight: 700; line-height: 1.2; }}
        .summary-label {{ font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
        .summary-positive .summary-value {{ color: #1e8e49; }}
        .summary-negative .summary-value {{ color: #c0392b; }}
        .summary-info .summary-value {{ color: #3b82f6; }}
        .summary-warning .summary-value {{ color: #f39c12; }}
        .refresh-btn {{ position: fixed; bottom: 24px; right: 24px; width: 56px; height: 56px; border-radius: 50%; background: #1a1a2e; color: white; border: none; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.2); display: flex; align-items: center; justify-content: center; }}
        .refresh-btn:hover {{ background: #2c2c4e; }}
        .refresh-btn svg {{ width: 24px; height: 24px; }}
        .last-updated {{ font-size: 0.8rem; color: #888; margin-top: 8px; }}
        .loading {{ opacity: 0.6; pointer-events: none; }}
        .empty-state {{ text-align: center; color: #999; padding: 40px 20px; }}
        .empty-state svg {{ width: 48px; height: 48px; margin-bottom: 12px; opacity: 0.5; }}
        .position-row {{ display: flex; gap: 16px; padding: 12px; background: #fafafa; border-radius: 8px; margin-bottom: 8px; align-items: center; }}
        .position-row.long {{ border-left: 4px solid #1e8e49; }}
        .position-row.short {{ border-left: 4px solid #c0392b; }}
        .strategy-item {{ padding: 12px; border-radius: 8px; margin-bottom: 12px; background: #fafafa; border-left: 4px solid #ddd; }}
        .strategy-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
        .strategy-id {{ font-weight: 600; font-family: monospace; font-size: 0.9rem; }}
        .strategy-meta {{ display: flex; gap: 16px; font-size: 0.85rem; color: #555; }}
        .notes {{ font-size: 0.85rem; color: #666; margin-top: 8px; padding-top: 8px; border-top: 1px solid #eee; }}
        .paper-metrics {{ font-size: 0.8rem; color: #777; font-family: monospace; }}
        .genome-tree {{ font-family: monospace; font-size: 0.8rem; line-height: 1.8; white-space: pre-wrap; background: #f8fafc; padding: 16px; border-radius: 8px; overflow-x: auto; }}
        .action-buttons {{ display: flex; gap: 8px; margin-top: 12px; }}
        .btn {{ padding: 8px 16px; border-radius: 6px; border: none; font-weight: 500; cursor: pointer; font-size: 0.85rem; }}
        .btn-approve {{ background: #27ae60; color: white; }}
        .btn-reject {{ background: #e74c3c; color: white; }}
    </style>
</head>
<body>
    {_nav_bar(active)}
    <div class="container">
        <h1>{title}</h1>
        <p class="subtitle">{content}</p>
        {body}
    </div>
</body>
</html>
"""