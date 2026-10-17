/* ═══════════════════════════════════════════════════════════════════════════
   FOREX DOJO — REIMAGINED
   RL Training Dashboard with Modern UI
   ═══════════════════════════════════════════════════════════════════════════ */

const Dojo = (() => {
  const API = '';
  let pollTimer = null;
  let _currentSymbol = null;
  let _livePollTimer = null;

  // ── Authenticated request helpers ────────────────────────────────────
  async function _unwrap(res) {
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error((data && (data.detail || data.error)) || `HTTP ${res.status}`);
    return data;
  }

  async function _get(path) {
    const res = await fetch(path, { headers: AUTH_TOKEN ? { 'X-Auth-Token': AUTH_TOKEN } : {} });
    return _unwrap(res);
  }

  async function _post(path) {
    const headers = { 'Content-Type': 'application/json' };
    if (AUTH_TOKEN) headers['X-Auth-Token'] = AUTH_TOKEN;
    const res = await fetch(path, { method: 'POST', headers });
    return _unwrap(res);
  }

  async function _postBody(path, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (AUTH_TOKEN) headers['X-Auth-Token'] = AUTH_TOKEN;
    const res = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body || {}) });
    return _unwrap(res);
  }

  async function _del(path) {
    const headers = AUTH_TOKEN ? { 'X-Auth-Token': AUTH_TOKEN } : {};
    const res = await fetch(path, { method: 'DELETE', headers });
    return _unwrap(res);
  }

  // ── Skill level config ────────────────────────────────────────────────
  const SKILL_CONFIG = {
    'Master':       { color: '#00d4aa', bg: 'rgba(0,212,170,0.12)', icon: '🏆', rank: 5 },
    'Advanced':     { color: '#3b82f6', bg: 'rgba(59,130,246,0.12)', icon: '🥇', rank: 4 },
    'Intermediate': { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', icon: '🥈', rank: 3 },
    'Novice':       { color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)', icon: '🥉', rank: 2 },
    'Beginner':     { color: '#ef4444', bg: 'rgba(239,68,68,0.12)', icon: '🚲', rank: 1 },
    'Unknown':      { color: '#64748b', bg: 'rgba(100,116,139,0.12)', icon: '❓', rank: 0 },
  };

  function formatEta(seconds) {
    if (!seconds || seconds <= 0) return '--:--';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m + ':' + String(s).padStart(2, '0');
  }

  function formatNumber(n, decimals = 2) {
    if (n === null || n === undefined || isNaN(n)) return '--';
    return Number(n).toFixed(decimals);
  }

  function formatCurrency(n) {
    if (n === null || n === undefined || isNaN(n)) return '--';
    const prefix = n >= 0 ? '+$' : '-$';
    return prefix + Math.abs(n).toFixed(2);
  }

  // ── Main render ───────────────────────────────────────────────────────
  function render() {
    return `
      <div class="dojo-v2">
        <!-- Hero Header -->
        <div class="dojo-hero">
          <div class="dojo-hero-content">
            <div class="dojo-hero-icon">🥋</div>
            <div>
              <h1 class="dojo-hero-title">ForexDojo</h1>
              <p class="dojo-hero-sub">Reinforcement Learning Training Center</p>
            </div>
          </div>
          <div class="dojo-hero-actions">
            <button class="dojo-btn dojo-btn-primary dojo-btn-lg" onclick="Dojo.showTrainDialog()">
              ⚡ New Lesson
            </button>
            <button class="dojo-btn dojo-btn-ghost" onclick="Dojo.refreshAll()">
              🔄 Refresh
            </button>
          </div>
        </div>

        <!-- Training Status Banner -->
        <div id="dojo-training-status" class="dojo-banner hidden"></div>

        <!-- Live Training Progress -->
        <div id="dojo-progress-panel" class="dojo-progress-panel hidden">
          <div class="dojo-progress-header">
            <div class="dojo-progress-title">
              <span class="dojo-pulse"></span>
              <span>Training in Progress</span>
            </div>
            <button class="dojo-btn dojo-btn-sm dojo-btn-danger" onclick="Dojo.stopTraining(Dojo._currentSymbol)">
              ⏹ Stop
            </button>
          </div>
          
          <div class="dojo-progress-bar-wrap">
            <div class="dojo-progress-bar" id="dojo-progress-bar"></div>
            <span class="dojo-progress-label" id="dojo-progress-label">0%</span>
          </div>
          
          <div class="dojo-progress-stats">
            <div class="dojo-stat">
              <span class="dojo-stat-label">Episode</span>
              <span class="dojo-stat-value" id="dojo-progress-episode">0 / 0</span>
            </div>
            <div class="dojo-stat">
              <span class="dojo-stat-label">Reward</span>
              <span class="dojo-stat-value profit" id="dojo-progress-reward">--</span>
            </div>
            <div class="dojo-stat">
              <span class="dojo-stat-label">P&L</span>
              <span class="dojo-stat-value" id="dojo-progress-pnl">--</span>
            </div>
            <div class="dojo-stat">
              <span class="dojo-stat-label">Win Rate</span>
              <span class="dojo-stat-value" id="dojo-progress-wr">--</span>
            </div>
            <div class="dojo-stat">
              <span class="dojo-stat-label">Skill</span>
              <span class="dojo-stat-value" id="dojo-progress-skill">--</span>
            </div>
            <div class="dojo-stat">
              <span class="dojo-stat-label">Trades</span>
              <span class="dojo-stat-value" id="dojo-progress-trades">0</span>
            </div>
            <div class="dojo-stat">
              <span class="dojo-stat-label">Sharpe</span>
              <span class="dojo-stat-value" id="dojo-progress-sharpe">--</span>
            </div>
            <div class="dojo-stat">
              <span class="dojo-stat-label">PF</span>
              <span class="dojo-stat-value" id="dojo-progress-pf">--</span>
            </div>
          </div>
          
          <div class="dojo-progress-footer">
            <span class="dojo-dim" id="dojo-speed">Speed: -- ep/s | ETA: --:--</span>
            <span class="dojo-dim" id="dojo-progress-time">Elapsed: 0s</span>
          </div>
          
          <!-- Episode History Sparkline -->
          <div class="dojo-sparkline-wrap">
            <span class="dojo-dim small">Last 50 Episodes</span>
            <div id="dojo-ep-history" class="dojo-sparkline"></div>
          </div>
        </div>

        <!-- Stats Overview -->
        <div class="dojo-stats-grid">
          <div class="dojo-stat-card">
            <div class="dojo-stat-card-icon">🏆</div>
            <div class="dojo-stat-card-content">
              <span class="dojo-stat-card-value" id="dojo-total-models">0</span>
              <span class="dojo-stat-card-label">Models Trained</span>
            </div>
          </div>
          <div class="dojo-stat-card">
            <div class="dojo-stat-card-icon">⚡</div>
            <div class="dojo-stat-card-content">
              <span class="dojo-stat-card-value" id="dojo-active-sessions">0</span>
              <span class="dojo-stat-card-label">Active Sessions</span>
            </div>
          </div>
          <div class="dojo-stat-card">
            <div class="dojo-stat-card-icon">📈</div>
            <div class="dojo-stat-card-content">
              <span class="dojo-stat-card-value" id="dojo-best-wr">--%</span>
              <span class="dojo-stat-card-label">Best Win Rate</span>
            </div>
          </div>
          <div class="dojo-stat-card">
            <div class="dojo-stat-card-icon">💰</div>
            <div class="dojo-stat-card-content">
              <span class="dojo-stat-card-value" id="dojo-best-pnl">--</span>
              <span class="dojo-stat-card-label">Best P&L</span>
            </div>
          </div>
        </div>

        <!-- Main Content Grid -->
        <div class="dojo-main-grid">
          <!-- Leaderboard -->
          <div class="dojo-card dojo-card-lg">
            <div class="dojo-card-header">
              <h3>📊 Leaderboard</h3>
              <div class="dojo-card-actions">
                <button class="dojo-btn dojo-btn-sm" onclick="Dojo.compareSelected()">Compare Selected</button>
                <select id="dojo-filter-skill" class="dojo-select-sm" onchange="Dojo.filterLeaderboard()">
                  <option value="">All Skills</option>
                  <option value="Master">🏆 Master</option>
                  <option value="Advanced">🥇 Advanced</option>
                  <option value="Intermediate">🥈 Intermediate</option>
                  <option value="Novice">🥉 Novice</option>
                  <option value="Beginner">🚲 Beginner</option>
                </select>
              </div>
            </div>
            <div id="dojo-leaderboard" class="dojo-leaderboard">
              <div class="dojo-loading">
                <div class="dojo-spinner"></div>
                Loading models...
              </div>
            </div>
            <div id="dojo-comparison" class="dojo-comparison hidden"></div>
          </div>

          <!-- Active Training -->
          <div class="dojo-card">
            <div class="dojo-card-header">
              <h3>🔥 Active Sessions</h3>
            </div>
            <div id="dojo-active-training" class="dojo-active-list">
              <div class="dojo-empty-state">
                <div class="dojo-empty-icon">🎯</div>
                <p>No active sessions</p>
                <button class="dojo-btn dojo-btn-sm dojo-btn-primary" onclick="Dojo.showTrainDialog()">Start Training</button>
              </div>
            </div>
          </div>

          <!-- Skill Distribution -->
          <div class="dojo-card">
            <div class="dojo-card-header">
              <h3>📈 Skill Distribution</h3>
            </div>
            <div id="dojo-skill-dist" class="dojo-skill-chart">
              <div class="dojo-loading">
                <div class="dojo-spinner"></div>
                Loading...
              </div>
            </div>
          </div>
        </div>

        <!-- Recursive Self-Improvement -->
        <div class="dojo-card dojo-card-full" id="dojo-self-improve">
          <div class="dojo-card-header">
            <h3>🧬 Recursive Self-Improvement</h3>
            <span class="dojo-dim small">SelfLearningLoop — mutate → multi-regime backtest → gatekeeper → promote</span>
            <div class="dojo-card-actions">
              <span class="dojo-dim small">every</span>
              <input id="si-interval" class="dojo-input si-interval" type="number" value="30" min="5" max="3600" step="5" title="Seconds between background iterations">
              <span class="dojo-dim small">s</span>
              <button id="si-start" class="dojo-btn dojo-btn-sm dojo-btn-primary" onclick="Dojo.siStart()">▶ Start</button>
              <button id="si-stop" class="dojo-btn dojo-btn-sm dojo-btn-danger" onclick="Dojo.siStop()">⏹ Stop</button>
              <button id="si-step" class="dojo-btn dojo-btn-sm" onclick="Dojo.siStep()">⏭ Step</button>
              <button class="dojo-btn dojo-btn-sm" onclick="Dojo.loadSelfImprove()">🔄</button>
            </div>
          </div>
          <div id="si-status" class="si-status">
            <div class="dojo-loading"><div class="dojo-spinner"></div> Loading self-improvement loop...</div>
          </div>
          <div id="si-history" class="si-history"></div>
        </div>

        <!-- Research Lab -->
        <div class="dojo-card dojo-card-full">
          <div class="dojo-card-header">
            <h3>🔬 Research Lab</h3>
            <span class="dojo-dim small">Evidence-based findings from training sessions</span>
          </div>
          <div id="dojo-research-lab" class="dojo-research-grid">
            <div class="dojo-loading">
              <div class="dojo-spinner"></div>
              Loading research findings...
            </div>
          </div>
        </div>

        <!-- Training Dialog -->
        <div id="dojo-train-dialog" class="dojo-modal hidden">
          <div class="dojo-modal-backdrop" onclick="Dojo.hideTrainDialog()"></div>
          <div class="dojo-modal-content dojo-modal-lg">
            <div class="dojo-modal-header">
              <h3>⚡ Start New Lesson</h3>
              <button class="dojo-modal-close" onclick="Dojo.hideTrainDialog()">✕</button>
            </div>
            <div class="dojo-modal-body">
              <div class="dojo-form-grid">
                <!-- Left Column: Strategy -->
                <div class="dojo-form-section">
                  <h4 class="dojo-form-section-title">Strategy</h4>
                  
                  <label class="dojo-label">Algorithm</label>
                  <select id="dojo-algorithm" class="dojo-select">
                    <option value="ppo" selected>🎯 PPO (Recommended)</option>
                    <option value="reinforce">📊 REINFORCE (Baseline)</option>
                  </select>

                  <div id="dojo-ppo-params">
                    <label class="dojo-label">Clip Epsilon</label>
                    <input type="number" id="dojo-clip" value="0.2" step="0.05" min="0.1" max="0.5" class="dojo-input">
                    
                    <label class="dojo-label">PPO Epochs</label>
                    <input type="number" id="dojo-ppo-epochs" value="4" step="1" min="1" max="10" class="dojo-input">
                  </div>

                  <label class="dojo-label">Learning Rate</label>
                  <input id="dojo-lr" type="number" value="0.001" min="0.0001" max="0.01" step="0.0001" class="dojo-input">

                  <label class="dojo-label">Entropy Bonus</label>
                  <input id="dojo-entropy" type="number" value="0.01" min="0" max="0.1" step="0.005" class="dojo-input">
                </div>

                <!-- Right Column: Market -->
                <div class="dojo-form-section">
                  <h4 class="dojo-form-section-title">Market</h4>
                  
                  <label class="dojo-label">Symbol</label>
                  <select id="dojo-symbol" class="dojo-select">
                    <optgroup label="Forex">
                      <option value="EURUSD">EUR/USD</option>
                      <option value="USDJPY">USD/JPY</option>
                      <option value="AUDUSD">AUD/USD</option>
                      <option value="GBPUSD">GBP/USD</option>
                      <option value="EURJPY">EUR/JPY</option>
                    </optgroup>
                    <optgroup label="Metals">
                      <option value="XAUUSD">Gold (XAU/USD)</option>
                      <option value="XAGUSD">Silver (XAG/USD)</option>
                    </optgroup>
                    <optgroup label="Indices">
                      <option value="SPX500">S&P 500</option>
                      <option value="JP225">Nikkei 225</option>
                    </optgroup>
                  </select>

                  <label class="dojo-label">Timeframe</label>
                  <select id="dojo-tf" class="dojo-select">
                    <option value="M15">M15 (15 min)</option>
                    <option value="H1" selected>H1 (1 hour)</option>
                    <option value="H4">H4 (4 hour)</option>
                  </select>

                  <label class="dojo-label">Episodes</label>
                  <input id="dojo-episodes" type="number" value="100" min="5" max="1000" class="dojo-input">

                  <label class="dojo-label">Spread (pips)</label>
                  <input id="dojo-spread" type="number" value="1.5" min="0" max="10" step="0.1" class="dojo-input">

                  <label class="dojo-label">Slippage (pips)</label>
                  <input id="dojo-slippage" type="number" value="0.5" min="0" max="5" step="0.1" class="dojo-input">
                </div>
              </div>
            </div>
            <div class="dojo-modal-footer">
              <button class="dojo-btn dojo-btn-ghost" onclick="Dojo.hideTrainDialog()">Cancel</button>
              <button class="dojo-btn dojo-btn-primary" onclick="Dojo.startTraining()">
                ⚡ Start Training
              </button>
            </div>
          </div>
        </div>

        <!-- Model Detail Modal -->
        <div id="dojo-model-modal" class="dojo-modal hidden">
          <div class="dojo-modal-backdrop" onclick="Dojo.hideModelModal()"></div>
          <div class="dojo-modal-content dojo-modal-xl">
            <div class="dojo-modal-header">
              <h3 id="dojo-model-title">Model Details</h3>
              <button class="dojo-modal-close" onclick="Dojo.hideModelModal()">✕</button>
            </div>
            <div id="dojo-model-body" class="dojo-modal-body">
              <div class="dojo-loading">
                <div class="dojo-spinner"></div>
                Loading model data...
              </div>
            </div>
            <div class="dojo-modal-footer">
              <button id="dojo-walk-forward" class="dojo-btn dojo-btn-primary">
                📊 Walk-Forward Test
              </button>
              <button id="dojo-delete-model" class="dojo-btn dojo-btn-danger">
                🗑️ Delete Model
              </button>
            </div>
            <div id="dojo-wf-result" class="dojo-wf-result hidden"></div>
          </div>
        </div>
      </div>
    `;
  }

  // ── Self-improvement controls ─────────────────────────────────────────
  async function siStart() {
    const btn = document.getElementById('si-start');
    try {
      if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
      const interval = Number((document.getElementById('si-interval') || {}).value || 30) || 30;
      const r = await _postBody(`${API}/api/dojo/self-improve/start`, { interval_s: interval });
      toast(r.status === 'started' ? `Self-improvement started (every ${interval}s)` : `Not started: ${r.status}`,
            r.status === 'started' ? 'up' : 'error');
    } catch (e) {
      toast(e.message || 'Start failed', 'error');
    } finally {
      if (btn) btn.textContent = '▶ Start';
      loadSelfImprove();
    }
  }

  async function siStop() {
    const btn = document.getElementById('si-stop');
    try {
      if (btn) { btn.disabled = true; btn.textContent = 'Stopping…'; }
      const r = await _post(`${API}/api/dojo/self-improve/stop`);
      toast(`Self-improvement ${r.status || 'stopped'}`, 'info');
    } catch (e) {
      toast(e.message || 'Stop failed', 'error');
    } finally {
      if (btn) btn.textContent = '⏹ Stop';
      loadSelfImprove();
    }
  }

  async function siStep() {
    const btn = document.getElementById('si-step');
    try {
      if (btn) { btn.disabled = true; btn.textContent = 'Working… ⏳'; }
      const r = await _postBody(`${API}/api/dojo/self-improve/step`, {});
      if (r.status === 'error') toast(`Iteration failed: ${r.error}`, 'error');
      else toast(`Iteration #${r.iteration}: ${r.decision}${r.improved ? ' ✅ improved' : ''}`,
                 r.decision === 'promote' ? 'up' : 'info');
    } catch (e) {
      toast(e.message || 'Step failed', 'error');
    } finally {
      if (btn) btn.textContent = '⏭ Step';
      loadSelfImprove();
    }
  }

  // ── Load leaderboard ──────────────────────────────────────────────────
  async function loadLeaderboard() {
    try {
      const data = await _get(`${API}/api/dojo/leaderboard`);
      const el = document.getElementById('dojo-leaderboard');
      
      if (!data.leaderboard || data.leaderboard.length === 0) {
        el.innerHTML = `
          <div class="dojo-empty-state">
            <div class="dojo-empty-icon">📚</div>
            <p>No trained models yet</p>
            <button class="dojo-btn dojo-btn-sm dojo-btn-primary" onclick="Dojo.showTrainDialog()">Start First Lesson</button>
          </div>`;
        return;
      }

      // Update stats
      document.getElementById('dojo-total-models').textContent = data.leaderboard.length;
      
      const bestWr = Math.max(...data.leaderboard.map(m => m.win_rate || 0));
      const bestPnl = Math.max(...data.leaderboard.map(m => m.total_pnl || 0));
      document.getElementById('dojo-best-wr').textContent = bestWr.toFixed(1) + '%';
      document.getElementById('dojo-best-pnl').textContent = formatCurrency(bestPnl);

      let html = `
        <table class="dojo-table">
          <thead>
            <tr>
              <th style="width:30px"><input type="checkbox" id="dojo-select-all" onclick="Dojo.toggleSelectAll(this)"/></th>
              <th style="width:40px">#</th>
              <th>Symbol</th>
              <th>TF</th>
              <th>Skill</th>
              <th>Win Rate</th>
              <th>P&L</th>
              <th>Sharpe</th>
              <th>PF</th>
              <th>Episodes</th>
              <th style="width:80px">Actions</th>
            </tr>
          </thead>
          <tbody>`;
      
      data.leaderboard.forEach((m, i) => {
        const skill = SKILL_CONFIG[m.skill_level] || SKILL_CONFIG['Unknown'];
        const wr = (m.win_rate || 0).toFixed(1);
        const pnl = m.total_pnl || 0;
        const pnlClass = pnl >= 0 ? 'profit' : 'loss';
        
        html += `
          <tr class="dojo-row" data-skill="${m.skill_level || 'Unknown'}" onclick="Dojo.showModelDetail('${m.symbol}', '${m.timeframe}', ${m.episodes})">
            <td><input type="checkbox" class="dojo-select" data-idx="${i}" onclick="event.stopPropagation()"/></td>
            <td class="dojo-rank">${i + 1}</td>
            <td class="dojo-symbol">${m.symbol}</td>
            <td class="dojo-mono">${m.timeframe}</td>
            <td>
              <span class="dojo-skill-badge" style="background:${skill.bg};color:${skill.color}">
                ${skill.icon} ${m.skill_level || 'Unknown'}
              </span>
            </td>
            <td class="dojo-mono">${wr}%</td>
            <td class="dojo-mono ${pnlClass}">${formatCurrency(pnl)}</td>
            <td class="dojo-mono">${formatNumber(m.sharpe_ratio)}</td>
            <td class="dojo-mono">${formatNumber(m.profit_factor)}</td>
            <td class="dojo-mono">${m.episodes}</td>
            <td>
              <button class="dojo-btn dojo-btn-xs" onclick="event.stopPropagation(); Dojo.showModelDetail('${m.symbol}', '${m.timeframe}', ${m.episodes})">View</button>
            </td>
          </tr>`;
      });
      
      html += '</tbody></table>';
      el.innerHTML = html;
      
    } catch (e) {
      console.error('Failed to load leaderboard:', e);
      document.getElementById('dojo-leaderboard').innerHTML = `
        <div class="dojo-error-state">
          <p>Failed to load leaderboard</p>
          <button class="dojo-btn dojo-btn-sm" onclick="Dojo.loadLeaderboard()">Retry</button>
        </div>`;
    }
  }

  // ── Load active training ──────────────────────────────────────────────
  async function loadActiveTraining() {
    try {
      const data = await _get(`${API}/api/dojo/status`);
      const el = document.getElementById('dojo-active-training');
      const statusEl = document.getElementById('dojo-training-status');
      const progressEl = document.getElementById('dojo-progress-panel');
      
      const activeSymbols = data.active ? Object.entries(data.active).filter(([k,v]) => v).map(([k]) => k) : [];
      
      if (activeSymbols.length === 0) {
        el.innerHTML = `
          <div class="dojo-empty-state">
            <div class="dojo-empty-icon">🎯</div>
            <p>No active sessions</p>
            <button class="dojo-btn dojo-btn-sm dojo-btn-primary" onclick="Dojo.showTrainDialog()">Start Training</button>
          </div>`;
        statusEl.classList.add('hidden');
        progressEl.classList.add('hidden');
        return;
      }

      document.getElementById('dojo-active-sessions').textContent = activeSymbols.length;
      
      let html = '';
      for (const sym of activeSymbols) {
        const s = await _get(`${API}/api/dojo/live/${sym}`);
        const skill = SKILL_CONFIG[s.skill_level] || SKILL_CONFIG['Unknown'];
        html += `
          <div class="dojo-session-card">
            <div class="dojo-session-header">
              <span class="dojo-session-symbol">${s.symbol}</span>
              <span class="dojo-skill-badge" style="background:${skill.bg};color:${skill.color}">${skill.icon} ${s.skill_level || 'Unknown'}</span>
            </div>
            <div class="dojo-session-stats">
              <span>Episode ${s.episode || 0}/${s.total_episodes || '?'}</span>
              <span>WR: ${(s.win_rate || 0).toFixed(1)}%</span>
              <span>PnL: ${formatCurrency(s.pnl || 0)}</span>
            </div>
            <button class="dojo-btn dojo-btn-xs dojo-btn-danger" onclick="Dojo.stopTraining('${s.symbol}')">Stop</button>
          </div>`;
        
        _currentSymbol = s.symbol;
        _updateProgress(s);
      }
      
      el.innerHTML = html;
      statusEl.classList.remove('hidden');
      progressEl.classList.remove('hidden');
      
    } catch (e) {
      console.error('Failed to load active training:', e);
    }
  }

  function _updateProgress(session) {
    const pct = session.total_episodes ? ((session.episode || 0) / session.total_episodes * 100) : 0;
    document.getElementById('dojo-progress-bar').style.width = pct + '%';
    document.getElementById('dojo-progress-label').textContent = pct.toFixed(0) + '%';
    document.getElementById('dojo-progress-episode').textContent = `${session.episode || 0} / ${session.total_episodes || '?'}`;
    document.getElementById('dojo-progress-reward').textContent = formatNumber(session.avg_reward);
    document.getElementById('dojo-progress-pnl').textContent = formatCurrency(session.total_pnl);
    document.getElementById('dojo-progress-wr').textContent = (session.win_rate || 0).toFixed(1) + '%';
    document.getElementById('dojo-progress-skill').textContent = session.skill_level || 'Unknown';
    document.getElementById('dojo-progress-trades').textContent = session.total_trades || 0;
    document.getElementById('dojo-progress-sharpe').textContent = formatNumber(session.sharpe_ratio);
    document.getElementById('dojo-progress-pf').textContent = formatNumber(session.profit_factor);
    document.getElementById('dojo-speed').textContent = `Speed: ${(session.episodes_per_sec || 0).toFixed(1)} ep/s | ETA: ${formatEta(session.eta_seconds)}`;
    document.getElementById('dojo-progress-time').textContent = `Elapsed: ${session.elapsed || '0s'}`;
  }

  // ── Load skill distribution ───────────────────────────────────────────
  async function loadSkillDist() {
    try {
      const data = await _get(`${API}/api/dojo/leaderboard`);
      const el = document.getElementById('dojo-skill-dist');
      
      if (!data.leaderboard || data.leaderboard.length === 0) {
        el.innerHTML = '<div class="dojo-dim" style="padding:20px;text-align:center">No data yet</div>';
        return;
      }

      const counts = {};
      data.leaderboard.forEach(m => {
        const skill = m.skill_level || 'Unknown';
        counts[skill] = (counts[skill] || 0) + 1;
      });

      const total = data.leaderboard.length;
      let html = '<div class="dojo-skill-bars">';
      
      Object.entries(SKILL_CONFIG).forEach(([skill, config]) => {
        const count = counts[skill] || 0;
        const pct = total > 0 ? (count / total * 100) : 0;
        if (count > 0) {
          html += `
            <div class="dojo-skill-bar-row">
              <span class="dojo-skill-bar-label">${config.icon} ${skill}</span>
              <div class="dojo-skill-bar-track">
                <div class="dojo-skill-bar-fill" style="width:${pct}%;background:${config.color}"></div>
              </div>
              <span class="dojo-skill-bar-count">${count}</span>
            </div>`;
        }
      });
      
      html += '</div>';
      el.innerHTML = html;
      
    } catch (e) {
      console.error('Failed to load skill distribution:', e);
    }
  }

  // ── Recursive self-improvement (trading_ai SelfLearningLoop) ──────────
  let _siRunning = false;

  function _siBadge(decision) {
    const d = String(decision || '').toLowerCase();
    let color = '#94a3b8', bg = 'rgba(148,163,184,0.12)';
    if (d === 'promote') { color = '#00d4aa'; bg = 'rgba(0,212,170,0.12)'; }
    else if (d === 'reject') { color = '#ef4444'; bg = 'rgba(239,68,68,0.12)'; }
    else if (d === 'continue_paper' || d === 'need_more_data') { color = '#f59e0b'; bg = 'rgba(245,158,11,0.12)'; }
    else if (d === 'blocked' || d === 'error') { color = '#ef4444'; bg = 'rgba(239,68,68,0.18)'; }
    return `<span class="dojo-skill-badge" style="background:${bg};color:${color}">${decision || '--'}</span>`;
  }

  function _siChip(label, value) {
    return `<div class="si-chip"><span class="si-chip-label">${label}</span><span class="si-chip-value">${value}</span></div>`;
  }

  function _siRenderStatus(st) {
    _siRunning = !!st.running;
    const safety = st.safety || {};
    const limits = safety.limits || {};
    const best = st.best_metrics || {};
    const running = st.running
      ? '<span class="dojo-skill-badge" style="background:rgba(0,212,170,0.12);color:#00d4aa">▶ running</span>'
      : '<span class="dojo-skill-badge" style="background:rgba(148,163,184,0.12);color:#94a3b8">⏸ stopped</span>';
    const phase = safety.phase
      ? `<span class="dojo-skill-badge" style="background:rgba(139,92,246,0.12);color:#8b5cf6">${safety.phase}</span>` : '--';
    const safetyAlert = safety.stopped
      ? `<div class="si-alert">🛑 Safety stopped: ${safety.stop_reason || 'unknown'} — reset required</div>` : '';

    const paper = st.paper_pending || [], paperOk = st.paper_approved || [], liveOk = st.live_approved || [];
    const deployment = st.deployment || {};
    const depBadges = [
      paper.length ? `<span class="dojo-skill-badge" style="background:rgba(245,158,11,0.12);color:#f59e0b">📝 awaiting paper: ${paper.length}</span>` : '',
      paperOk.length ? `<span class="dojo-skill-badge" style="background:rgba(59,130,246,0.12);color:#3b82f6">✅ paper approved: ${paperOk.length}</span>` : '',
      liveOk.length ? `<span class="dojo-skill-badge" style="background:rgba(0,212,170,0.12);color:#00d4aa">🚀 live approved: ${liveOk.length}</span>` : '',
      ...Object.entries(deployment).map(([sid, r]) =>
        `<span class="dojo-skill-badge" style="background:rgba(139,92,246,0.12);color:#8b5cf6">${String(sid).slice(0, 22)}: ${(r && r.stage) || '?'}</span>`),
    ].filter(Boolean).join(' ');

    const credits = st.mutation_credits || [];
    const creditsHtml = credits.length ? `
      <div class="si-sub"><span class="dojo-dim small">🧠 Mutation credit ledger (meta-learned weights)</span></div>
      <table class="dojo-table si-table">
        <thead><tr><th>Mutation</th><th>Attempts</th><th>Success rate</th><th>Avg ΔPF</th><th>Weight</th></tr></thead>
        <tbody>${credits.map(c => `<tr>
          <td>${c.mutation_type || '--'}</td>
          <td>${c.attempts ?? 0}</td>
          <td>${c.success_rate != null ? (100 * c.success_rate).toFixed(0) + '%' : '--'}</td>
          <td class="${(c.avg_delta_pf || 0) >= 0 ? 'si-good' : 'si-bad'}">${c.avg_delta_pf != null ? ((c.avg_delta_pf >= 0 ? '+' : '') + formatNumber(c.avg_delta_pf, 3)) : '--'}</td>
          <td>${formatNumber(c.weight, 2)}</td>
        </tr>`).join('')}</tbody>
      </table>` : '<div class="dojo-dim small">No mutation credits yet — run an iteration.</div>';

    const last = st.last_iteration;
    const lastHtml = last ? `
      <div class="si-sub"><span class="dojo-dim small">Last iteration:</span> ${_siBadge(last.decision)}
        <span class="dojo-dim small">#${last.iteration} · ${last.mutation_type || '?'} · PF ${last.parent_pf != null ? formatNumber(last.parent_pf, 2) : '--'} → ${last.cand_pf != null ? formatNumber(last.cand_pf, 2) : '--'}${last.error ? ` · <span class="si-err">${last.error}</span>` : ''}</span>
      </div>` : '';

    document.getElementById('si-status').innerHTML = `
      ${safetyAlert}
      <div class="si-chips">
        ${_siChip('State', running)}
        ${_siChip('Phase', phase)}
        ${_siChip('Iterations', st.iterations_completed ?? 0)}
        ${_siChip('Promotions', `<span class="si-good">${st.promotions ?? 0}</span>`)}
        ${_siChip('Rejections', `<span class="si-bad">${st.rejections ?? 0}</span>`)}
        ${_siChip('Errors', st.errors ?? 0)}
        ${_siChip('Population', st.population_size ?? 0)}
        ${_siChip('Best PF', best.profit_factor != null ? formatNumber(best.profit_factor, 2) : '--')}
        ${_siChip('Best genome', st.best_genome_id ? String(st.best_genome_id).slice(0, 18) : '--')}
        ${_siChip('Stagnation', `${safety.stagnation ?? 0}/${limits.max_stagnation ?? '--'}`)}
        ${_siChip('Peak PF', safety.peak_best_pf != null ? formatNumber(safety.peak_best_pf, 3) : '--')}
        ${_siChip('Daily iters', `${safety.daily_iterations ?? 0}/${limits.max_daily_iterations ?? '--'}`)}
      </div>
      ${depBadges ? `<div class="si-sub"><span class="dojo-dim small">Deployment:</span> ${depBadges}</div>` : ''}
      ${lastHtml}
      ${creditsHtml}
    `;
  }

  function _siRenderHistory(rows) {
    const el = document.getElementById('si-history');
    if (!el) return;
    if (!rows.length) {
      el.innerHTML = '<div class="dojo-empty-state"><div class="dojo-empty-icon">🌱</div><p>No iterations yet — press ⏭ Step to run one, or ▶ Start the loop</p></div>';
      return;
    }
    el.innerHTML = `
      <div class="si-sub"><span class="dojo-dim small">Iteration history (latest ${rows.length})</span></div>
      <table class="dojo-table si-table">
        <thead><tr><th>#</th><th>When</th><th>Decision</th><th>Mutation</th><th>Phase</th><th>Parent PF</th><th>Candidate PF</th><th>OOS</th><th>Duration</th><th>Error</th></tr></thead>
        <tbody>${rows.slice().reverse().map(h => `<tr>
          <td>${h.iteration ?? '--'}</td>
          <td>${h.at ? String(h.at).slice(11, 19) : '--'}</td>
          <td>${_siBadge(h.decision)}${h.improved ? ' ✅' : ''}</td>
          <td>${h.mutation_type || '--'}</td>
          <td>${h.phase || '--'}</td>
          <td>${h.parent_pf != null ? formatNumber(h.parent_pf, 2) : '--'}</td>
          <td class="${h.cand_pf != null && h.parent_pf != null && h.cand_pf >= h.parent_pf ? 'si-good' : ''}">${h.cand_pf != null ? formatNumber(h.cand_pf, 2) : '--'}</td>
          <td>${h.oos_passed == null ? '--' : (h.oos_passed ? '✅' : '❌')}</td>
          <td>${h.duration_s != null ? h.duration_s + 's' : '--'}</td>
          <td class="si-err">${h.error ? String(h.error).slice(0, 60) : ''}</td>
        </tr>`).join('')}</tbody>
      </table>`;
  }

  async function loadSelfImprove() {
    const stEl = document.getElementById('si-status');
    if (!stEl) return; // dojo section not rendered yet
    try {
      const [st, hist] = await Promise.all([
        _get(`${API}/api/dojo/self-improve/status`),
        _get(`${API}/api/dojo/self-improve/history?limit=30`),
      ]);
      _siRenderStatus(st);
      _siRenderHistory((hist && hist.history) || []);
      const stepBtn = document.getElementById('si-step');
      const startBtn = document.getElementById('si-start');
      const stopBtn = document.getElementById('si-stop');
      if (stepBtn) stepBtn.disabled = _siRunning;
      if (startBtn) startBtn.disabled = _siRunning;
      if (stopBtn) stopBtn.disabled = !_siRunning;
    } catch (e) {
      stEl.innerHTML = `<div class="dojo-empty-state"><p>Self-improvement API unavailable: ${e.message}</p></div>`;
    }
  }

  // ── Load research lab ─────────────────────────────────────────────────
  async function loadResearchLab() {
    try {
      const data = await _get(`${API}/api/dojo/research`);
      const el = document.getElementById('dojo-research-lab');
      
      // Backend returns structured findings, not an array
      const findings = [];
      
      if (data.rl_verdict) {
        findings.push({
          type: 'RL Verdict',
          title: 'Reinforcement Learning Status',
          summary: data.rl_verdict.summary,
          detail: data.rl_verdict.detail,
          metrics: data.rl_verdict.honest_baseline,
        });
      }
      
      if (data.price_action_verdict) {
        findings.push({
          type: 'Price Action',
          title: 'Price Action Strategy Validation',
          summary: data.price_action_verdict.summary,
          detail: data.price_action_verdict.detail,
        });
        
        if (data.price_action_verdict.jp225) {
          findings.push({
            type: 'JP225 Deployed',
            title: 'JP225 — The Only Winner',
            summary: data.price_action_verdict.jp225.stats,
            metrics: data.price_action_verdict.jp225,
          });
        }
      }
      
      if (data.deriv_verdict) {
        findings.push({
          type: 'Deriv Edge',
          title: 'Spike-Drift Strategy',
          summary: data.deriv_verdict.summary,
          metrics: data.deriv_verdict,
        });
      }
      
      if (findings.length === 0) {
        el.innerHTML = '<div class="dojo-dim" style="padding:20px;text-align:center">No research findings yet</div>';
        return;
      }

      let html = '<div class="dojo-research-grid">';
      findings.forEach(f => {
        html += `
          <div class="dojo-research-card">
            <div class="dojo-research-header">
              <span class="dojo-research-type">${f.type || 'Finding'}</span>
            </div>
            <h4 class="dojo-research-title">${f.title || 'Untitled'}</h4>
            <p class="dojo-research-summary">${f.summary || ''}</p>
            ${f.detail ? `<p class="dojo-research-summary" style="font-size:0.8rem;margin-top:8px;color:var(--text-3)">${f.detail.substring(0, 200)}...</p>` : ''}
          </div>`;
      });
      html += '</div>';
      el.innerHTML = html;
      
    } catch (e) {
      console.error('Failed to load research lab:', e);
    }
  }

  // ── Show model detail ─────────────────────────────────────────────────
  async function showModelDetail(symbol, timeframe, episodes) {
    const modal = document.getElementById('dojo-model-modal');
    const body = document.getElementById('dojo-model-body');
    const title = document.getElementById('dojo-model-title');
    
    title.textContent = `${symbol} ${timeframe} — ${episodes} episodes`;
    body.innerHTML = '<div class="dojo-loading"><div class="dojo-spinner"></div>Loading...</div>';
    modal.classList.remove('hidden');
    
    try {
      const data = await _get(`${API}/api/dojo/model/${symbol}?timeframe=${timeframe}`);
      
      let html = `
        <div class="dojo-model-grid">
          <div class="dojo-model-section">
            <h4>Performance</h4>
            <div class="dojo-model-stats">
              <div class="dojo-model-stat">
                <span class="dojo-dim">Win Rate</span>
                <span class="dojo-bold">${(data.win_rate || 0).toFixed(1)}%</span>
              </div>
              <div class="dojo-model-stat">
                <span class="dojo-dim">Total P&L</span>
                <span class="dojo-bold ${data.pnl >= 0 ? 'profit' : 'loss'}">${formatCurrency(data.pnl)}</span>
              </div>
              <div class="dojo-model-stat">
                <span class="dojo-dim">Sharpe Ratio</span>
                <span class="dojo-bold">${formatNumber(data.sharpe_ratio)}</span>
              </div>
              <div class="dojo-model-stat">
                <span class="dojo-dim">Profit Factor</span>
                <span class="dojo-bold">${formatNumber(data.profit_factor)}</span>
              </div>
              <div class="dojo-model-stat">
                <span class="dojo-dim">Max Drawdown</span>
                <span class="dojo-bold loss">${formatNumber(data.max_drawdown)}%</span>
              </div>
              <div class="dojo-model-stat">
                <span class="dojo-dim">Skill Level</span>
                <span class="dojo-bold">${data.skill_level || 'Unknown'}</span>
              </div>
            </div>
          </div>
          <div class="dojo-model-section">
            <h4>Parameters</h4>
            <div class="dojo-model-params">
              <div class="dojo-param"><span class="dojo-dim">Algorithm</span><span>${data.algorithm || 'PPO'}</span></div>
              <div class="dojo-param"><span class="dojo-dim">Learning Rate</span><span>${data.learning_rate || 0.001}</span></div>
              <div class="dojo-param"><span class="dojo-dim">Clip Epsilon</span><span>${data.clip_epsilon || 0.2}</span></div>
              <div class="dojo-param"><span class="dojo-dim">Entropy</span><span>${data.entropy_coeff || 0.01}</span></div>
            </div>
          </div>
        </div>`;
      
      body.innerHTML = html;
      
      // Setup delete button
      document.getElementById('dojo-delete-model').onclick = async () => {
        if (confirm('Delete this model?')) {
          await _del(`${API}/api/dojo/model/${symbol}?timeframe=${timeframe}`);
          modal.classList.add('hidden');
          Dojo.refreshAll();
        }
      };
      
      // Setup walk-forward button
      document.getElementById('dojo-walk-forward').onclick = async () => {
        const wfResult = document.getElementById('dojo-wf-result');
        wfResult.classList.remove('hidden');
        wfResult.innerHTML = '<div class="dojo-loading"><div class="dojo-spinner"></div>Running walk-forward test...</div>';
        try {
          const result = await _post(`${API}/api/dojo/walk-forward?symbol=${symbol}&timeframe=${timeframe}&episodes=${episodes || 30}&n_windows=3`);
          wfResult.innerHTML = `<pre>${JSON.stringify(result, null, 2)}</pre>`;
        } catch (e) {
          wfResult.innerHTML = `<div class="dojo-error">Walk-forward failed: ${e.message}</div>`;
        }
      };
      
    } catch (e) {
      body.innerHTML = `<div class="dojo-error">Failed to load model: ${e.message}</div>`;
    }
  }

  function hideModelModal() {
    document.getElementById('dojo-model-modal').classList.add('hidden');
  }

  // ── Training controls ─────────────────────────────────────────────────
  function showTrainDialog() {
    document.getElementById('dojo-train-dialog').classList.remove('hidden');
  }

  function hideTrainDialog() {
    document.getElementById('dojo-train-dialog').classList.add('hidden');
  }

  async function startTraining() {
    const symbol = document.getElementById('dojo-symbol').value;
    const timeframe = document.getElementById('dojo-tf').value;
    const episodes = parseInt(document.getElementById('dojo-episodes').value);
    const learning_rate = parseFloat(document.getElementById('dojo-lr').value);
    const spread_pips = parseFloat(document.getElementById('dojo-spread').value);
    const slippage_pips = parseFloat(document.getElementById('dojo-slippage').value);
    const entropy_coeff = parseFloat(document.getElementById('dojo-entropy').value);
    const algorithm = document.getElementById('dojo-algorithm').value;
    
    let url = `${API}/api/dojo/train?symbol=${symbol}&timeframe=${timeframe}&episodes=${episodes}&learning_rate=${learning_rate}&spread_pips=${spread_pips}&slippage_pips=${slippage_pips}&entropy_coeff=${entropy_coeff}&algorithm=${algorithm}`;
    
    if (algorithm === 'ppo') {
      const clip_epsilon = parseFloat(document.getElementById('dojo-clip').value);
      const ppo_epochs = parseInt(document.getElementById('dojo-ppo-epochs').value);
      url += `&clip_epsilon=${clip_epsilon}&ppo_epochs=${ppo_epochs}`;
    }
    
    try {
      await _post(url);
      hideTrainDialog();
      refreshAll();
    } catch (e) {
      alert('Failed to start training: ' + e.message);
    }
  }

  async function stopTraining(symbol) {
    try {
      await _del(`${API}/api/dojo/stop/${symbol}`);
      refreshAll();
    } catch (e) {
      alert('Failed to stop training: ' + e.message);
    }
  }

  // ── Compare selected ──────────────────────────────────────────────────
  function compareSelected() {
    const checked = document.querySelectorAll('.dojo-select:checked');
    if (checked.length < 2) {
      alert('Select at least 2 models to compare');
      return;
    }
    // TODO: Implement comparison view
    alert('Comparison feature coming soon!');
  }

  function toggleSelectAll(el) {
    document.querySelectorAll('.dojo-select').forEach(cb => {
      cb.checked = el.checked;
    });
  }

  function filterLeaderboard() {
    const skill = document.getElementById('dojo-filter-skill').value;
    document.querySelectorAll('.dojo-row').forEach(row => {
      if (!skill || row.dataset.skill === skill) {
        row.style.display = '';
      } else {
        row.style.display = 'none';
      }
    });
  }

  // ── Refresh all ───────────────────────────────────────────────────────
  function refreshAll() {
    loadLeaderboard();
    loadActiveTraining();
    loadSkillDist();
    loadResearchLab();
    loadSelfImprove();
  }

  // ── Init ──────────────────────────────────────────────────────────────
  function init() {
    refreshAll();
    
    // Auto-refresh every 10 seconds if there are active sessions
    let siTick = 0;
    setInterval(async () => {
      try {
        const data = await _get(`${API}/api/dojo/status`);
        const activeSymbols = data.active ? Object.values(data.active).some(v => v) : false;
        if (activeSymbols) {
          loadActiveTraining();
        }
      } catch (e) { /* silent */ }
      // Self-improvement panel: live refresh while the loop runs, else every minute
      siTick += 1;
      if (_siRunning || siTick % 6 === 0) loadSelfImprove();
    }, 10000);
  }

  return {
    render,
    init,
    refreshAll,
    showTrainDialog,
    hideTrainDialog,
    startTraining,
    stopTraining,
    showModelDetail,
    hideModelModal,
    compareSelected,
    toggleSelectAll,
    filterLeaderboard,
    loadLeaderboard,
    loadActiveTraining,
    loadSkillDist,
    loadResearchLab,
    loadSelfImprove,
    siStart,
    siStop,
    siStep,
    _currentSymbol,
  };
})();
