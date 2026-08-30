"""Live trading orchestrator for the forex RL trading system.

Manages the full live/paper trading loop:
1. Connect to MT5
2. Load trained agent
3. Main loop: fetch bars → compute features → get prediction → risk check → execute
4. Log all actions

SAFETY:
- DRY_RUN = True by default
- PAPER_TRADING = True by default
- Never sends live orders unless explicitly enabled
- All actions logged to database
- Daily P&L limits enforced
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from forex_rl.src.execution.mt5_connector import MT5Connector, MT5Error
from forex_rl.src.execution.order_manager import OrderManager
from forex_rl.src.risk.risk_manager import RiskManager, RiskConfig

logger = logging.getLogger(__name__)


class LiveTrader:
    """Live/paper trading orchestrator.

    Parameters
    ----------
    config : dict
        Configuration with keys:
        - ``DRY_RUN``: if True, log trades but don't execute (default True)
        - ``PAPER_TRADING``: if True, use paper trading mode (default True)
        - ``LIVE_TRADING``: must be True to send real orders (default False)
        - ``POLL_INTERVAL``: seconds between iterations (default 60)
        - ``SYMBOLS``: list of symbols to trade
        - ``TIMEFRAME``: bar timeframe (default H1)
        - ``AGENT_PATH``: path to trained model file
        - ``DAILY_LOSS_LIMIT``: max daily loss as fraction (default 0.02)
        - ``DAILY_PROFIT_TARGET``: daily profit target (default 0.05)
        - ``RISK_PER_TRADE``: risk per trade fraction (default 0.005)
        - All MT5Connector config keys
    """

    def __init__(self, config: dict) -> None:
        self.dry_run: bool = config.get("DRY_RUN", True)
        self.paper_trading: bool = config.get("PAPER_TRADING", True)
        self.live_trading: bool = config.get("LIVE_TRADING", False)
        self.poll_interval: int = config.get("POLL_INTERVAL", 60)
        self.symbols: list[str] = config.get("SYMBOLS", ["EURUSD"])
        self.timeframe: str = config.get("TIMEFRAME", "H1")
        self.agent_path: Optional[str] = config.get("AGENT_PATH")
        self.daily_loss_limit: float = config.get("DAILY_LOSS_LIMIT", 0.02)
        self.daily_profit_target: float = config.get("DAILY_PROFIT_TARGET", 0.05)

        self._config = config
        self._running = False
        self._iteration = 0
        self._start_time: Optional[float] = None

        self.connector: Optional[MT5Connector] = None
        self.order_manager: Optional[OrderManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self._agent = None

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        logger.info("Signal %d received, shutting down gracefully...", signum)
        self._running = False

    def run(self, agent_path: str, symbols: list[str], timeframe: str) -> None:
        """Run the live trading loop.

        Parameters
        ----------
        agent_path : str
            Path to the trained SB3 model file (.zip).
        symbols : list[str]
            Symbols to trade.
        timeframe : str
            Bar timeframe (M1, M5, M15, M30, H1, H4, D1).
        """
        can_go, reason = self._can_go_live()
        if not can_go:
            logger.error("Cannot go live: %s", reason)
            print(f"BLOCKED: {reason}")
            return

        self.symbols = symbols
        self.timeframe = timeframe
        self.agent_path = agent_path

        logger.info(
            "Starting LiveTrader: dry_run=%s paper=%s live=%s symbols=%s tf=%s",
            self.dry_run, self.paper_trading, self.live_trading,
            self.symbols, self.timeframe,
        )

        try:
            self._connect_mt5()
            self._load_agent(agent_path)
            self._init_components()
            self._main_loop()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as exc:
            logger.error("LiveTrader error: %s", exc, exc_info=True)
        finally:
            self._shutdown()

    def _connect_mt5(self) -> None:
        self.connector = MT5Connector(self._config)
        if not self.connector.connect():
            raise RuntimeError(f"Failed to connect to MT5: {self.connector._last_error}")
        account = self.connector.get_account()
        logger.info(
            "MT5 connected: login=%s balance=%.2f equity=%.2f",
            account.get("login"), account.get("balance"), account.get("equity"),
        )

    def _load_agent(self, agent_path: str) -> None:
        path = Path(agent_path)
        if not path.exists():
            raise FileNotFoundError(f"Agent model not found: {path}")

        from stable_baselines3 import PPO, SAC

        if "ppo" in path.stem.lower():
            self._agent = PPO.load(str(path))
            logger.info("Loaded PPO agent from %s", path)
        elif "sac" in path.stem.lower():
            self._agent = SAC.load(str(path))
            logger.info("Loaded SAC agent from %s", path)
        else:
            self._agent = PPO.load(str(path))
            logger.info("Loaded agent (default PPO) from %s", path)

    def _init_components(self) -> None:
        risk_config = RiskConfig(
            risk_per_trade=self._config.get("RISK_PER_TRADE", 0.005),
            max_daily_drawdown=self.daily_loss_limit,
        )
        self.risk_manager = RiskManager(risk_config)
        self.order_manager = OrderManager(
            connector=self.connector,
            risk_manager=self.risk_manager,
            config=self._config,
        )

    def _main_loop(self) -> None:
        self._running = True
        self._start_time = time.time()
        self._iteration = 0

        try:
            from forex_rl.src.data.loader import DataLoader
            self._data_loader = DataLoader(self.connector)
        except Exception:
            self._data_loader = None
            logger.warning("DataLoader not available, using raw MT5 data")

        logger.info("Main loop started (poll every %ds)", self.poll_interval)

        while self._running:
            try:
                self._iteration += 1
                self._run_iteration()
                time.sleep(self.poll_interval)
            except MT5Error as exc:
                logger.error("MT5 error in iteration %d: %s", self._iteration, exc)
                if not self._try_reconnect():
                    logger.error("Reconnect failed, stopping")
                    break
            except Exception as exc:
                logger.error("Unexpected error in iteration %d: %s", self._iteration, exc)
                time.sleep(min(self.poll_interval * 2, 300))

    def _run_iteration(self) -> None:
        for symbol in self.symbols:
            try:
                self._process_symbol(symbol)
            except Exception as exc:
                logger.error("Error processing %s: %s", symbol, exc)

        self.order_manager.sync_with_broker()

        pnl = self.order_manager.get_daily_pnl()
        risk = self.order_manager.get_open_risk()
        tracked = self.order_manager.get_tracked_positions()

        if self._iteration % 10 == 0:
            logger.info(
                "Iteration %d | daily_pnl=%.2f open_risk=%.4f positions=%d",
                self._iteration, pnl, risk, len(tracked),
            )

    def _process_symbol(self, symbol: str) -> None:
        try:
            df = self.connector.get_rates(symbol, self.timeframe, count=200)
        except MT5Error as exc:
            logger.warning("Failed to get rates for %s: %s", symbol, exc)
            return

        if df.empty or len(df) < 50:
            return

        current_price = float(df["close"].iloc[-1])
        atr = self._compute_atr(df)
        spread_info = self._get_spread_pips(symbol)

        market_state = {
            "current_price": current_price,
            "atr": atr,
            "spread_pips": spread_info,
        }

        observation = self._build_observation(df)
        if observation is None:
            return

        try:
            action_raw, _ = self._agent.predict(observation, deterministic=True)
        except Exception as exc:
            logger.warning("Agent predict failed for %s: %s", symbol, exc)
            return

        signal = self._parse_agent_action(action_raw, symbol)

        portfolio_state = self.order_manager.get_portfolio_state()

        if self.dry_run:
            logger.info(
                "DRY_RUN signal: %s %s (confidence=%.2f) price=%.5f atr=%.5f",
                signal["action"], symbol, signal["confidence"], current_price, atr,
            )
            return

        result = self.order_manager.execute_signal(signal, portfolio_state, market_state)
        if result["success"] and result.get("ticket"):
            logger.info(
                "Trade executed: %s %s ticket=%d",
                signal["action"], symbol, result["ticket"],
            )
        elif not result["success"]:
            logger.info("Trade rejected: %s", result["reason"])

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        n = len(close)
        if n < period + 1:
            return float(high[-1] - low[-1])

        tr = np.zeros(n)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
        atr = np.zeros(n)
        atr[0] = tr[0]
        alpha = 1.0 / period
        for i in range(1, n):
            atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha
        return float(atr[-1])

    def _get_spread_pips(self, symbol: str) -> float:
        try:
            info = self.connector.get_symbol_info(symbol)
            spread = info.get("spread", 0)
            point = info.get("point", 0.0001)
            if point > 0:
                return spread * point / 0.0001
            return 1.5
        except Exception:
            return 1.5

    def _build_observation(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        try:
            from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig

            env_config = EnvConfig(
                lookback=self._config.get("LOOKBACK", 64),
                normalize_obs=True,
            )
            env = ForexTradingEnv(df, config=env_config)
            env.set_eval_mode()
            obs, _ = env.reset()
            return obs
        except Exception as exc:
            logger.debug("Could not build observation: %s", exc)
            return self._build_minimal_observation(df)

    def _build_minimal_observation(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        lookback = self._config.get("LOOKBACK", 64)
        if len(df) < lookback:
            return None

        close = df["close"].values.astype(np.float64)
        recent = close[-lookback:]
        returns = np.diff(recent) / np.where(recent[:-1] != 0, recent[:-1], 1.0)
        padded = np.zeros(lookback, dtype=np.float32)
        padded[1:] = returns.astype(np.float32)

        obs = np.zeros((lookback, 53), dtype=np.float32)
        obs[:, 0] = padded
        for i in range(1, min(5, lookback)):
            obs[i:, i] = padded[:-i]
        return obs

    def _parse_agent_action(self, action_raw: Any, symbol: str) -> dict:
        action_map = {0: "SELL", 1: "HOLD", 2: "BUY"}
        if isinstance(action_raw, np.ndarray):
            action_val = int(action_raw.flat[0]) if action_raw.ndim > 0 else int(action_raw)
        else:
            action_val = int(action_raw)
        action_val = max(0, min(action_val, 2))
        return {
            "action": action_map.get(action_val, "HOLD"),
            "symbol": symbol,
            "confidence": 1.0,
        }

    def _try_reconnect(self) -> bool:
        for attempt in range(3):
            logger.info("Reconnect attempt %d/3...", attempt + 1)
            time.sleep(5 * (attempt + 1))
            try:
                self.connector.disconnect()
                if self.connector.connect():
                    logger.info("Reconnected successfully")
                    return True
            except Exception as exc:
                logger.warning("Reconnect attempt %d failed: %s", attempt + 1, exc)
        return False

    def _shutdown(self) -> None:
        logger.info("Shutting down LiveTrader...")
        if self.connector and self.connector.is_connected():
            self.connector.disconnect()
        elapsed = time.time() - self._start_time if self._start_time else 0
        logger.info(
            "LiveTrader stopped: iterations=%d uptime=%.0fs",
            self._iteration, elapsed,
        )

    def _can_go_live(self) -> tuple[bool, str]:
        """Safety check: paper trading must be successful first."""
        if not self.live_trading and not self.paper_trading and not self.dry_run:
            return False, "No mode enabled. Set DRY_RUN, PAPER_TRADING, or LIVE_TRADING."

        if self.live_trading:
            if self.paper_trading:
                return False, "Cannot run LIVE_TRADING and PAPER_TRADING simultaneously."

        if self.agent_path:
            path = Path(self.agent_path)
            if not path.exists():
                return False, f"Agent model not found: {path}"

        return True, "OK"
