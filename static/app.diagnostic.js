/* Diagnostic Dashboard Panel */

const Diagnostic = {
  data: null,

  async load() {
    const container = document.getElementById('diagnostic-content');
    if (!container) return;

    try {
      const [summary, config, symbols, consensus] = await Promise.all([
        api('/api/diagnostic/summary'),
        api('/api/diagnostic/config'),
        api('/api/diagnostic/symbols'),
        api('/api/diagnostic/consensus'),
      ]);

      this.data = { summary, config, symbols, consensus };
      this.render();
    } catch (e) {
      container.innerHTML = `<div style="color:var(--error);padding:20px">Error loading diagnostic: ${e.message || e}</div>`;
    }
  },

  render() {
    const container = document.getElementById('diagnostic-content');
    if (!container || !this.data) return;

    const { summary, config, symbols, consensus } = this.data;
    const health = summary.health || {};
    const sym = summary.symbols || {};

    let html = `
      <div style="max-width:1400px;margin:0 auto">
        <h2 style="margin-bottom:20px;color:var(--text-primary)">🔍 Pipeline Diagnostic Dashboard</h2>
        <div style="color:var(--text-secondary);margin-bottom:24px;font-size:13px">
          Last updated: ${new Date(summary.timestamp).toLocaleString()}
        </div>

        <!-- System Health -->
        <div class="diag-section" style="background:var(--card-bg);border-radius:12px;padding:20px;margin-bottom:20px">
          <h3 style="margin:0 0 16px 0;color:var(--text-primary)">System Health</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px">
            <div class="diag-stat" style="padding:16px;background:var(--bg-secondary);border-radius:8px">
              <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">Config Status</div>
              <div style="font-size:24px;font-weight:600;color:${health.config_ok ? 'var(--success)' : 'var(--error)'}">
                ${health.config_ok ? '✓ OK' : '✗ Issues'}
              </div>
            </div>
            <div class="diag-stat" style="padding:16px;background:var(--bg-secondary);border-radius:8px">
              <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">Tradable Symbols</div>
              <div style="font-size:24px;font-weight:600;color:var(--text-primary)">${sym.tradable || 0}/${sym.total_engine || 0}</div>
            </div>
            <div class="diag-stat" style="padding:16px;background:var(--bg-secondary);border-radius:8px">
              <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">Would Trade Now</div>
              <div style="font-size:24px;font-weight:600;color:var(--accent)">${symbols.tradable_now || 0}</div>
            </div>
            <div class="diag-stat" style="padding:16px;background:var(--bg-secondary);border-radius:8px">
              <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">Consensus Votes</div>
              <div style="font-size:24px;font-weight:600;color:var(--text-primary)">${consensus.fresh_votes || 0} fresh</div>
            </div>
          </div>
        </div>
    `;

    // Issues
    const issues = config.issues || [];
    if (issues.length > 0) {
      html += `
        <div class="diag-section" style="background:var(--card-bg);border-radius:12px;padding:20px;margin-bottom:20px;border-left:3px solid var(--error)">
          <h3 style="margin:0 0 12px 0;color:var(--error)">⚠ Configuration Issues</h3>
          <ul style="margin:0;padding-left:20px;color:var(--text-primary);line-height:1.8">
            ${issues.map(i => `<li>${i}</li>`).join('')}
          </ul>
        </div>
      `;
    }

    // Current Config
    const cfg = summary.config || {};
    html += `
      <div class="diag-section" style="background:var(--card-bg);border-radius:12px;padding:20px;margin-bottom:20px">
        <h3 style="margin:0 0 16px 0;color:var(--text-primary)">Current Configuration</h3>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;font-size:14px">
          <div><span style="color:var(--text-secondary)">Risk per trade:</span> <strong>${cfg.risk_percent || 0.5}%</strong></div>
          <div><span style="color:var(--text-secondary)">Conservative risk:</span> <strong>${cfg.conservative_risk_percent || 0.5}%</strong></div>
          <div><span style="color:var(--text-secondary)">Min R:R:</span> <strong>${cfg.min_risk_reward || 2.0}</strong></div>
          <div><span style="color:var(--text-secondary)">Confidence floor:</span> <strong>${cfg.confidence_floor || 60}%</strong></div>
          <div><span style="color:var(--text-secondary)">Min zone score:</span> <strong>${cfg.min_zone_score || 60}</strong></div>
          <div><span style="color:var(--text-secondary)">Min trade score:</span> <strong>${cfg.min_trade_score || 60}</strong></div>
          <div><span style="color:var(--text-secondary)">MTF alignment:</span> <strong>${cfg.require_mtf_alignment ? 'Required' : 'Optional'}</strong></div>
          <div><span style="color:var(--text-secondary)">Max positions:</span> <strong>${cfg.max_positions || 3}</strong></div>
          <div><span style="color:var(--text-secondary)">Max trades/day:</span> <strong>${cfg.max_trades_per_day || 3}</strong></div>
        </div>
      </div>
    `;

    // Symbol breakdown
    html += `
      <div class="diag-section" style="background:var(--card-bg);border-radius:12px;padding:20px;margin-bottom:20px">
        <h3 style="margin:0 0 16px 0;color:var(--text-primary)">Symbol Pipeline Trace</h3>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>
              <tr style="border-bottom:1px solid var(--border)">
                <th style="text-align:left;padding:8px;color:var(--text-secondary)">Symbol</th>
                <th style="text-align:center;padding:8px;color:var(--text-secondary)">Engines</th>
                <th style="text-align:center;padding:8px;color:var(--text-secondary)">Compliance</th>
                <th style="text-align:center;padding:8px;color:var(--text-secondary)">Consensus</th>
                <th style="text-align:center;padding:8px;color:var(--text-secondary)">MT5</th>
                <th style="text-align:center;padding:8px;color:var(--text-secondary)">Strategy</th>
                <th style="text-align:left;padding:8px;color:var(--text-secondary)">Reason / Action</th>
              </tr>
            </thead>
            <tbody>
    `;

    const symList = symbols.symbols || [];
    for (const s of symList) {
      const compStatus = s.compliance_blocked
        ? '<span style="color:var(--error)">✗ Blocked</span>'
        : '<span style="color:var(--success)">✓ Pass</span>';
      const consStatus = s.consensus_allowed
        ? '<span style="color:var(--success)">✓ Allowed</span>'
        : '<span style="color:var(--error)">✗ Missing</span>';
      const mt5Status = s.mt5_available
        ? '<span style="color:var(--success)">✓</span>'
        : '<span style="color:var(--error)">✗</span>';

      let stratStatus, reason;
      if (s.strategy && s.strategy.action && s.strategy.action !== 'NONE') {
        stratStatus = `<span style="color:var(--success)">${s.strategy.action}</span>`;
        reason = `conf=${s.strategy.confidence || 0}%, score=${s.strategy.trade_score || 0}`;
      } else if (s.strategy && s.strategy.reason) {
        stratStatus = '<span style="color:var(--text-secondary)">NONE</span>';
        reason = s.strategy.reason;
      } else if (s.reason) {
        stratStatus = '<span style="color:var(--text-secondary)">—</span>';
        reason = s.reason;
      } else {
        stratStatus = '<span style="color:var(--text-secondary)">—</span>';
        reason = 'Not evaluated';
      }

      html += `
        <tr style="border-bottom:1px solid var(--border)">
          <td style="padding:8px;font-weight:600">${s.symbol}</td>
          <td style="padding:8px;text-align:center;font-size:11px">${(s.engines || []).join(', ')}</td>
          <td style="padding:8px;text-align:center">${compStatus}</td>
          <td style="padding:8px;text-align:center">${consStatus}</td>
          <td style="padding:8px;text-align:center">${mt5Status}</td>
          <td style="padding:8px;text-align:center">${stratStatus}</td>
          <td style="padding:8px;font-size:12px;color:var(--text-secondary);max-width:300px;overflow:hidden;text-overflow:ellipsis">${reason}</td>
        </tr>
      `;
    }

    html += `
            </tbody>
          </table>
        </div>
      </div>
    `;

    // Consensus board
    const votes = consensus.votes || {};
    const voteSymbols = Object.keys(votes);
    if (voteSymbols.length > 0) {
      html += `
        <div class="diag-section" style="background:var(--card-bg);border-radius:12px;padding:20px;margin-bottom:20px">
          <h3 style="margin:0 0 16px 0;color:var(--text-primary)">Consensus Board</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px">
      `;

      for (const sym of voteSymbols) {
        const entries = votes[sym] || [];
        const freshEntries = entries.filter(e => e.fresh);
        if (freshEntries.length === 0) continue;

        html += `
          <div style="padding:12px;background:var(--bg-secondary);border-radius:8px">
            <div style="font-weight:600;margin-bottom:8px">${sym}</div>
        `;
        for (const e of freshEntries) {
          const sideColor = e.side === 'BUY' ? 'var(--success)' : e.side === 'SELL' ? 'var(--error)' : 'var(--text-secondary)';
          html += `
            <div style="font-size:12px;margin-bottom:4px">
              <span style="color:var(--text-secondary)">${e.source}:</span>
              <span style="color:${sideColor};font-weight:600">${e.side}</span>
              <span style="color:var(--text-secondary)">(${e.score || 0})</span>
              <span style="color:var(--text-secondary);font-size:10px"> ${Math.round(e.age_sec / 60)}m ago</span>
            </div>
          `;
        }
        html += `</div>`;
      }

      html += `
          </div>
        </div>
      `;
    }

    html += `</div>`;
    container.innerHTML = html;
  },
};
