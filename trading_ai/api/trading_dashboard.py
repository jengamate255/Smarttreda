"""FastAPI trading dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/trading", response_class=HTMLResponse)
async def trading_dashboard() -> str:
    """Real-time trading dashboard: P&L, positions, equity, risk."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading AI - Trading Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #0d1117; color: #e6edf3; }
        .container { max-width: 1600px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #30363d; }
        h1 { margin: 0; font-size: 1.5rem; }
        .connection { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; }
        .dot { width: 8px; height: 8px; border-radius: 50%; background: #3fb950; }
        .dot.disconnected { background: #f85149; }
        
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 20px; }
        .card h2 { margin: 0 0 16px; font-size: 1rem; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
        
        /* KPI Cards */
        .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
        .kpi { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 16px; text-align: center; }
        .kpi-value { font-size: 1.75rem; font-weight: 700; line-height: 1.2; }
        .kpi-label { font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
        .kpi-profit .kpi-value { color: #3fb950; }
        .kpi-loss .kpi-value { color: #f85149; }
        .kpi-neutral .kpi-value { color: #58a6ff; }
        
        /* Equity Chart */
        .chart-container { height: 300px; position: relative; }
        
        /* Positions Table */
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th { text-align: left; padding: 10px 12px; color: #8b949e; font-weight: 600; border-bottom: 1px solid #30363d; }
        td { padding: 10px 12px; border-bottom: 1px solid #21262d; }
        tr:hover { background: #21262d; }
        .pos-long { color: #3fb950; }
        .pos-short { color: #f85149; }
        .pnl-positive { color: #3fb950; }
        .pnl-negative { color: #f85149; }
        
        /* Orders */
        .order-list { display: flex; flex-direction: column; gap: 8px; }
        .order-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; background: #21262d; border-radius: 8px; }
        .order-info { display: flex; flex-direction: column; gap: 2px; }
        .order-symbol { font-weight: 600; }
        .order-meta { font-size: 0.75rem; color: #8b949e; }
        .order-side { padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }
        .side-buy { background: rgba(63,185,80,0.2); color: #3fb950; }
        .side-sell { background: rgba(248,81,73,0.2); color: #f85149; }
        
        /* Risk Meter */
        .risk-meter { height: 60px; position: relative; }
        .risk-bar { height: 100%; border-radius: 8px; background: linear-gradient(90deg, #3fb950 0%, #d29922 50%, #f85149 100%); position: relative; overflow: hidden; }
        .risk-indicator { position: absolute; top: -8px; width: 4px; height: 76px; background: white; border-radius: 2px; transform: translateX(-50%); transition: left 0.3s ease; }
        .risk-labels { display: flex; justify-content: space-between; margin-top: 8px; font-size: 0.7rem; color: #8b949e; }
        
        .empty { text-align: center; color: #8b949e; padding: 40px 20px; }
        .refresh-indicator { position: fixed; bottom: 20px; right: 20px; width: 48px; height: 48px; border-radius: 50%; background: #238636; color: white; border: none; display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        .refresh-indicator.spinning svg { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        
        .tabs { display: flex; gap: 4px; margin-bottom: 16px; background: #21262d; padding: 4px; border-radius: 8px; }
        .tab { padding: 8px 16px; border-radius: 6px; background: transparent; border: none; color: #8b949e; cursor: pointer; font-size: 0.85rem; }
        .tab.active { background: #238636; color: white; }
        
        .tab-panel { display: none; }
        .tab-panel.active { display: block; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📈 Trading Dashboard</h1>
            <div class="connection">
                <span class="dot" id="conn-dot"></span>
                <span id="conn-status">Connecting...</span>
            </div>
        </header>
        
        <!-- KPI Row -->
        <div class="kpi-grid">
            <div class="kpi kpi-neutral">
                <div class="kpi-value" id="equity">—</div>
                <div class="kpi-label">Equity</div>
            </div>
            <div class="kpi kpi-profit">
                <div class="kpi-value" id="unrealized">—</div>
                <div class="kpi-label">Unrealized P&L</div>
            </div>
            <div class="kpi kpi-neutral">
                <div class="kpi-value" id="realized">—</div>
                <div class="kpi-label">Realized P&L (Day)</div>
            </div>
            <div class="kpi kpi-neutral">
                <div class="kpi-value" id="margin">—</div>
                <div class="kpi-label">Free Margin</div>
            </div>
        </div>
        
        <!-- Tabs -->
        <div class="tabs">
            <button class="tab active" data-tab="positions">📋 Positions</button>
            <button class="tab" data-tab="orders">📥 Orders</button>
            <button class="tab" data-tab="history">📜 History</button>
            <button class="tab" data-tab="risk">⚠️ Risk</button>
        </div>
        
        <div class="grid">
            <!-- Equity Chart (always visible) -->
            <section class="card" style="grid-column: 1 / -1;">
                <h2>Equity Curve</h2>
                <div class="chart-container">
                    <canvas id="equityChart"></canvas>
                </div>
            </section>
            
            <!-- Positions Tab -->
            <section class="card tab-panel active" id="panel-positions" style="grid-column: 1 / -1;">
                <h2>Open Positions</h2>
                <div class="table-wrap" id="positions-table">
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th><th>Side</th><th>Volume</th><th>Entry</th><th>Current</th>
                                <th>SL</th><th>TP</th><th>Unrealized</th><th>Pips</th><th>Duration</th>
                            </tr>
                        </thead>
                        <tbody id="positions-body"></tbody>
                    </table>
                </div>
                <div class="empty" id="no-positions" style="display:none;">No open positions</div>
            </section>
            
            <!-- Orders Tab -->
            <section class="card tab-panel" id="panel-orders" style="grid-column: 1 / -1;">
                <h2>Pending Orders</h2>
                <div class="order-list" id="orders-list">
                    <div class="empty">No pending orders</div>
                </div>
            </section>
            
            <!-- History Tab -->
            <section class="card tab-panel" id="panel-history" style="grid-column: 1 / -1;">
                <h2>Recent Trades (Last 50)</h2>
                <div class="table-wrap" id="history-table">
                    <table>
                        <thead>
                            <tr>
                                <th>Time</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th>
                                <th>Volume</th><th>P&L</th><th>R</th><th>Duration</th>
                            </tr>
                        </thead>
                        <tbody id="history-body"></tbody>
                    </table>
                </div>
                <div class="empty" id="no-history" style="display:none;">No trade history</div>
            </section>
            
            <!-- Risk Tab -->
            <section class="card tab-panel" id="panel-risk">
                <h2>Risk Meter</h2>
                <div class="risk-meter">
                    <div class="risk-bar" id="risk-bar">
                        <div class="risk-indicator" id="risk-indicator" style="left: 0%;"></div>
                    </div>
                </div>
                <div class="risk-labels">
                    <span>Safe</span><span>Caution</span><span>Danger</span>
                </div>
                <div style="margin-top: 16px;">
                    <div class="kpi-grid" style="grid-template-columns: repeat(2, 1fr);">
                        <div class="kpi"><div class="kpi-value" id="dd-pct">—</div><div class="kpi-label">Current Drawdown</div></div>
                        <div class="kpi"><div class="kpi-value" id="dd-max">—</div><div class="kpi-label">Max Drawdown</div></div>
                        <div class="kpi"><div class="kpi-value" id="margin-pct">—</div><div class="kpi-label">Margin Level</div></div>
                        <div class="kpi"><div class="kpi-value" id="leverage">—</div><div class="kpi-label">Effective Leverage</div></div>
                    </div>
                </div>
            </section>
            
            <!-- Daily Stats -->
            <section class="card tab-panel" id="panel-risk-stats">
                <h2>Daily Stats</h2>
                <div class="kpi-grid" style="grid-template-columns: repeat(2, 1fr);">
                    <div class="kpi"><div class="kpi-value" id="daily-trades">—</div><div class="kpi-label">Trades Today</div></div>
                    <div class="kpi"><div class="kpi-value" id="daily-wr">—</div><div class="kpi-label">Win Rate</div></div>
                    <div class="kpi"><div class="kpi-value" id="daily-pf">—</div><div class="kpi-label">Profit Factor</div></div>
                    <div class="kpi"><div class="kpi-value" id="daily-exp">—</div><div class="kpi-label">Expectancy</div></div>
                </div>
            </section>
        </div>
    </div>
    
    <button class="refresh-indicator" id="refresh-btn" onclick="loadAll()" title="Refresh">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 4v6h-6"></path><path d="M1 20v-6h6"></path>
            <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"></path>
        </svg>
    </button>

    <script>
        const API = '/api/v1/trading'; // will be proxied or use existing endpoints
        let equityChart = null;
        let equityData = { labels: [], datasets: [] };
        
        // Chart init
        function initChart() {
            const ctx = document.getElementById('equityChart').getContext('2d');
            equityChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Equity',
                        data: [],
                        borderColor: '#58a6ff',
                        backgroundColor: 'rgba(88,166,255,0.1)',
                        fill: true,
                        tension: 0.2,
                        pointRadius: 0,
                        borderWidth: 2
                    }, {
                        label: 'Balance',
                        data: [],
                        borderColor: '#8b949e',
                        borderDash: [5, 5],
                        fill: false,
                        pointRadius: 0,
                        borderWidth: 1.5
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { labels: { color: '#8b949e', font: { size: 11 } } } },
                    scales: {
                        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', maxTicksLimit: 8 } },
                        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }
                    }
                }
            });
        }
        
        // Tabs
        document.querySelectorAll('.tab').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById('panel-' + btn.dataset.tab).classList.add('active');
            });
        });
        
        async function loadAll() {
            const btn = document.getElementById('refresh-btn');
            btn.classList.add('spinning');
            try {
                const [acct, positions, orders, history, perf] = await Promise.all([
                    fetch('/paper/account').then(r => r.json()).catch(() => ({})),
                    fetch('/paper/positions').then(r => r.json()).catch(() => []),
                    fetch('/paper/orders').then(r => r.json()).catch(() => []),
                    fetch('/trades?limit=50').then(r => r.json()).catch(() => []),
                    fetch('/performance').then(r => r.json()).catch(() => ({}))
                ]);
                renderAccount(acct);
                renderPositions(positions);
                renderOrders(orders);
                renderHistory(history);
                renderPerformance(perf);
                updateEquityChart(acct);
            } catch (e) {
                console.error('Load failed:', e);
                setConnStatus(false);
            } finally {
                btn.classList.remove('spinning');
            }
        }
        
        function setConnStatus(ok) {
            const dot = document.getElementById('conn-dot');
            const status = document.getElementById('conn-status');
            if (ok) { dot.classList.remove('disconnected'); status.textContent = 'Connected'; }
            else { dot.classList.add('disconnected'); status.textContent = 'Disconnected'; }
        }
        
        function renderAccount(a) {
            setConnStatus(true);
            document.getElementById('equity').textContent = fmtMoney(a.equity ?? a.balance ?? 0);
            document.getElementById('unrealized').textContent = fmtMoney(a.unrealized_pnl ?? 0, true);
            document.getElementById('realized').textContent = fmtMoney(a.realized_pnl ?? 0, true);
            document.getElementById('margin').textContent = fmtMoney(a.free_margin ?? a.margin_free ?? 0);
            document.getElementById('margin-pct').textContent = a.margin_level ? a.margin_level.toFixed(1) + '%' : '—';
            document.getElementById('leverage').textContent = a.leverage ? a.leverage.toFixed(1) + 'x' : '—';
        }
        
        function renderPositions(list) {
            const tbody = document.getElementById('positions-body');
            const empty = document.getElementById('no-positions');
            if (!list || list.length === 0) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
            empty.style.display = 'none';
            tbody.innerHTML = list.map(p => {
                const side = p.side === 'LONG' || p.side === 'BUY' ? 'long' : 'short';
                const pnl = p.unrealized_pnl ?? 0;
                const pips = p.pips ?? 0;
                return `<tr>
                    <td>${p.symbol}</td>
                    <td class="pos-${side}">${p.side}</td>
                    <td>${p.volume}</td>
                    <td>${p.entry_price?.toFixed(5) ?? '—'}</td>
                    <td>${p.current_price?.toFixed(5) ?? '—'}</td>
                    <td>${p.stop_loss?.toFixed(5) ?? '—'}</td>
                    <td>${p.take_profit?.toFixed(5) ?? '—'}</td>
                    <td class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${fmtMoney(pnl, true)}</td>
                    <td class="${pips >= 0 ? 'pnl-positive' : 'pnl-negative'}">${pips >= 0 ? '+' : ''}${pips.toFixed(1)}</td>
                    <td>${fmtDuration(p.opened_at)}</td>
                </tr>`;
            }).join('');
        }
        
        function renderOrders(list) {
            const container = document.getElementById('orders-list');
            if (!list || list.length === 0) { container.innerHTML = '<div class="empty">No pending orders</div>'; return; }
            container.innerHTML = list.map(o => `
                <div class="order-item">
                    <div class="order-info">
                        <span class="order-symbol">${o.symbol}</span>
                        <span class="order-meta">${o.type} @ ${o.price?.toFixed(5) ?? 'market'} | Vol: ${o.volume}</span>
                    </div>
                    <span class="order-side side-${o.side?.toLowerCase() ?? 'buy'}">${o.side}</span>
                </div>`).join('');
        }
        
        function renderHistory(list) {
            const tbody = document.getElementById('history-body');
            const empty = document.getElementById('no-history');
            if (!list || list.length === 0) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
            empty.style.display = 'none';
            tbody.innerHTML = list.slice(0, 50).map(t => {
                const pnl = t.pnl ?? 0;
                const r = t.r_multiple ?? 0;
                return `<tr>
                    <td>${fmtTime(t.exit_time ?? t.entry_time)}</td>
                    <td>${t.symbol}</td>
                    <td class="pos-${t.side === 'BUY' ? 'long' : 'short'}">${t.side}</td>
                    <td>${t.entry_price?.toFixed(5) ?? '—'}</td>
                    <td>${t.exit_price?.toFixed(5) ?? '—'}</td>
                    <td>${t.volume ?? t.lot_size ?? '—'}</td>
                    <td class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${fmtMoney(pnl, true)}</td>
                    <td class="${r >= 0 ? 'pnl-positive' : 'pnl-negative'}">${r.toFixed(2)}R</td>
                    <td>${fmtDuration(t.entry_time, t.exit_time)}</td>
                </tr>`;
            }).join('');
        }
        
        function renderPerformance(p) {
            document.getElementById('dd-pct').textContent = (p.max_drawdown_pct ? (p.max_drawdown_pct * 100).toFixed(2) + '%' : '—');
            document.getElementById('dd-max').textContent = (p.max_drawdown ? (p.max_drawdown * 100).toFixed(2) + '%' : '—');
            document.getElementById('daily-trades').textContent = p.total_trades ?? '—';
            document.getElementById('daily-wr').textContent = p.win_rate ? (p.win_rate * 100).toFixed(1) + '%' : '—';
            document.getElementById('daily-pf').textContent = p.profit_factor ? p.profit_factor.toFixed(2) : '—';
            document.getElementById('daily-exp').textContent = p.expectancy ? p.expectancy.toFixed(4) : '—';
            
            // Risk meter
            const dd = p.max_drawdown_pct ?? 0;
            const pos = Math.min(100, Math.max(0, (dd / 0.1) * 100)); // 10% = 100%
            document.getElementById('risk-indicator').style.left = pos + '%';
        }
        
        function updateEquityChart(acct) {
            const now = new Date().toLocaleTimeString();
            if (!equityData.labels) equityData.labels = [];
            equityData.labels.push(now);
            equityData.datasets[0].data.push(acct.equity ?? acct.balance ?? 0);
            equityData.datasets[1].data.push(acct.balance ?? 0);
            if (equityData.labels.length > 60) {
                equityData.labels.shift();
                equityData.datasets.forEach(d => d.data.shift());
            }
            if (equityChart) equityChart.update('none');
        }
        
        function fmtMoney(v, signed = false) {
            if (v === null || v === undefined || isNaN(v)) return '—';
            const s = v >= 0 && signed ? '+' : '';
            return s + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }
        function fmtTime(ts) { if (!ts) return '—'; return new Date(ts).toLocaleTimeString(); }
        function fmtDuration(start, end = null) {
            if (!start) return '—';
            const s = new Date(start).getTime();
            const e = end ? new Date(end).getTime() : Date.now();
            const ms = e - s;
            if (ms < 60000) return Math.round(ms/1000) + 's';
            if (ms < 3600000) return Math.round(ms/60000) + 'm';
            return Math.round(ms/3600000) + 'h';
        }
        
        // Auto-refresh
        setInterval(loadAll, 5000);
        initChart();
        loadAll();
    </script>
</body>
</html>
    """