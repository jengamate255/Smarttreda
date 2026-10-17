"""Multi-timeframe chart with supply/demand zones."""
import sys, os, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(os.path.dirname(__file__), '.env')) as f:
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k] = v

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.pattern_detection import detect_all_patterns, PatternType
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np


def detect_supply_demand(df, lookback=100):
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    n = len(df)

    atr_period = min(14, n - 1)
    if atr_period >= 2:
        tr = np.maximum(highs[1:] - lows[1:],
                       np.maximum(abs(highs[1:] - closes[:-1]),
                                 abs(lows[1:] - closes[:-1])))
        tr = np.insert(tr, 0, highs[0] - lows[0])
        atr = pd.Series(tr).ewm(span=atr_period, adjust=False).mean().values
    else:
        atr = np.ones(n)

    zones = []

    for i in range(5, min(lookback, n) - 2):
        leg_start = n - 1 - i

        rally_len = 0
        rally_strength = 0.0
        for j in range(leg_start, min(n, leg_start + 5)):
            if closes[j] > opens[j] and (closes[j] - opens[j]) > atr[j] * 0.6:
                rally_len += 1
                rally_strength += (closes[j] - opens[j])
            else:
                break

        drop_len = 0
        drop_strength = 0.0
        for j in range(leg_start, min(n, leg_start + 5)):
            if closes[j] < opens[j] and (opens[j] - closes[j]) > atr[j] * 0.6:
                drop_len += 1
                drop_strength += (opens[j] - closes[j])
            else:
                break

        if rally_len >= 2 and rally_strength > atr[leg_start] * 1.8:
            base_end = leg_start - 1
            base_start = base_end
            for k in range(base_end, max(base_end - 8, 0), -1):
                body = abs(closes[k] - opens[k])
                if body < atr[k] * 0.5:
                    base_start = k
                else:
                    break

            if base_start <= base_end:
                zone_top = float(max(highs[base_start:base_end + 1]))
                zone_bottom = float(min(lows[base_start:base_end + 1]))
                zones.append({'type': 'demand', 'top': zone_top,
                             'bottom': zone_bottom, 'start': base_start,
                             'end': base_end,
                             'strength': rally_strength / atr[leg_start]})

        elif drop_len >= 2 and drop_strength > atr[leg_start] * 1.8:
            base_end = leg_start - 1
            base_start = base_end
            for k in range(base_end, max(base_end - 8, 0), -1):
                body = abs(closes[k] - opens[k])
                if body < atr[k] * 0.5:
                    base_start = k
                else:
                    break

            if base_start <= base_end:
                zone_top = float(max(highs[base_start:base_end + 1]))
                zone_bottom = float(min(lows[base_start:base_end + 1]))
                zones.append({'type': 'supply', 'top': zone_top,
                             'bottom': zone_bottom, 'start': base_start,
                             'end': base_end,
                             'strength': drop_strength / atr[leg_start]})

    merged = []
    for zone in sorted(zones, key=lambda z: z['start']):
        found = False
        for existing in merged:
            if (existing['type'] == zone['type']
                    and zone['start'] <= existing.get('end', existing['start']) + 3
                    and zone['bottom'] <= existing['top'] * 1.005
                    and zone['top'] >= existing['bottom'] * 0.995):
                existing['top'] = max(existing['top'], zone['top'])
                existing['bottom'] = min(existing['bottom'], zone['bottom'])
                existing['end'] = max(existing.get('end', existing['start']), zone.get('end', zone['start']))
                existing['strength'] = max(existing['strength'], zone['strength'])
                found = True
                break
        if not found:
            merged.append(dict(zone))

    for zone in merged:
        zone_end = min(n, zone.get('end', zone['start']) + 50)
        touched = 0
        for j in range(zone_end, min(n, zone_end + 30)):
            if zone['type'] == 'demand' and lows[j] <= zone['bottom']:
                touched += 1
                break
            if zone['type'] == 'supply' and highs[j] >= zone['top']:
                touched += 1
                break
        zone['touches'] = touched
        zone['fresh'] = touched == 0

    return [z for z in merged if z.get('fresh', True)][-6:]


def draw_patterns(ax, patterns, n):
    """Draw detected chart patterns on the axes."""
    pattern_colors = {
        PatternType.SUPPORT: '#3fb950',
        PatternType.RESISTANCE: '#f85149',
        PatternType.TRIANGLE_SYMMETRIC: '#d2a8ff',
        PatternType.TRIANGLE_ASCENDING: '#7ee787',
        PatternType.TRIANGLE_DESCENDING: '#ffa657',
        PatternType.WEDGE_RISING: '#ff7b72',
        PatternType.WEDGE_FALLING: '#79c0ff',
        PatternType.DOUBLE_TOP: '#ffa198',
        PatternType.DOUBLE_BOTTOM: '#aff5b4',
        PatternType.HEAD_SHOULDERS: '#e3b341',
        PatternType.INV_HEAD_SHOULDERS: '#56d364',
    }

    pattern_labels = {
        PatternType.SUPPORT: 'Supp',
        PatternType.RESISTANCE: 'Res',
        PatternType.TRIANGLE_SYMMETRIC: 'SymTri',
        PatternType.TRIANGLE_ASCENDING: 'AscTri',
        PatternType.TRIANGLE_DESCENDING: 'DscTri',
        PatternType.WEDGE_RISING: 'RiWdg',
        PatternType.WEDGE_FALLING: 'FaWdg',
        PatternType.DOUBLE_TOP: 'DT',
        PatternType.DOUBLE_BOTTOM: 'DB',
        PatternType.HEAD_SHOULDERS: 'H&S',
        PatternType.INV_HEAD_SHOULDERS: 'iH&S',
    }

    for pat in patterns:
        color = pattern_colors.get(pat.type, '#8b949e')
        label = pattern_labels.get(pat.type, '?')
        alpha = 0.4 + (pat.ml_score / 100) * 0.5 if pat.ml_score else 0.6

        if pat.type in (PatternType.SUPPORT, PatternType.RESISTANCE):
            price = pat.price_levels[0]
            ax.axhline(price, color=color, linestyle='-', linewidth=1.2, alpha=alpha)
            ax.text(n + 3, price, f' {label}', fontsize=5, color=color,
                    va='center', alpha=alpha)

        elif pat.type in (PatternType.TRIANGLE_SYMMETRIC, PatternType.TRIANGLE_ASCENDING,
                          PatternType.TRIANGLE_DESCENDING):
            if len(pat.price_levels) >= 4:
                upper_start, upper_end = pat.price_levels[0], pat.price_levels[1]
                lower_start, lower_end = pat.price_levels[2], pat.price_levels[3]
                ax.plot([pat.start_bar, n - 1], [upper_start, upper_end],
                        color=color, linestyle='-', linewidth=1.3, alpha=alpha)
                ax.plot([pat.start_bar, n - 1], [lower_start, lower_end],
                        color=color, linestyle='-', linewidth=1.3, alpha=alpha)

                for x_idx in range(pat.start_bar, n):
                    progress = (x_idx - pat.start_bar) / max(1, n - 1 - pat.start_bar)
                    upper_y = upper_start + (upper_end - upper_start) * progress
                    lower_y = lower_start + (lower_end - lower_start) * progress
                    ax.plot([x_idx, x_idx], [lower_y, upper_y],
                            color=color, alpha=0.04, linewidth=0.5)

                mid_x = (pat.start_bar + n - 1) / 2
                mid_y = (upper_start + upper_end + lower_start + lower_end) / 4
                ax.text(mid_x, mid_y, f'{label}', fontsize=6, color=color,
                        alpha=alpha + 0.2, fontweight='bold', ha='center',
                        bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117',
                                  edgecolor=color, alpha=0.7, linewidth=0.8))

        elif pat.type in (PatternType.DOUBLE_TOP, PatternType.DOUBLE_BOTTOM):
            p1, p2 = pat.price_levels[0], pat.price_levels[1]
            neckline = pat.price_levels[2]
            ax.plot([pat.start_bar, pat.start_bar], [p1 - 0.001, p1 + 0.001],
                    color=color, linewidth=3, alpha=alpha, marker='o', markersize=4)
            ax.plot([pat.end_bar, pat.end_bar], [p2 - 0.001, p2 + 0.001],
                    color=color, linewidth=3, alpha=alpha, marker='o', markersize=4)
            ax.axhline(neckline, color=color, linestyle=':', linewidth=0.8, alpha=alpha * 0.6)
            mid_x = (pat.start_bar + pat.end_bar) / 2
            top_y = max(p1, p2) if pat.type == PatternType.DOUBLE_TOP else neckline
            ax.text(mid_x, top_y, f'{label}', fontsize=6, color=color,
                    alpha=alpha, fontweight='bold', ha='center')

        elif pat.type in (PatternType.HEAD_SHOULDERS, PatternType.INV_HEAD_SHOULDERS):
            p1, p2, p3 = pat.price_levels[0], pat.price_levels[1], pat.price_levels[2]
            neckline = pat.price_levels[3]
            xs_pts = [pat.start_bar, (pat.start_bar + pat.end_bar) // 2, pat.end_bar]
            ys_pts = [p1, p2, p3]
            ax.plot(xs_pts, ys_pts, color=color, linewidth=1.5, alpha=alpha, marker='o', markersize=3)
            ax.axhline(neckline, color=color, linestyle=':', linewidth=0.8, alpha=alpha * 0.6)
            mid_x = (pat.start_bar + pat.end_bar) / 2
            top_y = p2 if pat.type == PatternType.HEAD_SHOULDERS else neckline
            ax.text(mid_x, top_y, f'{label}', fontsize=6, color=color,
                    alpha=alpha, fontweight='bold', ha='center')


def render_timeframe(ax, df, tf_label, n_bars=150):
    df = df.tail(n_bars).reset_index(drop=True)
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    n = len(df)
    xs = np.arange(n)

    last_close = float(closes[-1])
    prev_close = float(closes[-2]) if n > 1 else last_close
    change = last_close - prev_close
    is_up = change >= 0

    ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().values
    span200 = min(200, max(n - 1, 2))
    ema200 = pd.Series(closes).ewm(span=span200, adjust=False).mean().values

    zones = detect_supply_demand(df, lookback=n)
    patterns = detect_all_patterns(df, lookback=n)

    ax.set_facecolor('#0d1117')
    ax.tick_params(colors='#8b949e', labelsize=6)
    ax.grid(True, color='#21262d', linestyle=':', linewidth=0.4, alpha=0.6)
    for spine in ax.spines.values():
        spine.set_edgecolor('#30363d')
        spine.set_linewidth(0.6)

    for zone in zones:
        color = '#3fb950' if zone['type'] == 'demand' else '#f85149'
        zone_start = zone['start']
        zone_end = zone.get('end', zone_start)
        width = n - zone_start + 2
        rect = patches.Rectangle(
            (zone_start - 0.5, zone['bottom']),
            width,
            zone['top'] - zone['bottom'],
            facecolor=color, alpha=0.15, edgecolor=color,
            linewidth=1.2, linestyle='--'
        )
        ax.add_patch(rect)
        mid_y = (zone['top'] + zone['bottom']) / 2
        label = 'D' if zone['type'] == 'demand' else 'S'
        ax.text(n + 2, mid_y, label, fontsize=7, color=color,
                va='center', fontweight='bold', alpha=0.9)

    draw_patterns(ax, patterns, n)

    candle_width = 0.7
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        color = '#3fb950' if c >= o else '#f85149'
        ax.plot([i, i], [l, h], color=color, linewidth=0.7)
        body_bottom = min(o, c)
        body_height = abs(c - o) if abs(c - o) > 0 else 0.0001
        rect = patches.Rectangle(
            (i - candle_width / 2, body_bottom), candle_width, body_height,
            facecolor=color, edgecolor=color, linewidth=0.5
        )
        ax.add_patch(rect)

    ax.plot(xs, ema50, color='#f39c12', linewidth=1.0, alpha=0.8)
    ax.plot(xs, ema200, color='#2f81f7', linewidth=1.0, alpha=0.8)

    ax.axhline(last_close, color='#3fb950' if is_up else '#f85149',
               linestyle='--', linewidth=0.8, alpha=0.6)
    ax.text(n + 1, last_close, f' {last_close:.5g}',
            va='center', fontsize=6, fontweight='bold', color='white',
            bbox=dict(boxstyle='round,pad=0.2',
                      facecolor='#3fb950' if is_up else '#f85149',
                      edgecolor='none', alpha=0.9))

    ax.set_xlim(-1, n + 8)

    supply_count = sum(1 for z in zones if z['type'] == 'supply')
    demand_count = sum(1 for z in zones if z['type'] == 'demand')
    pattern_count = len(patterns)
    sign = '+' if is_up else ''

    top_patterns = [p for p in patterns[:3] if p.ml_score and p.ml_score > 60]
    pattern_text = ''
    if top_patterns:
        name_map = {
            'triangle_symmetric': 'SymTri', 'triangle_ascending': 'AscTri',
            'triangle_descending': 'DscTri', 'double_top': 'DT',
            'double_bottom': 'DB', 'head_shoulders': 'H&S',
            'inv_head_shoulders': 'iH&S', 'support': 'Supp',
            'resistance': 'Res',
        }
        labels = [name_map.get(p.type.value, p.type.value) for p in top_patterns]
        pattern_text = '  ' + ' '.join(labels)

    title_text = f'{tf_label}  |  {last_close:.5g}  {sign}{change:.5g}  |  S/D:{supply_count + demand_count}  Pat:{pattern_count}{pattern_text}'
    ax.set_title(title_text, color='#c9d1d9', fontsize=9, fontweight='bold',
                 loc='left',
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='#161b22',
                           edgecolor='#30363d', alpha=0.95))


def main():
    mt5 = MT5Client()
    mt5.connect()

    symbol = 'USDCHF'
    timeframes = ['M15', 'H1', 'H4']

    fig, axes = plt.subplots(3, 1, figsize=(14, 13), facecolor='#0d1117', dpi=130)
    fig.subplots_adjust(hspace=0.3, left=0.08, right=0.92, top=0.95, bottom=0.05)

    for idx, tf in enumerate(timeframes):
        rates = mt5.rates(symbol, tf, count=200)
        df = pd.DataFrame(rates)
        render_timeframe(axes[idx], df, tf)

    fig.text(0.5, 0.98, f'{symbol} Multi-Timeframe + Supply/Demand Zones',
             fontsize=12, color='#c9d1d9', fontweight='bold',
             ha='center', va='top', fontfamily='monospace',
             bbox=dict(boxstyle='round,pad=0.4', facecolor='#161b22',
                       edgecolor='#30363d', alpha=0.98, linewidth=1.2))

    legend_y = 0.02
    fig.text(0.3, legend_y, '■ Demand Zone', fontsize=8, color='#3fb950',
             fontweight='bold', ha='center')
    fig.text(0.7, legend_y, '■ Supply Zone', fontsize=8, color='#f85149',
             fontweight='bold', ha='center')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=fig.get_facecolor(),
                edgecolor='none', bbox_inches='tight', pad_inches=0.3)
    plt.close(fig)
    buf.seek(0)
    png_data = buf.getvalue()
    print(f'PNG size: {len(png_data)}')

    import telegram
    import asyncio

    async def send():
        token = os.environ.get('MT5_NOTIFY_TELEGRAM_TOKEN', '')
        chat_id = os.environ.get('MT5_NOTIFY_TELEGRAM_CHAT_ID', '')
        tb = telegram.Bot(token=token)
        caption = f'<b>{symbol}</b> M15 | H1 | H4 + Supply/Demand Zones'
        await tb.send_photo(chat_id=chat_id, photo=io.BytesIO(png_data),
                            caption=caption)
        print('Sent!')

    asyncio.run(send())
    mt5.shutdown()


if __name__ == '__main__':
    main()
