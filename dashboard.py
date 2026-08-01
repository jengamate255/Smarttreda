import datetime
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from gui_common import (
    MATPLOTLIB_OK,
    FigureCanvasTkAgg,
    Figure,
    MT5Config,
    mt5_connection,
    mt5,
    np,
    sma,
    ema,
    rsi,
    macd,
    bollinger_bands,
    atr,
    stochastic,
    adx,
    analyze_trend,
    support_resistance,
    pivot_points,
    AlertEngine,
    connect_mt5,
    enable_dpi_awareness,
    pad_front,
)


TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


class MT5Dashboard:
    def __init__(self, root):
        self.root = root
        root.title("MT5 Trading Dashboard")
        root.geometry("1400x900")
        root.configure(bg="#1e1e2e")

        self.symbol = tk.StringVar(value="EURUSD")
        self.timeframe = tk.StringVar(value="H1")
        self.count = 200
        self.auto_refresh = tk.BooleanVar(value=True)
        self.refresh_interval = tk.IntVar(value=5)

        self.alerts = AlertEngine(callback=self._log_alert)
        self.last_state = None

        self.config = MT5Config()
        self._connect()

        self._build_ui()
        self._setup_alerts()
        self._refresh()
        self._schedule()

    def _connect(self):
        if not mt5_connection.connect(self.config):
            messagebox.showerror("MT5 Error", "Failed to connect to MT5 terminal")
            self.root.destroy()

    def _build_ui(self):
        # Top control bar
        top = tk.Frame(self.root, bg="#1e1e2e")
        top.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(top, text="Symbol:", bg="#1e1e2e", fg="#cdd6f4",
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        self.symbol_entry = ttk.Combobox(top, textvariable=self.symbol, width=10,
                                         values=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "US30", "NAS100"])
        self.symbol_entry.pack(side=tk.LEFT, padx=(0, 15))

        tk.Label(top, text="TF:", bg="#1e1e2e", fg="#cdd6f4",
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        tf_box = ttk.Combobox(top, textvariable=self.timeframe, width=5,
                              values=list(TIMEFRAMES.keys()), state="readonly")
        tf_box.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Checkbutton(top, text="Auto-refresh", variable=self.auto_refresh).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(top, text="Sec:", style="TLabel").pack(side=tk.LEFT)
        ttk.Spinbox(top, from_=1, to=60, textvariable=self.refresh_interval, width=4).pack(side=tk.LEFT, padx=(0, 15))

        ttk.Button(top, text="Refresh Now", command=self._refresh).pack(side=tk.LEFT)
        ttk.Button(top, text="Apply", command=self._refresh).pack(side=tk.LEFT, padx=(0, 15))

        self.status_label = tk.Label(top, text="", bg="#1e1e2e", fg="#a6e3a1",
                                     font=("Consolas", 10))
        self.status_label.pack(side=tk.RIGHT)

        # Main area: left = charts, right = info
        main = tk.Frame(self.root, bg="#1e1e2e")
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = tk.Frame(main, bg="#1e1e2e")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right = tk.Frame(main, bg="#181825", width=380)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        right.pack_propagate(False)

        # Chart area
        if MATPLOTLIB_OK:
            self.fig = Figure(figsize=(9, 6), dpi=100, facecolor="#181825")
            self.ax_price = self.fig.add_subplot(3, 1, (1, 2))
            self.ax_rsi = self.fig.add_subplot(3, 1, 3, sharex=self.ax_price)
            self.fig.subplots_adjust(hspace=0.05)
            self.canvas = FigureCanvasTkAgg(self.fig, master=left)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        else:
            tk.Label(left, text="matplotlib not available - install to libs/",
                     bg="#1e1e2e", fg="#f38ba8").pack()

        # Right panel
        self.info_labels = {}
        rows = [
            ("account", "Account"),
            ("balance", "Balance"),
            ("equity", "Equity"),
            ("margin", "Margin"),
            ("price", "Price (B/A)"),
            ("spread", "Spread"),
            ("trend", "Trend"),
            ("rsi", "RSI (14)"),
            ("macd", "MACD Hist"),
            ("adx", "ADX (14)"),
            ("atr", "ATR (14)"),
            ("stoch", "Stoch %K/%D"),
            ("bb", "Bollinger"),
            ("sma20", "SMA 20"),
            ("sma50", "SMA 50"),
            ("pivot", "Daily Pivot"),
            ("s1", "Daily S1"),
            ("r1", "Daily R1"),
            ("support", "Support Zone"),
            ("resistance", "Resistance Zone"),
        ]
        tk.Label(right, text="MARKET INFO", bg="#181825", fg="#89b4fa",
                 font=("Segoe UI", 11, "bold")).pack(pady=(10, 5))

        info_frame = tk.Frame(right, bg="#181825")
        info_frame.pack(fill=tk.X, padx=10)

        for key, label in rows:
            row = tk.Frame(info_frame, bg="#181825")
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=label, bg="#181825", fg="#a6adc8", width=14,
                     anchor="w", font=("Segoe UI", 9)).pack(side=tk.LEFT)
            val = tk.Label(row, text="--", bg="#181825", fg="#cdd6f4",
                           anchor="e", font=("Consolas", 9))
            val.pack(side=tk.RIGHT)
            self.info_labels[key] = val

        # Alert log
        tk.Label(right, text="ALERT LOG", bg="#181825", fg="#89b4fa",
                 font=("Segoe UI", 11, "bold")).pack(pady=(15, 5))

        self.alert_box = tk.Text(right, height=12, bg="#11111b", fg="#f9e2af",
                                 font=("Consolas", 9), state=tk.DISABLED,
                                 relief=tk.FLAT, wrap=tk.WORD)
        self.alert_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        ttk.Button(right, text="Clear Log", command=self._clear_log).pack(pady=(0, 10))

        # Bindings
        self.symbol_entry.bind("<Return>", lambda e: self._refresh())
        tf_box.bind("<<ComboboxSelected>>", lambda e: self._refresh())

    def _log_alert(self, msg):
        self.alert_box.configure(state=tk.NORMAL)
        self.alert_box.insert(tk.END, msg + "\n")
        self.alert_box.see(tk.END)
        self.alert_box.configure(state=tk.DISABLED)
        self.root.bell()

    def _clear_log(self):
        self.alert_box.configure(state=tk.NORMAL)
        self.alert_box.delete("1.0", tk.END)
        self.alert_box.configure(state=tk.DISABLED)

    def _setup_alerts(self):
        def rsi_oversold(state):
            return state.get("rsi") is not None and state["rsi"] < 30
        def rsi_overbought(state):
            return state.get("rsi") is not None and state["rsi"] > 70
        def macd_cross_up(state):
            return state.get("macd_hist") is not None and state["macd_hist"] > 0 and state.get("prev_hist", 0) <= 0
        def macd_cross_down(state):
            return state.get("macd_hist") is not None and state["macd_hist"] < 0 and state.get("prev_hist", 0) >= 0
        def price_break_support(state):
            s = state.get("daily_s1")
            p = state.get("price")
            return s is not None and p is not None and p < s
        def price_break_resistance(state):
            r = state.get("daily_r1")
            p = state.get("price")
            return r is not None and p is not None and p > r
        def price_break_lower_bb(state):
            bb = state.get("bb_lower")
            p = state.get("price")
            return bb is not None and p is not None and p < bb
        def price_break_upper_bb(state):
            bb = state.get("bb_upper")
            p = state.get("price")
            return bb is not None and p is not None and p > bb

        self.alerts.add_condition("RSI Oversold (<30)", rsi_oversold)
        self.alerts.add_condition("RSI Overbought (>70)", rsi_overbought)
        self.alerts.add_condition("MACD Bullish Cross", macd_cross_up)
        self.alerts.add_condition("MACD Bearish Cross", macd_cross_down)
        self.alerts.add_condition("Price broke Daily S1", price_break_support)
        self.alerts.add_condition("Price broke Daily R1", price_break_resistance)
        self.alerts.add_condition("Price below BB Lower", price_break_lower_bb)
        self.alerts.add_condition("Price above BB Upper", price_break_upper_bb)

    def _refresh(self):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        try:
            symbol = self.symbol.get()
            tf_key = self.timeframe.get()
            tf_value = TIMEFRAMES.get(tf_key, mt5.TIMEFRAME_H1)

            rates = mt5.copy_rates_from_pos(symbol, tf_value, 0, self.count)
            if rates is None or len(rates) < 60:
                self.root.after(0, lambda: self.status_label.config(text=f"Error: {mt5.last_error()}"))
                return

            tick = mt5.symbol_info_tick(symbol)
            account = mt5.account_info()

            # Daily pivot (from D1)
            d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, 2)
            pivots = {}
            if d1 is not None and len(d1) > 0:
                last = d1[-1]
                pivots = pivot_points(last[2], last[3], last[4])

            # Indicators
            closes = np.array([r[4] for r in rates])
            highs = np.array([r[2] for r in rates])
            lows = np.array([r[3] for r in rates])

            rsi_vals = rsi(closes)
            macd_line, signal_line, hist = macd(closes)
            bb_up, bb_mid, bb_low = bollinger_bands(closes)
            atr_vals = atr(highs, lows, closes)
            stoch_k, stoch_d = stochastic(highs, lows, closes)
            adx_vals = adx(highs, lows, closes)
            sma20 = sma(closes, 20)
            sma50 = sma(closes, 50)
            trend = analyze_trend(closes)
            supports, resistances = support_resistance(closes, highs, lows)

            price = tick.bid if tick else closes[-1]

            state = {
                "price": price,
                "rsi": float(rsi_vals[-1]),
                "macd_hist": float(hist[-1]),
                "prev_hist": float(hist[-2]),
                "bb_upper": float(bb_up[-1]),
                "bb_lower": float(bb_low[-1]),
                "daily_s1": pivots.get("s1"),
                "daily_r1": pivots.get("r1"),
            }
            self.last_state = state

            # Update UI
            self.root.after(0, lambda: self._update_info(
                account, tick, trend, rsi_vals, macd_line, signal_line, hist,
                bb_up, bb_mid, bb_low, atr_vals, stoch_k, stoch_d, adx_vals,
                sma20, sma50, pivots, supports, resistances, symbol, tf_key,
                closes, highs, lows, rates,
            ))

            self.alerts.check_all(state)

        except Exception as e:
            self.root.after(0, lambda: self.status_label.config(text=f"Error: {e}"))

    def _update_info(self, account, tick, trend, rsi_vals, macd_line, signal_line,
                     hist, bb_up, bb_mid, bb_low, atr_vals, stoch_k, stoch_d,
                     adx_vals, sma20, sma50, pivots, supports, resistances,
                     symbol, tf_key, closes, highs, lows, rates):

        if account:
            self.info_labels["account"].config(text=str(account.login))
            self.info_labels["balance"].config(text=f"{account.balance:,.2f}")
            self.info_labels["equity"].config(text=f"{account.equity:,.2f}")
            self.info_labels["margin"].config(text=f"{account.margin_free:,.2f}")

        if tick:
            self.info_labels["price"].config(text=f"{tick.bid:.5f} / {tick.ask:.5f}")
            self.info_labels["spread"].config(text=str(tick.spread))

        colors = {
            "strong_uptrend": "#a6e3a1", "weak_uptrend": "#94e2d5",
            "strong_downtrend": "#f38ba8", "weak_downtrend": "#f9e2af",
            "ranging": "#cdd6f4", "insufficient_data": "#6c7086",
        }
        trend_color = colors.get(trend, "#cdd6f4")
        self.info_labels["trend"].config(text=trend, fg=trend_color)
        rsi_v = rsi_vals[-1]
        rsi_color = "#f38ba8" if rsi_v > 70 else ("#a6e3a1" if rsi_v < 30 else "#cdd6f4")
        self.info_labels["rsi"].config(text=f"{rsi_v:.1f}", fg=rsi_color)
        self.info_labels["macd"].config(text=f"{hist[-1]:+.5f}",
                                        fg="#a6e3a1" if hist[-1] > 0 else "#f38ba8")
        self.info_labels["adx"].config(text=f"{adx_vals[-1]:.1f}")
        self.info_labels["atr"].config(text=f"{atr_vals[-1]:.5f}")
        self.info_labels["stoch"].config(text=f"{stoch_k[-1]:.0f} / {stoch_d[-1]:.0f}")
        self.info_labels["bb"].config(text=f"U {bb_up[-1]:.5f} | L {bb_low[-1]:.5f}")
        self.info_labels["sma20"].config(text=f"{sma20[-1]:.5f}")
        self.info_labels["sma50"].config(text=f"{sma50[-1]:.5f}")
        if pivots:
            self.info_labels["pivot"].config(text=f"{pivots['pp']:.5f}")
            self.info_labels["s1"].config(text=f"{pivots['s1']:.5f}", fg="#f38ba8")
            self.info_labels["r1"].config(text=f"{pivots['r1']:.5f}", fg="#a6e3a1")
        self.info_labels["support"].config(text=", ".join(str(x) for x in supports[-3:]))
        self.info_labels["resistance"].config(text=", ".join(str(x) for x in resistances[-3:]))

        self.status_label.config(text=f"{symbol} {tf_key} | {datetime.datetime.now().strftime('%H:%M:%S')}")

        if MATPLOTLIB_OK:
            self._draw_chart(symbol, tf_key, rates, closes, highs, lows,
                             sma20, sma50, bb_up, bb_mid, bb_low, rsi_vals)

    def _draw_chart(self, symbol, tf_key, rates, closes, highs, lows,
                    sma20, sma50, bb_up, bb_mid, bb_low, rsi_vals):
        self.ax_price.clear()
        self.ax_rsi.clear()

        n = len(closes)
        x = np.arange(n)
        width = 0.6

        pad_sma20 = np.pad(sma20, (n - len(sma20), 0), mode="edge")
        pad_sma50 = np.pad(sma50, (n - len(sma50), 0), mode="edge")
        pad_bb_up = np.pad(bb_up, (n - len(bb_up), 0), mode="edge")
        pad_bb_low = np.pad(bb_low, (n - len(bb_low), 0), mode="edge")

        for i in range(n):
            o, c, h, l = rates[i][1], rates[i][4], rates[i][2], rates[i][3]
            color = "#a6e3a1" if c >= o else "#f38ba8"
            self.ax_price.vlines(i, l, h, color=color, linewidth=0.8)
            body_bottom = min(o, c)
            body_height = max(abs(c - o), 0.0001)
            self.ax_price.bar(i, body_height, bottom=body_bottom, width=width,
                              color=color, edgecolor=color, linewidth=0.5)

        self.ax_price.plot(x, pad_sma20, color="#f9e2af", linewidth=1.2, label="SMA20")
        self.ax_price.plot(x, pad_sma50, color="#89b4fa", linewidth=1.2, label="SMA50")
        self.ax_price.plot(x, pad_bb_up, color="#6c7086", linewidth=0.8, linestyle="--", label="BB")
        self.ax_price.plot(x, pad_bb_low, color="#6c7086", linewidth=0.8, linestyle="--")

        current = closes[-1]
        self.ax_price.axhline(current, color="#a6e3a1", linewidth=0.8, linestyle=":")

        self.ax_price.set_facecolor("#181825")
        self.ax_price.tick_params(colors="#cdd6f4", labelsize=8)
        for spine in self.ax_price.spines.values():
            spine.set_color("#313244")
        self.ax_price.legend(loc="upper left", fontsize=8, facecolor="#181825",
                             labelcolor="#cdd6f4")
        self.ax_price.set_title(f"{symbol} {tf_key}  (last {n} bars)",
                                color="#cdd6f4", fontsize=10)

        # RSI
        rsi_padded = np.pad(rsi_vals, (n - len(rsi_vals), 0), mode="edge")
        self.ax_rsi.plot(x, rsi_padded, color="#cba6f7", linewidth=1.0)
        self.ax_rsi.axhline(70, color="#f38ba8", linewidth=0.8, linestyle="--")
        self.ax_rsi.axhline(30, color="#a6e3a1", linewidth=0.8, linestyle="--")
        self.ax_rsi.axhline(50, color="#313244", linewidth=0.6)
        self.ax_rsi.set_ylim(0, 100)
        self.ax_rsi.set_facecolor("#181825")
        self.ax_rsi.tick_params(colors="#cdd6f4", labelsize=8)
        self.ax_rsi.set_ylabel("RSI", color="#cba6f7", fontsize=8)
        for spine in self.ax_rsi.spines.values():
            spine.set_color("#313244")

        self.fig.tight_layout()
        self.canvas.draw()

    def _schedule(self):
        if self.auto_refresh.get():
            interval = max(1, self.refresh_interval.get()) * 1000
            self.root.after(interval, self._tick)
        else:
            self.root.after(2000, self._schedule)

    def _tick(self):
        self._refresh()
        self._schedule()


def main():
    root = tk.Tk()
    enable_dpi_awareness()
    app = MT5Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()