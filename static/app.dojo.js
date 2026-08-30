/* ═══════════════════════════════════════════════════════════════════════════
   FOREX DOJO — "Kid Learning to Ride a Bike"
   RL Training Dashboard
   ═══════════════════════════════════════════════════════════════════════════ */

const Dojo = (() => {
  const API = '';  // same origin
  let pollTimer = null;
  let _currentSymbol = null;
  let _livePollTimer = null;

  // ── Authenticated request helpers ────────────────────────────────────
  // The API is behind the same X-Auth-Token header used by app.core.js's
  // api()/apiPost() helpers; a plain fetch() gets a 401 and the dojo panels
  // would stay stuck on "Loading...".
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

  async function _del(path) {
    const headers = AUTH_TOKEN ? { 'X-Auth-Token': AUTH_TOKEN } : {};
    const res = await fetch(path, { method: 'DELETE', headers });
    return _unwrap(res);
  }

  // ── Skill level colors ────────────────────────────────────────────────
  const SKILL_COLORS = {
    'Master':      '#4caf50',
    'Advanced':    '#2196f3',
    'Intermediate':'#ff9800',
    'Novice':      '#9c27b0',
    'Beginner':    '#f44336',
    'Unknown':     '#666',
  };

  const SKILL_ICONS = {
    'Master':      '🏆',
    'Advanced':    '🥇',
    'Intermediate':'🥈',
    'Novice':      '🥉',
    'Beginner':    '🚲',
    'Unknown':     '❓',
  };

  function formatEta(seconds) {
    if (!seconds || seconds <= 0) return '--:--';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m + ':' + String(s).padStart(2, '0');
  }

  // ── Main render ───────────────────────────────────────────────────────
  function render() {
    return `
      <div class="dojo-container">
        <div class="dojo-header">
          <h2>🥋 ForexDojo <span class="dojo-subtitle">"Kid Learning to Ride a Bike"</span></h2>
          <div class="dojo-actions">
            <button class="dojo-btn dojo-btn-primary" onclick="Dojo.showTrainDialog()">
              ⚡ Quick Lesson
            </button>
            <button class="dojo-btn" onclick="Dojo.refreshModels()">🔄 Refresh</button>
          </div>
        </div>

                <div id="dojo-training-status" class="dojo-training-banner hidden"></div>

        <!-- Live Training Progress Panel -->
        <div id="dojo-progress-panel" class="dojo-card hidden" style="margin-bottom:16px;">
          <h3>Live Training Progress</h3>
          <div class="dojo-progress-body">
            <div class="dojo-progress-bar-container" style="width:100%;height:24px;background:#1a1a2e;border-radius:12px;overflow:hidden;margin:8px 0;position:relative;">
              <div class="dojo-progress-bar" id="dojo-progress-bar" style="height:100%;background:linear-gradient(90deg,#00d4ff,#7c3aed);border-radius:12px;transition:width 0.3s;width:0%"></div>
              <div class="dojo-progress-label" id="dojo-progress-label" style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;font-weight:600;">0%</div>
            </div>
            <div class="dojo-speed" id="dojo-speed" style="font-size:11px;color:#888;margin-top:4px;">
              Speed: -- ep/s | ETA: --:--
            </div>
            <div class="dojo-progress-row">
              <span class="dojo-dim small">Episode</span>
              <span id="dojo-progress-episode" class="dojo-bold">0 / 0</span>
            </div>
            <div class="dojo-metrics-grid">
              <div class="dojo-metric"><span class="dojo-dim small">Reward</span><span id="dojo-progress-reward">--</span></div>
              <div class="dojo-metric"><span class="dojo-dim small">PnL</span><span id="dojo-progress-pnl">--</span></div>
              <div class="dojo-metric"><span class="dojo-dim small">Win Rate</span><span id="dojo-progress-wr">--</span></div>
              <div class="dojo-metric"><span class="dojo-dim small">Skill</span><span id="dojo-progress-skill">--</span></div>
              <div class="dojo-metric"><span class="dojo-dim small">Trades</span><span id="dojo-progress-trades">0</span></div>
              <div class="dojo-metric"><span class="dojo-dim small">Sharpe</span><span id="dojo-progress-sharpe">—</span></div>
              <div class="dojo-metric"><span class="dojo-dim small">PF</span><span id="dojo-progress-pf">—</span></div>
              <div class="dojo-metric"><span class="dojo-dim small">Elapsed</span><span id="dojo-progress-time">0s</span></div>
            </div>
            <div class="dojo-episode-history" id="dojo-episode-history" style="margin-top:8px;">
              <div style="font-size:11px;color:#888;margin-bottom:4px;">Recent Episodes</div>
              <div id="dojo-ep-history" style="display:flex;gap:2px;align-items:flex-end;height:40px;"></div>
            </div>
            <div class="dojo-dialog-actions">
              <button class="dojo-btn dojo-btn-sm dojo-btn-ghost" onclick="Dojo.stopTraining(Dojo._currentSymbol)">Stop Training</button>
            </div>
          </div>
        </div>

        <div class="dojo-grid">
          <!-- Leaderboard -->
          <div class="dojo-card dojo-card-wide">
            <h3>📊 Leaderboard <button id="dojo-compare-btn" class="dojo-btn" style="margin-left:8px;">Compare Selected</button></h3>
            <div id="dojo-leaderboard" class="dojo-leaderboard">
              <div class="dojo-loading">Loading...</div>
            </div>
            <div id="dojo-comparison" style="display:none; margin-top:12px;"></div>
          </div>

          <!-- Active Training -->
          <div class="dojo-card">
            <h3>🔥 Active Training</h3>
            <div id="dojo-active-training" class="dojo-active-training">
              <div class="dojo-empty">No active sessions</div>
            </div>
          </div>

          <!-- Skill Distribution -->
          <div class="dojo-card">
            <h3>📈 Skill Distribution</h3>
            <div id="dojo-skill-dist" class="dojo-skill-dist">
              <div class="dojo-loading">Loading...</div>
            </div>
          </div>
        </div>

        <!-- Training Dialog -->
        <div id="dojo-train-dialog" class="dojo-dialog hidden">
          <div class="dojo-dialog-content">
            <h3>⚡ Start Training</h3>
            <div class="dojo-form">
              <label>Algorithm</label>
              <select id="dojo-algorithm" class="dojo-input">
                <option value="ppo" selected>PPO (Recommended)</option>
                <option value="reinforce">REINFORCE (Baseline)</option>
              </select>

              <div id="dojo-ppo-params">
                <label>Clip Epsilon</label>
                <input type="number" id="dojo-clip" value="0.2" step="0.05" min="0.1" max="0.5" class="dojo-input">
                <label>PPO Epochs</label>
                <input type="number" id="dojo-ppo-epochs" value="4" step="1" min="1" max="10" class="dojo-input">
              </div>

              <label>Symbol</label>
              <select id="dojo-symbol" class="dojo-input">
                <option value="EURUSD">EURUSD</option>
                <option value="USDJPY">USDJPY</option>
                <option value="AUDUSD">AUDUSD</option>
                <option value="XAUUSD">XAUUSD</option>
                <option value="GBPUSD">GBPUSD</option>
                <option value="EURJPY">EURJPY</option>
                <option value="XAGUSD">XAGUSD</option>
                <option value="SPX500">SPX500</option>
                <option value="JP225">JP225</option>
              </select>

              <label>Timeframe</label>
              <select id="dojo-tf" class="dojo-input">
                <option value="M15">M15</option>
                <option value="H1" selected>H1</option>
                <option value="H4">H4</option>
              </select>

              <label>Episodes</label>
              <input id="dojo-episodes" type="number" value="100" min="5" max="1000" class="dojo-input">

              <label>Learning Rate</label>
              <input id="dojo-lr" type="number" value="0.001" min="0.0001" max="0.01" step="0.0001" class="dojo-input">

              <label>Spread (pips)</label>
              <input id="dojo-spread" type="number" value="1.5" min="0" max="10" step="0.1" class="dojo-input">

              <label>Slippage (pips)</label>
              <input id="dojo-slippage" type="number" value="0.5" min="0" max="5" step="0.1" class="dojo-input">

              <label>Entropy</label>
              <input id="dojo-entropy" type="number" value="0.01" min="0" max="0.1" step="0.005" class="dojo-input">

              <div class="dojo-dialog-actions">
                <button class="dojo-btn dojo-btn-primary" onclick="Dojo.startTraining()">Start Training</button>
                <button class="dojo-btn" onclick="Dojo.hideTrainDialog()">Cancel</button>
              </div>
            </div>
                    </div>
        </div>

        <!-- Model Detail Modal -->
        <div id="dojo-model-modal" class="dojo-dialog hidden">
          <div class="dojo-modal-content" style="max-width:800px;">
            <div class="dojo-modal-header">
              <h3 id="dojo-model-title">Model Details</h3>
              <button class="dojo-btn dojo-btn-sm dojo-btn-ghost" onclick="Dojo.hideModelModal()" style="float:right;">✕</button>
            </div>
            <div id="dojo-model-body">
              <div class="dojo-loading">Loading...</div>
            </div>
            <div id="dojo-model-meta" style="font-size:11px;color:#666;margin-top:8px;"></div>
            <button id="dojo-delete-model" style="background:#ff4444;color:#fff;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;margin-top:8px;">Delete Model</button>
            <button id="dojo-walk-forward" style="background:#7c3aed;color:#fff;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;margin-top:8px;margin-left:8px;">Walk-Forward Test</button>
            <div id="dojo-wf-result" style="margin-top:8px;"></div>
          </div>
        </div>
      </div>
    `;
  }

  // ── Load leaderboard ──────────────────────────────────────────────────
  async function loadLeaderboard() {
    try {
      const data = await _get(`${API}/api/dojo/leaderboard`);
      const el = document.getElementById('dojo-leaderboard');
      if (!data.leaderboard || data.leaderboard.length === 0) {
        el.innerHTML = '<div class="dojo-empty">No trained models yet. Start a lesson!</div>';
        return;
      }

      let html = `<table class="dojo-table">
        <thead><tr>
          <th style="width:30px;"><input type="checkbox" id="dojo-select-all" /></th>
          <th>#</th><th>Symbol</th><th>TF</th><th>Skill</th>
          <th>Win Rate</th><th>P&L</th><th>Sharpe</th><th>PF</th><th>Episodes</th><th>Equity</th>
        </tr></thead><tbody>`;

      data.leaderboard.forEach((m, i) => {
        const skill = m.skill_level || 'Unknown';
        const color = SKILL_COLORS[skill];
        const icon = SKILL_ICONS[skill];
        const wr = (m.win_rate == null || isNaN(m.win_rate)) ? null : m.win_rate;
        const pnl = (m.pnl == null || isNaN(m.pnl)) ? null : m.pnl;
        const equity = (m.equity_curve || []).slice(-30).filter(v => Number.isFinite(v));
        html += `<tr>
          <td><input type="checkbox" class="dojo-model-check" value="${esc(m.symbol || '')}" /></td>
          <td>${i + 1}</td>
          <td class="dojo-bold dojo-clickable" onclick="Dojo.showModelModal('${esc(m.symbol || '')}', '${esc(m.timeframe || 'H1')}')">${esc(m.symbol || '--')}${m.is_stale ? ' <span title="Old config - needs retrain" style="color:#ff9800;">⚠</span>' : ''}</td>
          <td>${esc(m.timeframe || '--')}</td>
          <td><span class="dojo-skill-badge" style="background:${color}">${icon} ${esc(skill)}</span></td>
          <td>${fmtNum(wr != null ? wr * 100 : null, 1)}%</td>
          <td class="${pnl === null ? '' : (pnl >= 0 ? 'dojo-profit' : 'dojo-loss')}">${pnl === null ? '--' : '$' + pnl.toFixed(2)}</td>
          <td>${m.sharpe_ratio?.toFixed(2) || '—'}</td>
          <td>${m.profit_factor?.toFixed(1) || '—'}</td>
          <td>${m.episodes ?? '--'}</td>
          <td><canvas class="dojo-spark" data-equity='${esc(JSON.stringify(equity))}' width="100" height="30"></canvas></td>
        </tr>`;
      });

      html += '</tbody></table>';
      el.innerHTML = html;

      // Draw sparklines
      el.querySelectorAll('.dojo-spark').forEach(canvas => {
        try {
          drawSparkline(canvas, JSON.parse(canvas.dataset.equity || '[]'));
        } catch (e) { /* invalid equity data - skip sparkline */ }
      });

      // Select-all checkbox
      document.getElementById('dojo-select-all')?.addEventListener('change', (e) => {
        document.querySelectorAll('.dojo-model-check').forEach(cb => cb.checked = e.target.checked);
      });
    } catch (e) {
      console.error('Failed to load leaderboard:', e);
    }
  }

  // ── Load active training ──────────────────────────────────────────────
  async function loadActiveTraining() {
    try {
      const data = await _get(`${API}/api/dojo/status`);
      const el = document.getElementById('dojo-active-training');
      const active = Object.entries(data.active || {}).filter(([_, v]) => v);

      if (active.length === 0) {
        el.innerHTML = '<div class="dojo-empty">No active sessions</div>';
        return;
      }

      el.innerHTML = active.map(([sym]) => `
        <div class="dojo-active-item">
          <span class="dojo-pulse"></span>
          <span class="dojo-bold">${esc(sym)}</span>
          <span class="dojo-training-text">Training...</span>
          <button class="dojo-btn dojo-btn-sm" onclick="Dojo.stopTraining('${esc(sym)}')">Stop</button>
        </div>
      `).join('');
    } catch (e) {
      console.error('Failed to load active training:', e);
    }
  }

  // ── Load skill distribution ───────────────────────────────────────────
  async function loadSkillDist() {
    try {
      const data = await _get(`${API}/api/dojo/leaderboard`);
      const el = document.getElementById('dojo-skill-dist');
      const models = data.leaderboard || [];

      if (models.length === 0) {
        el.innerHTML = '<div class="dojo-empty">No data yet</div>';
        return;
      }

      const dist = {};
      models.forEach(m => {
        const s = m.skill_level || 'Unknown';
        dist[s] = (dist[s] || 0) + 1;
      });

      const total = models.length;
      el.innerHTML = Object.entries(dist).map(([skill, count]) => {
        const pct = (count / total * 100).toFixed(0);
        const color = SKILL_COLORS[skill];
        const icon = SKILL_ICONS[skill];
        return `
          <div class="dojo-dist-row">
            <span class="dojo-dist-label">${icon} ${esc(skill)}</span>
            <div class="dojo-dist-bar">
              <div class="dojo-dist-fill" style="width:${pct}%;background:${color}"></div>
            </div>
            <span class="dojo-dist-count">${count} (${pct}%)</span>
          </div>
        `;
      }).join('');
    } catch (e) {
      console.error('Failed to load skill dist:', e);
    }
  }

  // ── Sparkline drawing ─────────────────────────────────────────────────
  function drawSparkline(canvas, data) {
    if (!data || data.length < 2) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;

    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    ctx.strokeStyle = data[data.length - 1] >= data[0] ? '#4caf50' : '#f44336';
    ctx.lineWidth = 1.5;

    data.forEach((v, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - ((v - min) / range) * h * 0.8 - h * 0.1;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
        ctx.stroke();
  }

  // ── Live training progress panel ─────────────────────────────────────
  function updateProgressPanel(data) {
    const panel = document.getElementById('dojo-progress-panel');
    if (!panel) return;
    _currentSymbol = data.symbol;
    panel.classList.remove('hidden');

    const totalEpisodes = data.total_episodes || 0;
    const currentEpisode = data.episode || 0;
    const step = data.step || 0;
    const totalSteps = data.total_steps || 0;

    // Overall progress (accounts for step within current episode)
    let overallPct = 0;
    if (totalEpisodes > 0) {
      if (totalSteps > 0 && step > 0) {
        overallPct = ((currentEpisode - 1 + step / totalSteps) / totalEpisodes) * 100;
      } else {
        overallPct = (currentEpisode / totalEpisodes) * 100;
      }
    }
    overallPct = Math.min(overallPct, 100);

    const bar = document.getElementById('dojo-progress-bar');
    const label = document.getElementById('dojo-progress-label');
    if (bar) {
      bar.style.width = `${overallPct}%`;
      bar.style.background = overallPct >= 100 ? '#4caf50' : 'linear-gradient(90deg,#00d4ff,#7c3aed)';
    }
    if (label) {
      if (totalSteps > 0 && step > 0) {
        label.textContent = `Ep ${currentEpisode}/${totalEpisodes} — Step ${step}/${totalSteps}`;
      } else {
        label.textContent = `Ep ${currentEpisode}/${totalEpisodes} (${overallPct.toFixed(0)}%)`;
      }
    }

    // Speed & ETA
    const speedEl = document.getElementById('dojo-speed');
    if (speedEl && data.speed) {
      const eps = data.speed.episodes_per_second || 0;
      const eta = data.speed.eta_seconds || 0;
      speedEl.textContent = `Speed: ${eps.toFixed(2)} ep/s | ETA: ${formatEta(eta)}`;
    } else if (speedEl && !data.active) {
      speedEl.textContent = 'Training Complete';
    }

    document.getElementById('dojo-progress-episode').textContent =
      `${currentEpisode} / ${totalEpisodes}`;
    document.getElementById('dojo-progress-reward').textContent = fmtNum(data.reward, 2);
    document.getElementById('dojo-progress-pnl').textContent = fmtNum(data.pnl, 2);
    document.getElementById('dojo-progress-wr').textContent = fmtNum((data.win_rate ?? 0) * 100, 1) + '%';
    document.getElementById('dojo-progress-skill').textContent = data.skill_level || '--';
    document.getElementById('dojo-progress-trades').textContent = data.trades ?? 0;
    document.getElementById('dojo-progress-sharpe').textContent = data.sharpe_ratio?.toFixed(2) || '—';
    document.getElementById('dojo-progress-pf').textContent = data.profit_factor?.toFixed(1) || '—';
    const algo = data.algorithm || 'ppo';
    if (document.getElementById('dojo-algo')) {
      document.getElementById('dojo-algo').textContent = algo.toUpperCase();
    }
    const t = data.training_time_seconds ?? 0;
    document.getElementById('dojo-progress-time').textContent =
      `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

    // Episode history sparkline
    const epHistEl = document.getElementById('dojo-ep-history');
    if (epHistEl && data.episode_history && data.episode_history.length > 0) {
      const hist = data.episode_history.slice(-40);
      const maxAbs = Math.max(...hist.map(e => Math.abs(e.pnl || 0)), 1);
      epHistEl.innerHTML = hist.map(e => {
        const pnl = e.pnl || 0;
        const h = Math.max(2, (Math.abs(pnl) / maxAbs) * 38);
        const color = pnl >= 0 ? '#4caf50' : '#f44336';
        return `<div style="width:6px;height:${h}px;background:${color};border-radius:2px;flex-shrink:0;" title="Ep ${e.episode}: $${pnl.toFixed(2)}"></div>`;
      }).join('');
    } else if (epHistEl) {
      epHistEl.innerHTML = '<div style="font-size:10px;color:#555;">No episode data yet</div>';
    }

    if (!data.active) {
      if (bar) bar.style.background = '#4caf50';
      if (label) label.textContent = overallPct >= 100 ? 'Complete!' : `${overallPct.toFixed(0)}%`;
    }
  }

  // ── Live progress polling ─────────────────────────────────────────────
  let _stepPollActive = false;

  function loadLiveProgress(symbol) {
    if (_livePollTimer) clearInterval(_livePollTimer);
    _stepPollActive = true;

    async function poll() {
      try {
        const data = await _get(`${API}/api/dojo/step-progress/${encodeURIComponent(symbol)}`);
        if (data) {
          updateProgressPanel(data);
          if (data.active) {
            _livePollTimer = setTimeout(poll, 1000);
          } else {
            _stepPollActive = false;
            _livePollTimer = null;
          }
        }
      } catch (e) {
        try {
          const fallback = await _get(`${API}/api/dojo/live/${encodeURIComponent(symbol)}`);
          if (fallback) {
            updateProgressPanel(fallback);
            if (fallback.active) {
              _livePollTimer = setTimeout(poll, 3000);
            } else {
              _stepPollActive = false;
              _livePollTimer = null;
            }
          }
        } catch (e2) {
          _stepPollActive = false;
          _livePollTimer = null;
        }
      }
    }

    poll();
  }

  // ── Model detail modal ────────────────────────────────────────────────
  function pnlClass(v) {
    return v >= 0 ? 'dojo-profit' : 'dojo-loss';
  }

  function drawEquityChart(ctx, w, h, data) {
    if (!data || !data.length || data.length < 2) {
      ctx.fillStyle = '#888'; ctx.font = '12px monospace'; ctx.textAlign = 'center';
      ctx.fillText('No equity data', w / 2, h / 2);
      return;
    }
    const min = Math.min(...data), max = Math.max(...data);
    const range = (max - min) || 1;
    const pad = { top: 15, right: 15, bottom: 25, left: 55 };
    const pw = w - pad.left - pad.right, ph = h - pad.top - pad.bottom;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
    for (let i = 0; i <= 5; i++) {
      const val = min + (i / 5) * range, y = pad.top + ph - (i / 5) * ph;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + pw, y); ctx.stroke();
      ctx.fillStyle = '#888'; ctx.font = '10px monospace'; ctx.textAlign = 'right';
      ctx.fillText(fmtNum(val, 0), pad.left - 4, y + 3);
    }
    ctx.strokeStyle = '#4caf50'; ctx.lineWidth = 2; ctx.beginPath();
    data.forEach((v, i) => {
      const x = pad.left + (i / (data.length - 1)) * pw;
      const y = pad.top + ph - ((v - min) / range) * ph;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  async function showModelModal(symbol, timeframe) {
    const modal = document.getElementById('dojo-model-modal');
    const body = document.getElementById('dojo-model-body');
    if (!modal || !body) return;
    modal.classList.remove('hidden');
    body.innerHTML = '<div class="dojo-loading">Loading model details…</div>';
    document.getElementById('dojo-model-title').textContent = `${symbol} Details`;
    try {
      const data = await _get(`${API}/api/dojo/model/${encodeURIComponent(symbol)}?timeframe=${encodeURIComponent(timeframe || 'H1')}`);
      const skill = data.skill_level || 'Unknown';
      const icon = SKILL_ICONS[skill];
      let html = `<div class="dojo-model-grid">
        <div class="dojo-model-main">
          <h4>${esc(symbol)} ${esc(data.timeframe || 'H1')} — ${icon} ${esc(skill)}</h4>
          <div class="dojo-stats-grid">
            <div class="dojo-metric"><span class="dojo-dim small">Episodes</span><span class="dojo-bold">${data.episodes_completed ?? 0}</span></div>
            <div class="dojo-metric"><span class="dojo-dim small">Total Trades</span><span class="dojo-bold">${data.total_trades ?? 0}</span></div>
            <div class="dojo-metric"><span class="dojo-dim small">Win Rate</span><span class="dojo-bold">${fmtNum((data.overall_win_rate ?? 0) * 100, 1)}%</span></div>
            <div class="dojo-metric"><span class="dojo-dim small">Total P&amp;L</span><span class="dojo-bold ${pnlClass(data.overall_pnl)}">$${fmtNum(data.overall_pnl, 2)}</span></div>
            <div class="dojo-metric"><span class="dojo-dim small">Max DD</span><span class="dojo-bold">${fmtNum(data.overall_max_dd, 1)}%</span></div>
            <div class="dojo-metric"><span class="dojo-dim small">Skill Score</span><span class="dojo-bold">${fmtNum(data.skill_score, 0)}/100</span></div>
          </div>
          <h4 class="small dim" style="margin:16px 0 8px;">Equity Curve</h4>
          <canvas id="dojo-modal-equity" width="500" height="180"></canvas>
          ${data.test_win_rate != null || data.test_pnl != null || data.test_sharpe != null ? `
          <h4 class="small dim" style="margin:16px 0 8px;">Test Set Results</h4>
          <div class="dojo-stats-grid">
            <div class="dojo-metric"><span class="dojo-dim small">Test Win Rate</span><span class="dojo-bold">${fmtNum((data.test_win_rate ?? 0) * 100, 1)}%</span></div>
            <div class="dojo-metric"><span class="dojo-dim small">Test P&amp;L</span><span class="dojo-bold ${pnlClass(data.test_pnl)}">$${fmtNum(data.test_pnl, 2)}</span></div>
            <div class="dojo-metric"><span class="dojo-dim small">Test Sharpe</span><span class="dojo-bold">${data.test_sharpe?.toFixed(2) || '—'}</span></div>
          </div>
          ` : ''}
          <h4 class="small dim" style="margin:16px 0 8px;">Lessons Learned</h4>
          <div class="dojo-lessons">${(data.lessons_learned || []).map(l => `<div class="dojo-lesson">• ${esc(l)}</div>`).join('') || '<div class="dojo-empty">No lessons yet</div>'}</div>
          <h4 class="small dim" style="margin:16px 0 8px;">Recent Episodes</h4>
          <table class="table table-sm">
            <thead><tr><th>Ep</th><th>Reward</th><th>Trades</th><th>Win%</th><th>PnL</th><th>Skill</th></tr></thead>
            <tbody>${(data.episodes || []).slice(-15).reverse().map(e => `
              <tr><td>${e.episode}</td><td>${fmtNum(e.total_reward, 1)}</td><td>${e.trades}</td>
              <td>${fmtNum((e.win_rate || 0) * 100, 1)}%</td><td class="${pnlClass(e.pnl)}">$${fmtNum(e.pnl, 2)}</td><td>${esc(e.skill_level || '--')}</td></tr>
            `).join('')}</tbody></table>
        </div>
        <div class="dojo-model-sidebar">
          <h4 class="small dim">Training Config</h4>
          <table class="table table-sm">${Object.entries(data.config || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(String(v))}</td></tr>`).join('')}</table>
          <h4 class="small dim" style="margin-top:16px;">Convergence</h4>
          <p class="small">${data.converged ? 'Converged ✓' : 'Not yet converged'}</p>
        </div>
      </div>`;
      body.innerHTML = html;
      const canvas = document.getElementById('dojo-modal-equity');
      if (canvas && data.equity_curve && data.equity_curve.length) {
        canvas.width = canvas.parentElement.clientWidth || 500; canvas.height = 180;
        drawEquityChart(canvas.getContext('2d'), canvas.width, canvas.height, data.equity_curve);
      }
      // Populate metadata
      const metaDiv = document.getElementById('dojo-model-meta');
      if (metaDiv && data.metadata) {
        const meta = data.metadata;
        metaDiv.innerHTML = `Trained: ${meta.timestamp || '—'} | Bars: ${meta.data_bars || '—'} | Data: ${meta.data_start?.slice(0,10) || '—'} to ${meta.data_end?.slice(0,10) || '—'} | Algo: ${meta.algorithm || 'ppo'}`;
      }
      if (data.is_stale && metaDiv) {
        metaDiv.innerHTML += ' <span title="Config changed since training - model may be stale" style="color:#ff9800;">⚠ Stale</span>';
      }
      // Delete button handler
      const deleteBtn = document.getElementById('dojo-delete-model');
      if (deleteBtn) {
        deleteBtn.onclick = async () => {
          if (!confirm('Delete this model? This cannot be undone.')) return;
          try {
            await _del(`${API}/api/dojo/model/${encodeURIComponent(symbol)}?timeframe=${encodeURIComponent(timeframe || 'H1')}`);
            hideModelModal();
            loadLeaderboard();
            showBanner(`Deleted model for ${symbol}`);
          } catch (e) {
            showBanner('Failed to delete: ' + (e.message || e), true);
          }
        };
      }
    } catch (e) {
      body.innerHTML = `<p class="dojo-loss small">⚠ Failed to load: ${esc(e.message || e)}</p>`;
    }
  }

  function hideModelModal() {
    document.getElementById('dojo-model-modal').classList.add('hidden');
  }

  function showTrainDialog() {
    document.getElementById('dojo-train-dialog').classList.remove('hidden');
  }

  function hideTrainDialog() {
    document.getElementById('dojo-train-dialog').classList.add('hidden');
  }

  async function startTraining() {
    const symbol = document.getElementById('dojo-symbol').value;
    const tf = document.getElementById('dojo-tf').value;
    const episodes = parseInt(document.getElementById('dojo-episodes').value) || 100;
    const lr = parseFloat(document.getElementById('dojo-lr').value) || 0.001;
    const spread = parseFloat(document.getElementById('dojo-spread').value) || 1.5;
    const slippage = parseFloat(document.getElementById('dojo-slippage').value) || 0.5;
    const entropy = parseFloat(document.getElementById('dojo-entropy').value) || 0.01;
    const algo = document.getElementById('dojo-algorithm')?.value || 'ppo';
    const clip = document.getElementById('dojo-clip')?.value || '0.2';
    const ppoEpochs = document.getElementById('dojo-ppo-epochs')?.value || '4';

    try {
      await _post(`${API}/api/dojo/train?symbol=${symbol}&timeframe=${tf}&episodes=${episodes}&learning_rate=${lr}&spread_pips=${spread}&slippage_pips=${slippage}&entropy_coeff=${entropy}&algorithm=${algo}&clip_epsilon=${clip}&ppo_epochs=${ppoEpochs}`);
      hideTrainDialog();
      showBanner(`Training started for ${symbol} (${episodes} episodes)`);
      _currentSymbol = symbol;
      loadLiveProgress(symbol);
      pollTraining();
    } catch (e) {
      showBanner(e.message || 'Failed to start training', true);
    }
  }

  async function stopTraining(symbol) {
    try {
      await _del(`${API}/api/dojo/stop/${symbol}`);
      showBanner(`Stopping training for ${symbol}`);
    } catch (e) {
      console.error('Stop failed:', e);
    }
  }

  // ── Polling ───────────────────────────────────────────────────────────
  function pollTraining() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      await loadActiveTraining();
      await loadLeaderboard();
      await loadSkillDist();

      // Check if still training
      let data = { active: {} };
      try { data = await _get(`${API}/api/dojo/status`); } catch (e) { data = { active: {} }; }
      const active = Object.values(data.active || {}).filter(v => v);
      if (active.length === 0 && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
        showBanner('Training complete!');
        loadLeaderboard();
        loadSkillDist();
      }
    }, 3000);
  }

  // ── Banner ────────────────────────────────────────────────────────────
  function showBanner(msg, isError = false) {
    const el = document.getElementById('dojo-training-status');
    if (!el) return;
    el.textContent = msg;
    el.className = `dojo-training-banner ${isError ? 'dojo-banner-error' : 'dojo-banner-info'}`;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), 5000);
  }

  // ── Refresh ───────────────────────────────────────────────────────────
  async function refreshModels() {
    await loadLeaderboard();
    await loadActiveTraining();
    await loadSkillDist();
    showBanner('Refreshed');
  }

  // ── Init ──────────────────────────────────────────────────────────────
  function init() {
    loadLeaderboard();
    loadActiveTraining();
    loadSkillDist();
    // Toggle PPO params visibility
    const algoSelect = document.getElementById('dojo-algorithm');
    if (algoSelect) {
      algoSelect.addEventListener('change', () => {
        const ppoParams = document.getElementById('dojo-ppo-params');
        if (ppoParams) ppoParams.style.display = algoSelect.value === 'ppo' ? 'block' : 'none';
      });
    }
    // Auto-poll if training active
    _get(`${API}/api/dojo/status`).then(data => {
      if (!data) return;
      const active = Object.values(data.active || {}).filter(v => v);
      if (active.length > 0) pollTraining();
    }).catch(() => {});

    // Model comparison
    document.getElementById('dojo-compare-btn')?.addEventListener('click', async () => {
      const models = document.querySelectorAll('.dojo-model-check:checked');
      const symbols = Array.from(models).map(m => m.value).join(',');
      if (!symbols) { showBanner('Select models to compare', 'warning'); return; }

      const resp = await fetch(`${API}/api/dojo/compare?symbols=${symbols}`, { headers: AUTH_TOKEN ? { 'X-Auth-Token': AUTH_TOKEN } : {} });
      const data = await resp.json();

      const container = document.getElementById('dojo-comparison');
      if (!container) return;
      container.style.display = 'block';

      let html = '<table class="dojo-table"><thead><tr><th>Metric</th>';
      data.models.forEach(m => html += `<th>${m.symbol}</th>`);
      html += '</tr></thead><tbody>';

      const metrics = [
        ['Episodes', m => m.episodes_completed || 0],
        ['Win Rate', m => (m.overall_win_rate * 100).toFixed(1) + '%'],
        ['P&L', m => '$' + (m.overall_pnl || 0).toFixed(2)],
        ['Sharpe', m => (m.sharpe_ratio || 0).toFixed(2)],
        ['Profit Factor', m => (m.profit_factor || 0).toFixed(2)],
        ['Max DD', m => (m.overall_max_dd || 0).toFixed(1) + '%'],
        ['Skill', m => m.skill_level || '—'],
        ['Test Win Rate', m => ((m.test_win_rate || 0) * 100).toFixed(1) + '%'],
        ['Test P&L', m => '$' + (m.test_pnl || 0).toFixed(2)],
        ['Test Sharpe', m => (m.test_sharpe || 0).toFixed(2)],
      ];

      metrics.forEach(([label, fn]) => {
        html += `<tr><td>${label}</td>`;
        data.models.forEach(m => html += `<td>${fn(m)}</td>`);
        html += '</tr>';
      });

      html += '</tbody></table>';
      container.innerHTML = html;
    });

    // Walk-forward test button in model modal
    document.getElementById('dojo-walk-forward')?.addEventListener('click', async () => {
      const titleEl = document.getElementById('dojo-model-title');
      const symbol = titleEl ? titleEl.textContent.split(' ')[0] : null;
      const resultDiv = document.getElementById('dojo-wf-result');
      if (!resultDiv || !symbol) return;
      resultDiv.innerHTML = '<div style="color:#888;">Running walk-forward validation...</div>';

      try {
        const resp = await fetch(`${API}/api/dojo/walk-forward?symbol=${symbol}&timeframe=H1&episodes=30&n_windows=3`, {
          method: 'POST',
          headers: AUTH_TOKEN ? { 'X-Auth-Token': AUTH_TOKEN, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
        });
        const data = await resp.json();

        let html = '<div style="font-size:12px;">';
        html += `<div style="font-weight:600;margin-bottom:4px;">Walk-Forward Results (${data.n_windows} windows)</div>`;
        if (data.windows) {
          data.windows.forEach(w => {
            const color = w.test_pnl > 0 ? '#00d4ff' : '#ff4444';
            html += `<div style="margin:2px 0;">W${w.window}: Train $${w.train_pnl.toFixed(2)} → Test <span style="color:${color}">$${w.test_pnl.toFixed(2)}</span> (WR ${(w.test_wr*100).toFixed(0)}%)</div>`;
          });
        }
        if (data.aggregate) {
          const a = data.aggregate;
          html += `<div style="margin-top:6px;font-weight:600;">Aggregate: P&L $${a.total_test_pnl.toFixed(2)} | Sharpe ${a.test_sharpe} | Consistency ${(a.consistency*100).toFixed(0)}%</div>`;
        }
        html += '</div>';
        resultDiv.innerHTML = html;
      } catch (err) {
        resultDiv.innerHTML = `<div style="color:#ff4444;">Error: ${err.message}</div>`;
      }
    });
  }

  return {
    render,
    init,
    refreshModels,
    showTrainDialog,
    hideTrainDialog,
    startTraining,
    stopTraining,
    pollTraining,
    showModelModal,
    hideModelModal,
    loadLiveProgress,
  };
})();
