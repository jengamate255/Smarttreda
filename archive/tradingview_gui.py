import datetime
import json
import os
import threading
import tkinter as tk
import urllib.parse
import urllib.request
from tkinter import ttk, messagebox

import matplotlib

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
    AlertEngine as _BaseAlertEngine,
    connect_mt5,
    enable_dpi_awareness,
    pad_front,
)

matplotlib.rcParams["font.family"] = "Segoe UI"

# ---------- TradingView palette ----------
TV_BG = "#131722"
TV_PANEL = "#1E222D"
TV_PANEL2 = "#181C25"
TV_BORDER = "#2A2E39"
TV_GREEN = "#26A69A"
TV_RED = "#EF5350"
TV_TEXT = "#D1D4DC"
TV_DIM = "#787B86"
TV_ACCENT = "#2962FF"
TV_YELLOW = "#FFD479"
TV_PURPLE = "#9B59B6"
TV_ORANGE = "#F39C12"
TV_CHART_BG = "#131722"
UP_CANDLE = "#26A69A"
DOWN_CANDLE = "#EF5350"

TIMEFRAMES = [("1m", mt5.TIMEFRAME_M1), ("5m", mt5.TIMEFRAME_M5), ("15m", mt5.TIMEFRAME_M15),
              ("30m", mt5.TIMEFRAME_M30), ("1h", mt5.TIMEFRAME_H1), ("4h", mt5.TIMEFRAME_H4),
              ("1D", mt5.TIMEFRAME_D1)]

WATCHLIST = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
             "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "US30", "NAS100", "SPX500", "GER40"]


def px(size):
    return f"{int(size)}px"


def _num(v):
    """Format a signal level for the treeview: float -> rounded, else raw."""
    try:
        if v is None or v == "":
            return ""
        f = float(v)
        return f"{f:.5f}"
    except (TypeError, ValueError):
        return str(v)


class TVStyle:
    @staticmethod
    def apply(root):
        style = ttk.Style(root)
        style.theme_use("clam")

        style.configure(".", background=TV_PANEL, foreground=TV_TEXT,
                        font=("Segoe UI", 9), fieldbackground=TV_PANEL)
        style.configure("TFrame", background=TV_PANEL)
        style.configure("Panel.TFrame", background=TV_PANEL2)
        style.configure("TLabel", background=TV_PANEL, foreground=TV_TEXT)
        style.configure("Dim.TLabel", background=TV_PANEL, foreground=TV_DIM)
        style.configure("Panel.TLabel", background=TV_PANEL2, foreground=TV_TEXT)
        style.configure("PanelDim.TLabel", background=TV_PANEL2, foreground=TV_DIM)
        style.configure("Title.TLabel", background=TV_PANEL, foreground=TV_TEXT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("PanelTitle.TLabel", background=TV_PANEL2, foreground=TV_TEXT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("Green.TLabel", background=TV_PANEL2, foreground=TV_GREEN,
                        font=("Consolas", 9))
        style.configure("Red.TLabel", background=TV_PANEL2, foreground=TV_RED,
                        font=("Consolas", 9))
        style.configure("GreenPanel.TLabel", background=TV_PANEL, foreground=TV_GREEN,
                        font=("Consolas", 9))
        style.configure("RedPanel.TLabel", background=TV_PANEL, foreground=TV_RED,
                        font=("Consolas", 9))
        style.configure("Accent.TLabel", background=TV_PANEL, foreground=TV_ACCENT,
                        font=("Consolas", 10, "bold"))

        style.configure("TButton", background=TV_PANEL2, foreground=TV_TEXT,
                        bordercolor=TV_BORDER, focuscolor=TV_PANEL2,
                        padding=(10, 4), relief="flat")
        style.map("TButton", background=[("active", TV_BORDER)])

        style.configure("Buy.TButton", background=TV_GREEN, foreground="#0b0f14",
                        font=("Segoe UI", 10, "bold"), padding=(18, 7))
        style.map("Buy.TButton", background=[("active", "#2bb8ab")])
        style.configure("Sell.TButton", background=TV_RED, foreground="#0b0f14",
                        font=("Segoe UI", 10, "bold"), padding=(18, 7))
        style.map("Sell.TButton", background=[("active", "#f26c5a")])

        style.configure("TFrame.TButton", background=TV_PANEL2, foreground=TV_DIM,
                        padding=(7, 3), relief="flat")
        style.map("TFrame.TButton", background=[("active", TV_BORDER)],
                  foreground=[("active", TV_TEXT)])

        style.configure("TFrame.Active.TButton", background=TV_ACCENT, foreground="#ffffff",
                        padding=(7, 3), relief="flat")
        style.map("TFrame.Active.TButton", background=[("active", TV_ACCENT)])

        style.configure("TCheckbutton", background=TV_PANEL, foreground=TV_TEXT)
        style.map("TCheckbutton", background=[("active", TV_PANEL)])
        style.configure("TSpinbox", fieldbackground=TV_PANEL2, foreground=TV_TEXT,
                        bordercolor=TV_BORDER, arrowcolor=TV_DIM)
        style.configure("TCombobox", fieldbackground=TV_PANEL2, foreground=TV_TEXT,
                        bordercolor=TV_BORDER, arrowcolor=TV_DIM)

        style.configure("Treeview", background=TV_PANEL2, fieldbackground=TV_PANEL2,
                        foreground=TV_TEXT, bordercolor=TV_BORDER, rowheight=24,
                        font=("Segoe UI", 9))
        style.map("Treeview", background=[("selected", "#2a3547")])
        style.configure("Treeview.Heading", background=TV_PANEL, foreground=TV_DIM,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview.Heading", background=[("active", TV_PANEL)])

        style.configure("TNotebook", background=TV_PANEL, bordercolor=TV_BORDER)
        style.configure("TNotebook.Tab", background=TV_PANEL, foreground=TV_DIM,
                        padding=(14, 5), font=("Segoe UI", 9))
        style.map("TNotebook.Tab", background=[("selected", TV_PANEL2)],
                  foreground=[("selected", TV_ACCENT)])

        style.configure("Horizontal.TProgressbar", background=TV_GREEN,
                        troughcolor=TV_PANEL2, bordercolor=TV_PANEL2)


class AlertEngine(_BaseAlertEngine):
    """TradingView GUI alert engine - inherits edge-triggered logic from gui_common."""
    pass


class OrderPanel(ttk.Frame):
    def __init__(self, parent, get_symbol, get_tf, notify, block_check=None):
        super().__init__(parent, style="Panel.TFrame")
        self.get_symbol = get_symbol
        self.get_tf = get_tf
        self.notify = notify
        self.block_check = block_check

        tk.Label(self, text="ORDER ENTRY", bg=TV_PANEL2, fg=TV_DIM,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 4))

        ORDER_TYPES = [
            ("Market Buy", "market", mt5.ORDER_TYPE_BUY),
            ("Market Sell", "market", mt5.ORDER_TYPE_SELL),
            ("Buy Limit", "pending", mt5.ORDER_TYPE_BUY_LIMIT),
            ("Sell Limit", "pending", mt5.ORDER_TYPE_SELL_LIMIT),
            ("Buy Stop", "pending", mt5.ORDER_TYPE_BUY_STOP),
            ("Sell Stop", "pending", mt5.ORDER_TYPE_SELL_STOP),
        ]
        self.order_types = ORDER_TYPES

        row = tk.Frame(self, bg=TV_PANEL2)
        row.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(row, text="Order type", bg=TV_PANEL2, fg=TV_DIM, width=10,
                 anchor="w").pack(side=tk.LEFT)
        self.type_var = tk.StringVar(value="Market Buy")
        ttk.Combobox(row, textvariable=self.type_var, width=12, state="readonly",
                     values=[t[0] for t in ORDER_TYPES]).pack(side=tk.RIGHT)

        row = tk.Frame(self, bg=TV_PANEL2)
        row.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(row, text="Lot size", bg=TV_PANEL2, fg=TV_DIM, width=10,
                 anchor="w").pack(side=tk.LEFT)
        self.lot_var = tk.StringVar(value="0.01")
        ttk.Spinbox(row, from_=0.01, to=100, increment=0.01,
                    textvariable=self.lot_var, width=8).pack(side=tk.RIGHT)

        row = tk.Frame(self, bg=TV_PANEL2)
        row.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(row, text="Price", bg=TV_PANEL2, fg=TV_DIM, width=10,
                 anchor="w").pack(side=tk.LEFT)
        self.price_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.price_var, width=12).pack(side=tk.RIGHT)

        row = tk.Frame(self, bg=TV_PANEL2)
        row.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(row, text="SL price", bg=TV_PANEL2, fg=TV_DIM, width=10,
                 anchor="w").pack(side=tk.LEFT)
        self.sl_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.sl_var, width=12).pack(side=tk.RIGHT)

        row = tk.Frame(self, bg=TV_PANEL2)
        row.pack(fill=tk.X, padx=10, pady=2)
        tk.Label(row, text="TP price", bg=TV_PANEL2, fg=TV_DIM, width=10,
                 anchor="w").pack(side=tk.LEFT)
        self.tp_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.tp_var, width=12).pack(side=tk.RIGHT)

        btn_row = tk.Frame(self, bg=TV_PANEL2)
        btn_row.pack(fill=tk.X, padx=10, pady=(6, 2))
        ttk.Button(btn_row, text="BUY", style="Buy.TButton",
                   command=lambda: self._send("BUY")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(btn_row, text="SELL", style="Sell.TButton",
                   command=lambda: self._send("SELL")).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(4, 0))

        self.msg = tk.Label(self, text="", bg=TV_PANEL2, fg=TV_DIM,
                            font=("Consolas", 8), wraplength=300, justify="left")
        self.msg.pack(fill=tk.X, padx=10, pady=(6, 8))

    def _send(self, side):
        if self.block_check:
            blocked, reason = self.block_check()
            if blocked:
                self.msg.config(text=f"BLOCKED: {reason}", fg=TV_RED)
                return
        symbol = self.get_symbol()
        type_name = self.type_var.get()
        entry = next((t for t in self.order_types if t[0] == type_name), None)
        if not entry:
            self.msg.config(text="Invalid order type", fg=TV_RED)
            return
        mode, order_type = entry[1], entry[2]

        try:
            lot = float(self.lot_var.get())
        except ValueError:
            self.msg.config(text="Invalid lot size", fg=TV_RED)
            return

        sl = self._parse(self.sl_var.get())
        tp = self._parse(self.tp_var.get())

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self.msg.config(text="No tick for " + symbol, fg=TV_RED)
            return

        if mode == "market":
            action = mt5.TRADE_ACTION_DEAL
            if order_type == mt5.ORDER_TYPE_BUY:
                price = tick.ask
            else:
                price = tick.bid
        else:
            action = mt5.TRADE_ACTION_PENDING
            price = self._parse(self.price_var.get())
            if price <= 0:
                self.msg.config(text="Price required for pending order", fg=TV_RED)
                return

        request = {
            "action": action,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 234000,
            "comment": "TV-GUI",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None:
            self.msg.config(text=f"Error: {mt5.last_error()}", fg=TV_RED)
            return
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            self.msg.config(text=f"DONE | deal #{result.deal} @ {result.price:.5f}",
                            fg=TV_GREEN)
            self.notify(f"[ORDER] {symbol} {type_name} {lot} @ {price:.5f}")
        elif result.retcode == mt5.TRADE_RETCODE_PLACED:
            self.msg.config(text=f"PLACED | order #{result.order} @ {result.price:.5f}",
                            fg=TV_GREEN)
            self.notify(f"[ORDER] {symbol} {type_name} {lot} @ {price:.5f} placed")
        else:
            self.msg.config(text=f"Rejected: {result.retcode} {result.comment}", fg=TV_RED)

    @staticmethod
    def _parse(s):
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0.0


class PositionsPanel(ttk.Frame):
    def __init__(self, parent, notify):
        super().__init__(parent, style="Panel.TFrame")
        self.notify = notify

        head = tk.Frame(self, bg=TV_PANEL2)
        head.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(head, text="POSITIONS", bg=TV_PANEL2, fg=TV_DIM,
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        self.total = tk.Label(head, text="", bg=TV_PANEL2, fg=TV_DIM, font=("Consolas", 8))
        self.total.pack(side=tk.RIGHT)

        cols = ("ticket", "symbol", "type", "volume", "open", "sl", "tp", "profit")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=8)
        widths = {"ticket": 80, "symbol": 70, "type": 50, "volume": 55,
                  "open": 75, "sl": 70, "tp": 70, "profit": 70}
        heads = {"ticket": "Ticket", "symbol": "Symbol", "type": "Type",
                 "volume": "Volume", "open": "Open", "sl": "SL", "tp": "TP", "profit": "P/L"}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="center", stretch=False)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10)

        btn_row = tk.Frame(self, bg=TV_PANEL2)
        btn_row.pack(fill=tk.X, padx=10, pady=(2, 0))
        ttk.Button(btn_row, text="Close Selected", command=self._close).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close All", command=self._close_all).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_row, text="Modify SL/TP", command=self._modify).pack(side=tk.RIGHT)

        mod_row = tk.Frame(self, bg=TV_PANEL2)
        mod_row.pack(fill=tk.X, padx=10, pady=(4, 6))
        tk.Label(mod_row, text="New SL:", bg=TV_PANEL2, fg=TV_DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)
        self.new_sl = tk.StringVar(value="")
        ttk.Entry(mod_row, textvariable=self.new_sl, width=10).pack(side=tk.LEFT, padx=(2, 8))
        tk.Label(mod_row, text="New TP:", bg=TV_PANEL2, fg=TV_DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)
        self.new_tp = tk.StringVar(value="")
        ttk.Entry(mod_row, textvariable=self.new_tp, width=10).pack(side=tk.LEFT, padx=(2, 0))

        self._last_tickets = set()

    def refresh(self, notify_events=True):
        positions = mt5.positions_get() or []
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        total_profit = 0.0
        for p in positions:
            pos_type = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
            self.tree.insert("", tk.END, iid=str(p.ticket), values=(
                p.ticket, p.symbol, pos_type, p.volume, round(p.price_open, 5),
                round(p.sl, 5) if p.sl else "--", round(p.tp, 5) if p.tp else "--",
                round(p.profit, 2),
            ))
            tag = "up" if p.profit >= 0 else "down"
            self.tree.item(str(p.ticket), tags=(tag,))
            total_profit += p.profit

        self.tree.tag_configure("up", foreground=TV_GREEN)
        self.tree.tag_configure("down", foreground=TV_RED)
        self.total.config(text=f"{len(positions)} pos | P/L {total_profit:+.2f}",
                          fg=TV_GREEN if total_profit >= 0 else TV_RED)

        if notify_events:
            current = {p.ticket for p in positions}
            opened = current - self._last_tickets
            closed = self._last_tickets - current
            now = datetime.datetime.now().strftime("%H:%M:%S")
            for t in opened:
                p = next((x for x in positions if x.ticket == t), None)
                if p:
                    self.notify(f"[{now}] NEW POSITION: {p.symbol} "
                                f"{'BUY' if p.type == 0 else 'SELL'} {p.volume} @ {p.price_open:.5f}")
            for t in closed:
                self.notify(f"[{now}] POSITION CLOSED: ticket {t}")
        self._last_tickets = {p.ticket for p in positions}

    def _selected_ticket(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _modify(self):
        ticket = self._selected_ticket()
        if not ticket:
            return
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return
        p = positions[0]
        sl = p.sl
        tp = p.tp
        try:
            if self.new_sl.get().strip():
                sl = float(self.new_sl.get())
            if self.new_tp.get().strip():
                tp = float(self.new_tp.get())
        except ValueError:
            self.notify("[MODIFY] Invalid SL/TP value")
            return
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": p.symbol,
            "position": ticket,
            "sl": sl,
            "tp": tp,
            "comment": "TV-GUI modify",
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            self.notify(f"[MODIFY] {p.symbol} #{ticket} SL {sl:.5f} TP {tp:.5f}")
        else:
            self.notify(f"[MODIFY] failed: {result.retcode if result else mt5.last_error()}")

    def _close(self):
        ticket = self._selected_ticket()
        if not ticket:
            return
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return
        p = positions[0]
        tick = mt5.symbol_info_tick(p.symbol)
        if not tick:
            return
        close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": p.magic,
            "comment": "TV-GUI close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            self.notify(f"[CLOSE] {p.symbol} ticket {ticket} @ {price:.5f}")

    def _close_all(self):
        positions = mt5.positions_get() or []
        for p in positions:
            ticket = p.ticket
            tick = mt5.symbol_info_tick(p.symbol)
            if not tick:
                continue
            close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": close_type,
                "position": ticket,
                "price": price,
                "deviation": 20,
                "magic": p.magic,
                "comment": "TV-GUI close all",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            mt5.order_send(request)


class TVDashboard:
    def __init__(self, root):
        self.root = root
        root.title("MT5 Trader - TradingView Style")
        root.geometry("1500x920")
        root.configure(bg=TV_BG)
        root.minsize(1280, 800)

        self.symbol = tk.StringVar(value="EURUSD")
        self.timeframe = tk.StringVar(value="1h")
        self.show_volume = tk.BooleanVar(value=True)
        self.show_rsi = tk.BooleanVar(value=True)
        self.show_macd = tk.BooleanVar(value=True)
        self.show_strategy = tk.BooleanVar(value=True)

        self.tf_buttons = {}
        self.state = {}
        self.compliance = {}
        self._tick_count = 0
        self._signals_cache = []

        self.config = MT5Config()
        if not mt5_connection.connect(self.config):
            messagebox.showerror("MT5 Error", "Failed to connect to MT5")
            root.destroy()
            return

        self.alerts = AlertEngine(self._log)
        self._setup_alerts()
        self._build_ui()
        self._restore_alert_history()
        self._refresh_compliance()
        self._refresh_signals()
        self._refresh_async()
        self._schedule()

    # ---------------- UI ----------------
    def _build_ui(self):
        TVStyle.apply(self.root)

        self._build_topbar()
        self._build_main()
        self._build_statusbar()

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=TV_BG)
        bar.pack(fill=tk.X, padx=8, pady=(6, 2))

        logo = tk.Label(bar, text="MT5", bg=TV_BG, fg="#ffffff",
                        font=("Segoe UI", 12, "bold"))
        logo.pack(side=tk.LEFT, padx=(4, 2))
        tk.Label(bar, text="Trader", bg=TV_BG, fg=TV_ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(bar, text="Symbol:", bg=TV_BG, fg=TV_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.symbol_box = ttk.Combobox(bar, textvariable=self.symbol, width=10,
                                       values=WATCHLIST)
        self.symbol_box.pack(side=tk.LEFT, padx=(4, 12))
        self.symbol_box.bind("<Return>", lambda e: self._switch_symbol())
        self.symbol_box.bind("<<ComboboxSelected>>", lambda e: self._switch_symbol())

        # Timeframe tabs
        for name, _tf in TIMEFRAMES:
            b = ttk.Button(bar, text=name, style="TFrame.TButton",
                           command=lambda n=name: self._set_tf(n))
            b.pack(side=tk.LEFT, padx=1)
            self.tf_buttons[name] = b

        ttk.Checkbutton(bar, text="Vol", variable=self.show_volume,
                        command=self._redraw).pack(side=tk.LEFT, padx=(14, 2))
        ttk.Checkbutton(bar, text="RSI", variable=self.show_rsi,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(bar, text="MACD", variable=self.show_macd,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(bar, text="Strat", variable=self.show_strategy,
                        command=self._redraw).pack(side=tk.LEFT, padx=2)

        self.price_ticker = tk.Label(bar, text="--", bg=TV_BG, fg=TV_TEXT,
                                     font=("Consolas", 14, "bold"))
        self.price_ticker.pack(side=tk.RIGHT, padx=6)
        self.change_label = tk.Label(bar, text="", bg=TV_BG, fg=TV_DIM,
                                     font=("Consolas", 10))
        self.change_label.pack(side=tk.RIGHT, padx=6)

    def _build_main(self):
        main = tk.Frame(self.root, bg=TV_BG)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=2)

        # Watchlist (left)
        self._build_watchlist(main)

        # Chart (center)
        self._build_chart(main)

        # Right column: account + positions + orders + alerts
        self._build_right(main)

        # Order bar (bottom of main)
        self._build_order_bar(main)

    def _build_watchlist(self, main):
        frame = tk.Frame(main, bg=TV_PANEL2, width=190)
        frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        frame.pack_propagate(False)

        tk.Label(frame, text="WATCHLIST", bg=TV_PANEL2, fg=TV_DIM,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=8, pady=(6, 2))

        cols = ("symbol", "bid", "chg")
        self.watch = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        self.watch.heading("symbol", text="Symbol")
        self.watch.heading("bid", text="Bid")
        self.watch.heading("chg", text="%")
        self.watch.column("symbol", width=70, anchor="w")
        self.watch.column("bid", width=62, anchor="e")
        self.watch.column("chg", width=46, anchor="e")
        self.watch.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self.watch.tag_configure("up", foreground=TV_GREEN)
        self.watch.tag_configure("down", foreground=TV_RED)
        self.watch.bind("<ButtonRelease-1>", self._watch_click)

        for s in WATCHLIST:
            self.watch.insert("", tk.END, iid=s, values=(s, "--", "--"))

    def _build_chart(self, main):
        container = tk.Frame(main, bg=TV_BG)
        container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        self.fig = Figure(figsize=(10, 7), dpi=100, facecolor=TV_CHART_BG)
        self.canvas = FigureCanvasTkAgg(self.fig, master=container)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._layout_axes()

        self.cross_vline = None
        self.cross_hline = None
        self.cross_annot = None
        self.cid = self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.cid_leave = self.canvas.mpl_connect("axes_leave_event", self._on_mouse_leave)

    def _on_mouse_move(self, event):
        if event.inaxes is not self.ax_price:
            self._hide_cross()
            return
        self.root.configure(cursor="crosshair")
        if self.cross_vline is None:
            self.cross_vline = self.ax_price.axvline(event.xdata, color="#ffffff",
                                                     linewidth=0.6, alpha=0.3, animated=True)
            self.cross_hline = self.ax_price.axhline(event.ydata, color="#ffffff",
                                                     linewidth=0.6, alpha=0.3, animated=True)
            self.cross_annot = self.ax_price.annotate(
                "", xy=(event.xdata, event.ydata), xytext=(5, 5),
                textcoords="offset points", fontsize=8, color="#ffffff",
                bbox=dict(boxstyle="round,pad=0.2", facecolor=TV_ACCENT, alpha=0.9),
                animated=True)
        else:
            self.cross_vline.set_xdata([event.xdata, event.xdata])
            self.cross_hline.set_ydata([event.ydata, event.ydata])
            self.cross_annot.set_position((event.xdata, event.ydata))

        if self.last_data and self.last_data["rates"] is not None:
            rates = self.last_data["rates"]
            idx = int(round(event.xdata))
            if 0 <= idx < len(rates):
                ts = datetime.datetime.fromtimestamp(int(rates[idx][0])).strftime("%d/%m %H:%M")
            else:
                ts = "--"
            self.cross_annot.set_text(f"{ts}  {event.ydata:.5f}")
        else:
            self.cross_annot.set_text(f"{event.ydata:.5f}")

        self.canvas.draw_idle()

    def _on_mouse_leave(self, _event):
        self.root.configure(cursor="")
        self._hide_cross()

    def _hide_cross(self):
        if self.cross_vline is not None:
            self.cross_vline.remove()
            self.cross_hline.remove()
            self.cross_annot.remove()
            self.cross_vline = self.cross_hline = self.cross_annot = None
            self.canvas.draw_idle()

    def _layout_axes(self):
        self.fig.clear()
        n = 1 + int(self.show_volume.get()) + int(self.show_rsi.get()) + int(self.show_macd.get())
        self.ax_price = self.fig.add_subplot(n, 1, 1)
        self.axes = [self.ax_price]
        i = 2
        if self.show_volume.get():
            ax = self.fig.add_subplot(n, 1, i, sharex=self.ax_price); i += 1
            self.ax_vol = ax; self.axes.append(ax)
        if self.show_rsi.get():
            ax = self.fig.add_subplot(n, 1, i, sharex=self.ax_price); i += 1
            self.ax_rsi = ax; self.axes.append(ax)
        if self.show_macd.get():
            ax = self.fig.add_subplot(n, 1, i, sharex=self.ax_price)
            self.ax_macd = ax; self.axes.append(ax)
        self.fig.subplots_adjust(hspace=0.02, left=0.06, right=0.95, top=0.94, bottom=0.05)
        for ax in self.axes:
            ax.set_facecolor(TV_CHART_BG)
            ax.tick_params(colors=TV_DIM, labelsize=8, length=0)
            for spine in ax.spines.values():
                spine.set_color(TV_BORDER)
        self.axes[-1].tick_params(colors=TV_DIM, labelsize=8)

    def _build_right(self, main):
        right = tk.Frame(main, bg=TV_PANEL, width=420)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        right.pack_propagate(False)

        # Account strip
        acc = tk.Frame(right, bg=TV_PANEL2)
        acc.pack(fill=tk.X, padx=4, pady=(0, 4))
        tk.Label(acc, text="ACCOUNT", bg=TV_PANEL2, fg=TV_DIM,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 0))
        self.acc_login = tk.Label(acc, text="", bg=TV_PANEL2, fg=TV_DIM, font=("Consolas", 8))
        self.acc_login.grid(row=0, column=1, sticky="e", padx=6, pady=(6, 0))

        fields = [("balance", "Balance"), ("equity", "Equity"), ("margin", "Free Margin")]
        self.acc_labels = {}
        for i, (key, label) in enumerate(fields):
            tk.Label(acc, text=label, bg=TV_PANEL2, fg=TV_DIM, font=("Consolas", 9)).grid(
                row=i + 1, column=0, sticky="w", padx=6)
            val = tk.Label(acc, text="--", bg=TV_PANEL2, fg=TV_TEXT, font=("Consolas", 10, "bold"))
            val.grid(row=i + 1, column=1, sticky="e", padx=6)
            self.acc_labels[key] = val
        acc.columnconfigure(1, weight=1)

        # Notebook: Positions / Orders / Alerts
        nb = ttk.Notebook(right)
        nb.pack(fill=tk.BOTH, expand=True, padx=4)

        self.positions_panel = PositionsPanel(nb, self._log)
        nb.add(self.positions_panel, text=" Positions ")

        orders_tab = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(orders_tab, text=" Orders ")
        cols = ("ticket", "symbol", "type", "volume", "price")
        tree = ttk.Treeview(orders_tab, columns=cols, show="headings", height=8)
        heads = {"ticket": "Ticket", "symbol": "Symbol", "type": "Type",
                 "volume": "Volume", "price": "Price"}
        for c in cols:
            tree.heading(c, text=heads[c])
            tree.column(c, width=70, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.orders_tree = tree

        signals_tab = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(signals_tab, text=" Signals ")
        scols = ("src", "symbol", "side", "entry", "sl", "tp", "conf", "age")
        stree = ttk.Treeview(signals_tab, columns=scols, show="headings", height=12)
        sheads = {"src": "Src", "symbol": "Symbol", "side": "Side", "entry": "Entry",
                  "sl": "SL", "tp": "TP", "conf": "Conf", "age": "Age"}
        swidths = {"src": 42, "symbol": 80, "side": 44, "entry": 74,
                   "sl": 74, "tp": 74, "conf": 46, "age": 44}
        for c in scols:
            stree.heading(c, text=sheads[c])
            stree.column(c, width=swidths[c], anchor="center")
        stree.tag_configure("up", foreground=TV_GREEN)
        stree.tag_configure("down", foreground=TV_RED)
        stree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        sbtns = tk.Frame(signals_tab, bg=TV_PANEL2)
        sbtns.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(sbtns, text="Load into order panel",
                   command=self._load_signal_into_order).pack(side=tk.LEFT)
        ttk.Button(sbtns, text="Refresh", command=self._refresh_signals).pack(side=tk.LEFT, padx=6)
        self.signals_tree = stree

        alerts_tab = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(alerts_tab, text=" Alerts ")
        self.alert_box = tk.Text(alerts_tab, bg=TV_PANEL2, fg=TV_YELLOW,
                                 font=("Consolas", 8), state=tk.DISABLED,
                                 relief=tk.FLAT, wrap=tk.WORD)
        self.alert_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _build_order_bar(self, main):
        bar = tk.Frame(main, bg=TV_PANEL2)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=(194, 0))

        self.order_panel = OrderPanel(bar, self._get_symbol, self._get_tf, self._log,
                                      block_check=self._compliance_block_check)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=TV_PANEL)
        bar.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 4), padx=8)

        self.status_conn = tk.Label(bar, text="MT5: checking...", bg=TV_PANEL, fg=TV_DIM,
                                    font=("Consolas", 8))
        self.status_conn.pack(side=tk.LEFT, padx=6)
        self.status_compliance = tk.Label(bar, text="", bg=TV_PANEL, fg=TV_DIM, font=("Consolas", 8))
        self.status_compliance.pack(side=tk.LEFT, padx=6)
        self.status_last = tk.Label(bar, text="", bg=TV_PANEL, fg=TV_DIM, font=("Consolas", 8))
        self.status_last.pack(side=tk.LEFT, padx=6)
        self.status_tf = tk.Label(bar, text="", bg=TV_PANEL, fg=TV_DIM, font=("Consolas", 8))
        self.status_tf.pack(side=tk.RIGHT, padx=6)

    # ---------------- Alerts ----------------
    def _setup_alerts(self):
        a = self.alerts
        a.add("RSI Oversold (<30)", lambda s: s.get("rsi") is not None and s["rsi"] < 30)
        a.add("RSI Overbought (>70)", lambda s: s.get("rsi") is not None and s["rsi"] > 70)
        a.add("MACD Bullish Cross", lambda s: s.get("macd_hist") is not None
              and s["macd_hist"] > 0 and s.get("prev_hist", 0) <= 0)
        a.add("MACD Bearish Cross", lambda s: s.get("macd_hist") is not None
              and s["macd_hist"] < 0 and s.get("prev_hist", 0) >= 0)
        a.add("Price broke Daily S1", lambda s: s.get("s1") is not None
              and s.get("price") is not None and s["price"] < s["s1"])
        a.add("Price broke Daily R1", lambda s: s.get("r1") is not None
              and s.get("price") is not None and s["price"] > s["r1"])
        a.add("Price below BB Lower", lambda s: s.get("bb_low") is not None
              and s.get("price") is not None and s["price"] < s["bb_low"])
        a.add("Price above BB Upper", lambda s: s.get("bb_up") is not None
              and s.get("price") is not None and s["price"] > s["bb_up"])
        a.add("Stoch Oversold", lambda s: s.get("stoch_k") is not None and s["stoch_k"] < 20)
        a.add("Stoch Overbought", lambda s: s.get("stoch_k") is not None and s["stoch_k"] > 80)

    def _restore_alert_history(self):
        # Restore alerts persisted across GUI restarts (logs/gui_alerts.json).
        for item in self.alerts.history():
            self._log(item.get("message", ""), bell=False)

    def _log(self, msg, bell=True):
        self.alert_box.configure(state=tk.NORMAL)
        self.alert_box.insert(tk.END, msg + "\n")
        self.alert_box.see(tk.END)
        self.alert_box.configure(state=tk.DISABLED)
        if bell:
            try:
                self.root.bell()
            except Exception:
                pass

    # ---------------- Compliance ----------------
    def _compliance_block_check(self):
        st = self.compliance or {}
        if st.get("mode") == "halted":
            return True, st.get("halt_reason") or "compliance halted"
        if st.get("trades_today", 0) >= st.get("slots_total", 10**9):
            return True, f"daily trade limit {st.get('trades_today')}/{st.get('slots_total')}"
        if st.get("positions", 0) >= st.get("max_positions", 10**9):
            return True, f"max positions {st.get('positions')}/{st.get('max_positions')}"
        return False, ""

    def _refresh_compliance(self):
        def worker():
            st = {
                "mode": "unknown", "trades_today": 0, "slots_total": 0,
                "positions": 0, "max_positions": 0, "halt_reason": "",
            }
            try:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                with open(os.path.join(base_dir, "mt5_mcp", "settings.json"), encoding="utf-8") as fh:
                    settings = json.load(fh).get("risk", {})
                with open(os.path.join(base_dir, "mt5_mcp", "memory", "compliance_state.json"), encoding="utf-8") as fh:
                    cstate = json.load(fh)
                st["mode"] = str(cstate.get("mode", "unknown"))
                st["halt_reason"] = str(cstate.get("halt_reason") or "")
                st["max_positions"] = int(settings.get("max_positions", 5))
                st["positions"] = len(mt5.positions_get() or [])
                base = int(settings.get("max_trades_per_day", 8))
                bonus = int(settings.get("max_trades_per_day_green_bonus", 0))
                now = int(datetime.datetime.now().timestamp())
                day_start = now - now % 86400
                deals = mt5.history_deals_get(day_start, now) or []
                st["trades_today"] = len(
                    [d for d in deals
                     if d.entry in (mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_OUT)
                     and d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)]
                )
                daily_pnl = sum(
                    float(d.profit or 0.0) + float(d.swap or 0.0) + float(d.commission or 0.0)
                    for d in deals
                )
                st["slots_total"] = base + (bonus if st["mode"] == "normal" and daily_pnl > 0 else 0)
            except Exception:
                pass
            self.compliance = st
            self.root.after(0, self._apply_compliance)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_compliance(self):
        st = self.compliance or {}
        mode = st.get("mode", "unknown")
        color = TV_GREEN if mode == "normal" else (TV_YELLOW if mode == "conservative" else TV_RED)
        text = (
            f"mode {mode} | trades {st.get('trades_today', 0)}/{st.get('slots_total', '?')}"
            f" | positions {st.get('positions', 0)}/{st.get('max_positions', '?')}"
        )
        if mode == "halted":
            text += " | HALTED"
        self.status_compliance.config(text=text, fg=color)

    # ---------------- Signals (web dashboards) ----------------
    SIGNAL_PORTS = (8050, 8051)

    def _refresh_signals(self):
        symbol = self.symbol.get()
        threading.Thread(target=lambda: self._fetch_signals(symbol), daemon=True).start()

    def _fetch_signals(self, symbol):
        rows = []
        for port in self.SIGNAL_PORTS:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/boom-crash/scan?min_confidence=0",
                    timeout=4,
                ) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
            except Exception:
                continue
            for sig in data.get("signals", []) or []:
                rows.append({
                    "src": str(port),
                    "symbol": str(sig.get("symbol") or ""),
                    "side": str(sig.get("direction") or "").upper(),
                    "entry": sig.get("entry_price") or sig.get("entry") or "",
                    "sl": sig.get("sl_price") or sig.get("sl") or "",
                    "tp": sig.get("tp_price") or sig.get("tp") or "",
                    "conf": sig.get("confidence"),
                    "age": sig.get("spike_age_candles"),
                })
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.SIGNAL_PORTS[0]}/api/live-overlay"
                f"?symbol={urllib.parse.quote(symbol)}&range_bars=24",
                timeout=4,
            ) as resp:
                ov = json.loads(resp.read().decode("utf-8", "replace"))
            bo = ov.get("breakout")
            if bo:
                rows.append({
                    "src": "OV", "symbol": str(ov.get("symbol") or symbol),
                    "side": str(bo.get("side") or "").upper(),
                    "entry": bo.get("entry"), "sl": bo.get("sl"), "tp": bo.get("tp"),
                    "conf": bo.get("confidence"), "age": "",
                })
            sp = ov.get("spike")
            if sp:
                rows.append({
                    "src": "OV", "symbol": str(ov.get("symbol") or symbol),
                    "side": str(sp.get("direction") or "").upper(),
                    "entry": sp.get("entry_price"), "sl": sp.get("sl_price"),
                    "tp": sp.get("tp_price"), "conf": sp.get("confidence"),
                    "age": sp.get("spike_age_candles"),
                })
        except Exception:
            pass
        self._signals_cache = rows
        self.root.after(0, self._apply_signals)

    def _apply_signals(self):
        tree = self.signals_tree
        for iid in tree.get_children():
            tree.delete(iid)
        for r in self._signals_cache:
            side = str(r.get("side") or "")
            tag = "up" if side.upper() == "BUY" else ("down" if side.upper() == "SELL" else "")
            conf = r.get("conf")
            tree.insert("", tk.END, values=(
                r.get("src", ""), r.get("symbol", ""), side,
                _num(r.get("entry")), _num(r.get("sl")), _num(r.get("tp")),
                f"{conf:.0f}" if isinstance(conf, (int, float)) else (conf or ""),
                r.get("age", ""),
            ), tags=(tag,) if tag else ())

    def _load_signal_into_order(self):
        sel = self.signals_tree.selection()
        if not sel:
            return
        vals = self.signals_tree.item(sel[0], "values")
        if len(vals) < 7:
            return
        symbol, side = vals[1], vals[2]
        entry, sl, tp = vals[3], vals[4], vals[5]
        if not symbol:
            return
        self.symbol.set(symbol)
        self.symbol_box.set(symbol)
        if str(side).upper() in ("BUY", "SELL"):
            self.order_panel.type_var.set(
                "Market Buy" if str(side).upper() == "BUY" else "Market Sell")
            self.order_panel.price_var.set("")
            if sl:
                self.order_panel.sl_var.set(str(sl))
            if tp:
                self.order_panel.tp_var.set(str(tp))
            self._log(f"[SIGNAL] loaded {side} {symbol} SL {sl} TP {tp}", bell=False)
        self._refresh_async()

    # ---------------- Data loop ----------------
    def _get_symbol(self):
        return self.symbol.get()

    def _get_tf(self):
        return self.timeframe.get()

    def _switch_symbol(self):
        s = self.symbol.get().upper()
        self.symbol.set(s)
        if s in WATCHLIST:
            self.watch.selection_set(s)
        self._refresh_async()

    def _set_tf(self, name):
        self.timeframe.set(name)
        self._refresh_async()

    def _refresh_async(self):
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            symbol = self.symbol.get()
            tf_name = self.timeframe.get()
            tf_value = next(v for n, v in TIMEFRAMES if n == tf_name)

            rates = mt5.copy_rates_from_pos(symbol, tf_value, 0, 300)
            if rates is None or len(rates) < 60:
                self.root.after(0, lambda: self.status_tf.config(
                    text=f"no data: {mt5.last_error()}"))
                return

            tick = mt5.symbol_info_tick(symbol)
            account = mt5.account_info()

            d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, 2)
            pivots = pivot_points(d1[-1][2], d1[-1][3], d1[-1][4]) if (d1 is not None and len(d1)) else {}

            closes = np.array([r[4] for r in rates], dtype=float)
            highs = np.array([r[2] for r in rates], dtype=float)
            lows = np.array([r[3] for r in rates], dtype=float)

            rsi_vals = rsi(closes)
            macd_l, macd_s, macd_h = macd(closes)
            bb_up, bb_mid, bb_low = bollinger_bands(closes)
            atr_vals = atr(highs, lows, closes)
            stoch_k, stoch_d = stochastic(highs, lows, closes)
            adx_vals = adx(highs, lows, closes)
            sma20 = sma(closes, 20)
            sma50 = sma(closes, 50)
            trend = analyze_trend(closes)
            supports, resistances = support_resistance(closes, highs, lows)

            state = {
                "price": tick.bid if tick else float(closes[-1]),
                "rsi": float(rsi_vals[-1]),
                "stoch_k": float(stoch_k[-1]),
                "macd_hist": float(macd_h[-1]),
                "prev_hist": float(macd_h[-2]),
                "bb_up": float(bb_up[-1]), "bb_low": float(bb_low[-1]),
                "s1": pivots.get("s1"), "r1": pivots.get("r1"),
            }
            self.state = state

            data = dict(symbol=symbol, tf=tf_name, rates=rates, tick=tick, account=account,
                        pivots=pivots, rsi=rsi_vals, macd=(macd_l, macd_s, macd_h),
                        bb=(bb_up, bb_mid, bb_low), atr=atr_vals, stoch=(stoch_k, stoch_d),
                        adx=adx_vals, sma20=sma20, sma50=sma50, trend=trend,
                        supports=supports, resistances=resistances,
                        closes=closes, highs=highs, lows=lows)
            data["strategy"] = self._strategy_overlay(symbol)
            self.root.after(0, lambda: self._apply(data))
            self.alerts.check(state)

            self.positions_panel.refresh()
            self._refresh_orders()
            self._refresh_watchlist(symbol)
        except Exception as e:
            self.root.after(0, lambda: self.status_tf.config(text=f"err: {e}"))

    def _apply(self, d):
        self._update_topbar(d)
        self._update_account(d)
        self._draw_chart(d)
        self._update_status(d)

    def _update_topbar(self, d):
        tick, symbol = d["tick"], d["symbol"]
        if tick:
            self.price_ticker.config(text=f"{tick.bid:.5f}")
            prev = d["closes"][-2]
            chg = (tick.bid - prev) / prev * 100
            self.change_label.config(
                text=f"{chg:+.3f}%", fg=TV_GREEN if chg >= 0 else TV_RED)
        tf = d["tf"]
        for name, btn in self.tf_buttons.items():
            btn.configure(style="TFrame.Active.TButton" if name == tf else "TFrame.TButton")
        if tick:
            self.order_panel.msg.config(text=f"{symbol} bid {tick.bid:.5f} / ask {tick.ask:.5f}")

    def _update_account(self, d):
        acc = d["account"]
        if not acc:
            return
        self.acc_login.config(text=f"#{acc.login} | {acc.server}")
        equity = acc.equity
        for key, label in [("balance", "balance"), ("equity", "equity"), ("margin", "margin_free")]:
            v = getattr(acc, label)
            self.acc_labels[key].config(text=f"{v:,.2f}",
                                        fg=TV_GREEN if v >= 0 else TV_RED)
        self.acc_labels["equity"].config(text=f"{equity:,.2f}",
                                         fg=TV_GREEN if equity >= acc.balance else TV_RED)

    def _update_status(self, d):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.status_last.config(text=f"updated {now}")
        self.status_conn.config(text=f"MT5 connected | {d['symbol']} {d['tf']} | {d['trend']}",
                                fg=TV_GREEN)
        p = d["tick"]
        if p:
            spread = (p.ask - p.bid) if (p.ask and p.bid) else 0.0
            self.status_tf.config(text=f"vol {p.volume} | spread {spread:.5f}")

    # ---------------- Chart ----------------
    def _redraw(self):
        if hasattr(self, "last_data"):
            self._draw_chart(self.last_data)

    def _draw_chart(self, d):
        self._hide_cross()
        self.last_data = d
        self._layout_axes()

        rates = d["rates"]
        n = len(rates)
        x = np.arange(n)

        # Candles
        for i in range(n):
            o, c, h, l = float(rates[i][1]), float(rates[i][4]), float(rates[i][2]), float(rates[i][3])
            color = UP_CANDLE if c >= o else DOWN_CANDLE
            self.ax_price.vlines(i, l, h, color=color, linewidth=0.7)
            self.ax_price.bar(i, max(abs(c - o), 1e-9), bottom=min(o, c), width=0.62,
                              color=color, edgecolor=color, linewidth=0.4)

        # Overlays
        def pad(arr):
            a = np.array(arr)
            return np.pad(a, (n - len(a), 0), mode="edge")

        self.ax_price.plot(x, pad(d["sma20"]), color=TV_YELLOW, linewidth=1.1, label="SMA 20")
        self.ax_price.plot(x, pad(d["sma50"]), color=TV_ACCENT, linewidth=1.1, label="SMA 50")
        bb_up, bb_mid, bb_low = d["bb"]
        self.ax_price.plot(x, pad(bb_up), color=TV_DIM, linewidth=0.8, linestyle="--")
        self.ax_price.plot(x, pad(bb_low), color=TV_DIM, linewidth=0.8, linestyle="--")
        self.ax_price.fill_between(x, pad(bb_up), pad(bb_low), color=TV_DIM, alpha=0.04)

        # Pivot + S/R lines
        if d["pivots"]:
            piv = d["pivots"]
            self.ax_price.axhline(piv["pp"], color=TV_PURPLE, linewidth=0.7, linestyle=":")
            self.ax_price.axhline(piv["s1"], color=TV_RED, linewidth=0.7, linestyle=":")
            self.ax_price.axhline(piv["r1"], color=TV_GREEN, linewidth=0.7, linestyle=":")

        # Current price line + tag
        cur = float(rates[-1][4])
        self.ax_price.axhline(cur, color="#ffffff", linewidth=0.6, alpha=0.4)
        self.ax_price.annotate(f"{cur:.5f}", xy=(n - 1, cur), xytext=(n + 2, cur),
                               fontsize=8, color=TV_TEXT, va="center")

        # Live strategy overlay: Donchian 24-bar M15 range + H1 EMA-200 trend
        strat = d.get("strategy") or {}
        if self.show_strategy.get() and strat:
            if strat.get("range_high"):
                self.ax_price.axhline(strat["range_high"], color=TV_YELLOW,
                                      linewidth=0.9, linestyle="--")
                self.ax_price.annotate(f"R24 {strat['range_high']:.5f}",
                                       xy=(n - 1, strat["range_high"]),
                                       xytext=(n + 2, strat["range_high"]),
                                       fontsize=7, color=TV_YELLOW, va="center")
            if strat.get("range_low"):
                self.ax_price.axhline(strat["range_low"], color=TV_YELLOW,
                                      linewidth=0.9, linestyle="--")
                self.ax_price.annotate(f"R24 {strat['range_low']:.5f}",
                                       xy=(n - 1, strat["range_low"]),
                                       xytext=(n + 2, strat["range_low"]),
                                       fontsize=7, color=TV_YELLOW, va="center")
            if strat.get("ema200"):
                ec = (TV_GREEN if strat.get("trend") == "bull"
                      else (TV_RED if strat.get("trend") == "bear" else TV_PURPLE))
                self.ax_price.axhline(strat["ema200"], color=ec, linewidth=1.0, linestyle=":")
                self.ax_price.annotate(f"EMA200(H1) {strat['ema200']:.5f}",
                                       xy=(n - 1, strat["ema200"]),
                                       xytext=(n + 2, strat["ema200"]),
                                       fontsize=7, color=ec, va="center")

        # Last N label
        tick = d["tick"]
        side_text = f"{d['symbol']} {d['tf'].upper()}"
        if tick:
            side_text += f"  bid {tick.bid:.5f}"
        self.ax_price.set_title(side_text, color=TV_DIM, fontsize=9,
                                loc="left", pad=8)
        self.ax_price.legend(loc="upper left", fontsize=7, framealpha=0,
                             labelcolor=TV_TEXT, ncol=2)

        last_time = datetime.datetime.fromtimestamp(int(rates[-1][0]))
        self.ax_price.set_xlim(0, n)
        self.ax_price.set_xlabel("")

        # Volume
        if self.show_volume.get():
            vols = [float(r[5]) for r in rates]
            colors = [UP_CANDLE if float(r[4]) >= float(r[1]) else DOWN_CANDLE for r in rates]
            self.ax_vol.bar(x, vols, width=0.62, color=colors, alpha=0.5)
            self.ax_vol.set_ylabel("Vol", color=TV_DIM, fontsize=7)
            self.ax_vol.set_ylim(0, max(vols) * 1.2 if vols else 1)

        # RSI
        if self.show_rsi.get():
            self.ax_rsi.plot(x, pad(d["rsi"]), color="#C792EA", linewidth=1.0)
            self.ax_rsi.axhline(70, color=TV_RED, linewidth=0.6, linestyle="--")
            self.ax_rsi.axhline(30, color=TV_GREEN, linewidth=0.6, linestyle="--")
            self.ax_rsi.axhline(50, color=TV_BORDER, linewidth=0.5)
            self.ax_rsi.set_ylim(0, 100)
            self.ax_rsi.set_ylabel("RSI", color=TV_DIM, fontsize=7)
            r = float(d["rsi"][-1])
            self.ax_rsi.set_title(f"{r:.1f}", color=TV_GREEN if r < 30 else (TV_RED if r > 70 else TV_TEXT),
                                  fontsize=9, loc="right")

        # MACD
        if self.show_macd.get():
            macd_l, macd_s, macd_h = d["macd"]
            self.ax_macd.bar(x, pad(macd_h), width=0.62,
                             color=[UP_CANDLE if v >= 0 else DOWN_CANDLE for v in pad(macd_h)],
                             alpha=0.6)
            self.ax_macd.plot(x, pad(macd_l), color="#4FC3F7", linewidth=0.9)
            self.ax_macd.plot(x, pad(macd_s), color=TV_YELLOW, linewidth=0.9)
            self.ax_macd.axhline(0, color=TV_BORDER, linewidth=0.6)
            self.ax_macd.set_ylabel("MACD", color=TV_DIM, fontsize=7)

        # X labels on last axis only
        for ax in self.axes[:-1]:
            ax.set_xticklabels([])

        # Grid + time labels
        if n > 1:
            step = max(1, n // 8)
            idxs = list(range(0, n, step))
            self.axes[-1].set_xticks(idxs)
            self.axes[-1].set_xticklabels(
                [datetime.datetime.fromtimestamp(int(rates[i][0])).strftime("%d/%m %H:%M") for i in idxs],
                fontsize=7, color=TV_DIM)

        self.canvas.draw()

    def _strategy_overlay(self, symbol):
        """Donchian 24-bar M15 range + ATR14, and H1 EMA-200 trend level
        (same math as forex_breakout_watch.py). Never raises."""
        try:
            m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 60)
            h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 220)
            out = {}
            if m15 is not None and len(m15) >= 27:
                h = np.array([float(r[2]) for r in m15], dtype=float)
                l = np.array([float(r[3]) for r in m15], dtype=float)
                c = np.array([float(r[4]) for r in m15], dtype=float)
                window_h = h[-25:-1]
                window_l = l[-25:-1]
                range_high = float(window_h.max())
                range_low = float(window_l.min())
                tr = np.maximum(h[1:] - l[1:], np.maximum(
                    np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
                atr14 = float(tr[-14:].mean()) if len(tr) >= 14 else float(tr.mean())
                out["range_high"] = range_high
                out["range_low"] = range_low
                out["atr"] = atr14
            if h1 is not None and len(h1) >= 220:
                try:
                    import pandas as pd
                    closes_h1 = np.array([float(r[4]) for r in h1], dtype=float)
                    ema200 = float(pd.Series(closes_h1).ewm(span=200, adjust=False).mean().iloc[-1])
                    h1_h = np.array([float(r[2]) for r in h1], dtype=float)
                    h1_l = np.array([float(r[3]) for r in h1], dtype=float)
                    tr1 = np.maximum(h1_h[1:] - h1_l[1:], np.maximum(
                        np.abs(h1_h[1:] - closes_h1[:-1]), np.abs(h1_l[1:] - closes_h1[:-1])))
                    atr_h1 = float(tr1[-14:].mean())
                    last = float(closes_h1[-1])
                    if last > ema200 + 0.3 * atr_h1:
                        out["trend"] = "bull"
                    elif last < ema200 - 0.3 * atr_h1:
                        out["trend"] = "bear"
                    else:
                        out["trend"] = "flat"
                    out["ema200"] = ema200
                    out["atr_h1"] = atr_h1
                except Exception:
                    pass
            return out
        except Exception:
            return {}

    # ---------------- Watchlist ----------------
    def _refresh_watchlist(self, active_symbol):
        def worker():
            try:
                rows = []
                for s in WATCHLIST:
                    t = mt5.symbol_info_tick(s)
                    if t:
                        rows.append((s, t.bid, t.ask))
            except Exception:
                rows = []

            def update():
                for s, bid, ask in rows:
                    if self.watch.exists(s):
                        prev = self.watch.set(s, "bid")
                        self.watch.set(s, "bid", f"{bid:.5f}")
                        if prev != "--" and float(prev) > 0:
                            chg = (bid - float(prev)) / float(prev) * 100
                            self.watch.set(s, "chg", f"{chg:+.2f}")
                            self.watch.item(s, tags=("up" if chg >= 0 else "down",))
                        else:
                            self.watch.set(s, "chg", "0.00")
                if active_symbol and self.watch.exists(active_symbol):
                    self.watch.selection_set(active_symbol)

            self.root.after(0, update)
        threading.Thread(target=worker, daemon=True).start()

    def _watch_click(self, _event):
        sel = self.watch.selection()
        if sel:
            self.symbol.set(sel[0])
            self.symbol_box.set(sel[0])
            self._refresh_async()

    # ---------------- Orders ----------------
    def _refresh_orders(self):
        def worker():
            try:
                orders = mt5.orders_get() or []
            except Exception:
                orders = []
            data = [(o.ticket, o.symbol, "BUY" if o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP)
                     else "SELL", o.volume_initial, o.price_open) for o in orders]

            def update():
                for iid in self.orders_tree.get_children():
                    self.orders_tree.delete(iid)
                for t, s, ty, v, p in data:
                    self.orders_tree.insert("", tk.END, values=(t, s, ty, v, round(p, 5)))

            self.root.after(0, update)
        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Loop ----------------
    def _schedule(self):
        self.root.after(5000, self._tick)

    def _tick(self):
        self._refresh_async()
        self._tick_count += 1
        if self._tick_count % 2 == 0:
            self._refresh_signals()
        if self._tick_count % 3 == 0:
            self._refresh_compliance()
        self._schedule()


def main():
    root = tk.Tk()
    enable_dpi_awareness()
    app = TVDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()