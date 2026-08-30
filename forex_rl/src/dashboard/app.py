"""Dash dashboard for forex_rl.

Shows: equity curve, drawdown, open positions, trade history, regime, metrics.
Run: python -m forex_rl.src.dashboard.app
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

try:
    import dash
    from dash import dcc, html, dash_table, Input, Output, State
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    HAS_DASH = True
except ImportError:
    HAS_DASH = False


class Dashboard:
    """Dash-based dashboard for monitoring forex RL trading system."""

    def __init__(self, experiment_id: Optional[str] = None) -> None:
        if not HAS_DASH:
            raise ImportError(
                "Dash and Plotly are required. Install with: pip install dash plotly"
            )
        self.experiment_id = experiment_id
        self.app = dash.Dash(__name__)
        self._setup_layout()
        self._setup_callbacks()

    def _setup_layout(self) -> None:
        self.app.layout = html.Div(
            style={"fontFamily": "Segoe UI, sans-serif", "padding": "20px", "backgroundColor": "#1a1a2e"},
            children=[
                # Header
                html.Div(
                    style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "20px"},
                    children=[
                        html.H1("Forex RL Dashboard", style={"color": "#e0e0e0", "margin": "0"}),
                        html.Div(
                            id="training-status",
                            style={
                                "padding": "8px 16px", "borderRadius": "4px",
                                "backgroundColor": "#16213e", "color": "#00d2ff",
                                "fontWeight": "bold", "fontSize": "14px",
                            },
                            children="STATUS: IDLE",
                        ),
                    ],
                ),
                # Metrics cards row
                html.Div(
                    id="metrics-cards",
                    style={"display": "grid", "gridTemplateColumns": "repeat(6, 1fr)", "gap": "12px", "marginBottom": "20px"},
                    children=self._build_metric_cards({
                        "Equity": "$10,000",
                        "Drawdown": "0.0%",
                        "Win Rate": "0.0%",
                        "Sharpe": "0.00",
                        "Profit Factor": "0.00",
                        "Total Trades": "0",
                    }),
                ),
                # Charts row
                html.Div(
                    style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "16px", "marginBottom": "20px"},
                    children=[
                        html.Div([
                            html.H3("Equity Curve", style={"color": "#b0b0b0", "fontSize": "14px"}),
                            dcc.Graph(id="equity-chart", style={"height": "350px"}),
                        ]),
                        html.Div([
                            html.H3("Drawdown", style={"color": "#b0b0b0", "fontSize": "14px"}),
                            dcc.Graph(id="drawdown-chart", style={"height": "350px"}),
                        ]),
                    ],
                ),
                # Bottom row: positions + history
                html.Div(
                    style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "16px"},
                    children=[
                        html.Div([
                            html.H3("Open Positions", style={"color": "#b0b0b0", "fontSize": "14px"}),
                            html.Div(id="positions-table"),
                        ]),
                        html.Div([
                            html.H3("Recent Trades", style={"color": "#b0b0b0", "fontSize": "14px"}),
                            html.Div(id="trades-table"),
                        ]),
                    ],
                ),
                # Regime indicator
                html.Div(
                    id="regime-bar",
                    style={
                        "marginTop": "16px", "padding": "12px", "borderRadius": "6px",
                        "backgroundColor": "#16213e", "color": "#e0e0e0",
                        "fontSize": "14px",
                    },
                    children="Regime: -- | Trend: -- | ADX: --",
                ),
                # Auto-refresh interval
                dcc.Interval(id="refresh-interval", interval=30 * 1000, n_intervals=0),
            ],
        )

    def _setup_callbacks(self) -> None:
        @self.app.callback(
            [
                Output("metrics-cards", "children"),
                Output("equity-chart", "figure"),
                Output("drawdown-chart", "figure"),
                Output("positions-table", "children"),
                Output("trades-table", "children"),
                Output("regime-bar", "children"),
                Output("training-status", "children"),
            ],
            [Input("refresh-interval", "n_intervals")],
        )
        def update_dashboard(n: int) -> tuple:
            data = self._load_data()
            metrics = data.get("metrics", {})
            equity_curve = data.get("equity_curve", [10000.0])
            trades = data.get("trades", [])
            positions = data.get("positions", [])
            regime = data.get("regime", {})
            status = data.get("status", "IDLE")

            # Metrics cards
            cards = self._build_metric_cards({
                "Equity": f"${metrics.get('equity', equity_curve[-1]):,.2f}",
                "Drawdown": f"{metrics.get('drawdown', 0.0):.1%}",
                "Win Rate": f"{metrics.get('win_rate', 0.0):.1%}",
                "Sharpe": f"{metrics.get('sharpe', 0.0):.2f}",
                "Profit Factor": f"{metrics.get('profit_factor', 0.0):.2f}",
                "Total Trades": str(metrics.get('total_trades', 0)),
            })

            # Equity chart
            eq_fig = go.Figure()
            eq_fig.add_trace(go.Scatter(
                y=equity_curve, mode="lines", name="Equity",
                line={"color": "#00d2ff", "width": 2},
            ))
            eq_fig.update_layout(
                template="plotly_dark", paper_bgcolor="#16213e", plot_bgcolor="#16213e",
                margin=dict(l=40, r=20, t=10, b=30),
                xaxis_title="Step", yaxis_title="Equity ($)",
                font={"color": "#b0b0b0"},
            )

            # Drawdown chart
            eq_arr = np.array(equity_curve)
            running_max = np.maximum.accumulate(eq_arr)
            dd = np.where(running_max > 0, (running_max - eq_arr) / running_max, 0.0)
            dd_fig = go.Figure()
            dd_fig.add_trace(go.Scatter(
                y=-dd, fill="tozeroy", name="Drawdown",
                line={"color": "#ff4757", "width": 1},
                fillcolor="rgba(255, 71, 87, 0.3)",
            ))
            dd_fig.update_layout(
                template="plotly_dark", paper_bgcolor="#16213e", plot_bgcolor="#16213e",
                margin=dict(l=40, r=20, t=10, b=30),
                xaxis_title="Step", yaxis_title="Drawdown",
                font={"color": "#b0b0b0"},
            )

            # Positions table
            pos_table = self._build_positions_table(positions)

            # Trades table
            trades_table = self._build_trades_table(trades)

            # Regime bar
            regime_text = (
                f"Regime: {regime.get('label', '--')} | "
                f"Trend: {regime.get('trend', '--')} | "
                f"ADX: {regime.get('adx', '--')}"
            )

            status_text = f"STATUS: {status}"

            return cards, eq_fig, dd_fig, pos_table, trades_table, regime_text, status_text

    def _build_metric_cards(self, metrics: dict[str, str]) -> list[html.Div]:
        cards = []
        for label, value in metrics.items():
            cards.append(
                html.Div(
                    style={
                        "backgroundColor": "#16213e", "padding": "16px",
                        "borderRadius": "8px", "textAlign": "center",
                    },
                    children=[
                        html.Div(label, style={"color": "#888", "fontSize": "12px", "marginBottom": "4px"}),
                        html.Div(value, style={"color": "#e0e0e0", "fontSize": "20px", "fontWeight": "bold"}),
                    ],
                )
            )
        return cards

    def _build_positions_table(self, positions: list[dict]) -> html.Table:
        if not positions:
            return html.Table(style={"width": "100%", "color": "#b0b0b0"}, children=[
                html.Tr([html.Td("No open positions", style={"textAlign": "center", "padding": "20px"})])
            ])
        header = html.Tr([html.Th(h) for h in ["Symbol", "Side", "Entry", "Volume", "P&L"]])
        rows = []
        for p in positions:
            pnl_color = "#00ff88" if p.get("pnl", 0) >= 0 else "#ff4757"
            rows.append(html.Tr([
                html.Td(p.get("symbol", "")),
                html.Td(p.get("side", "")),
                html.Td(f"{p.get('entry', 0):.5f}"),
                html.Td(f"{p.get('volume', 0):.2f}"),
                html.Td(f"${p.get('pnl', 0):.2f}", style={"color": pnl_color}),
            ]))
        return html.Table(
            style={"width": "100%", "borderCollapse": "collapse"},
            children=[header] + rows,
        )

    def _build_trades_table(self, trades: list[dict]) -> html.Table:
        if not trades:
            return html.Table(style={"width": "100%", "color": "#b0b0b0"}, children=[
                html.Tr([html.Td("No trades yet", style={"textAlign": "center", "padding": "20px"})])
            ])
        header = html.Tr([html.Th(h) for h in ["Time", "Side", "Entry", "Exit", "P&L"]])
        rows = []
        for t in trades[-20:]:  # last 20
            pnl_color = "#00ff88" if t.get("pnl", 0) >= 0 else "#ff4757"
            rows.append(html.Tr([
                html.Td(str(t.get("time", ""))),
                html.Td(t.get("side", "")),
                html.Td(f"{t.get('entry', 0):.5f}"),
                html.Td(f"{t.get('exit', 0):.5f}"),
                html.Td(f"${t.get('pnl', 0):.2f}", style={"color": pnl_color}),
            ]))
        return html.Table(
            style={"width": "100%", "borderCollapse": "collapse"},
            children=[header] + rows,
        )

    def _load_data(self) -> dict[str, Any]:
        """Load data from the experiments directory or return defaults."""
        data: dict[str, Any] = {
            "metrics": {},
            "equity_curve": [10000.0],
            "trades": [],
            "positions": [],
            "regime": {"label": "--", "trend": "--", "adx": "--"},
            "status": "IDLE",
        }
        project_root = Path(__file__).resolve().parents[3]
        exp_dir = project_root / "experiments"
        if self.experiment_id:
            exp_file = exp_dir / f"{self.experiment_id}.json"
            if exp_file.exists():
                try:
                    with open(exp_file) as f:
                        stored = json.load(f)
                    data.update(stored)
                    data["status"] = stored.get("status", "BACKTEST")
                except Exception:
                    pass
        return data

    def run(self, port: int = 8056) -> None:
        """Start the Dash server."""
        print(f"Dashboard running at http://127.0.0.1:{port}")
        self.app.run(debug=False, port=port)


if __name__ == "__main__":
    Dashboard().run()
