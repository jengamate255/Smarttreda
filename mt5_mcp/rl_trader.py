"""RL Trader — live trading wrapper for trained forex_dojo models.

Loads a trained PPO/reinforce model and uses it to make trading decisions
on live market data.

Usage:
    from mt5_mcp.rl_trader import RLTrader
    trader = RLTrader()
    decision = trader.decide("EURUSD", timeframe="H1")
    # decision = {"action": "BUY"|"SELL"|"NONE", "confidence": 0.0-1.0, ...}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from mt5_mcp.client import get_client
from mt5_mcp.config import get_settings
from mt5_mcp.indicators import atr, rsi

logger = logging.getLogger("rl_trader")

MODELS_DIR = Path(__file__).parent / "models" / "dojo"


class RLTrader:
    """Live trading wrapper for trained RL models."""

    def __init__(self):
        self.models: dict[str, dict] = {}  # symbol -> loaded model
        self.nn_cache: dict[str, object] = {}  # symbol -> instantiated NN

    def _load_model(self, symbol: str, timeframe: str = "H1") -> dict | None:
        """Load a trained model for the given symbol."""
        key = f"{symbol}_{timeframe}"
        if key in self.models:
            return self.models[key]

        model_path = MODELS_DIR / f"{symbol}_{timeframe}" / "model.json"
        if not model_path.exists():
            logger.debug("no trained model for %s", key)
            return None

        try:
            data = json.loads(model_path.read_text(encoding="utf-8"))
            self.models[key] = data
            logger.info("loaded RL model for %s (architecture=%s)", key, data.get("architecture", "unknown"))
            return data
        except Exception as exc:
            logger.warning("failed to load model %s: %s", key, exc)
            return None

    def _get_nn(self, symbol: str, timeframe: str = "H1"):
        """Get or instantiate the neural network for a model."""
        key = f"{symbol}_{timeframe}"
        if key in self.nn_cache:
            return self.nn_cache[key]

        model_data = self._load_model(symbol, timeframe)
        if not model_data:
            return None

        try:
            from mt5_mcp.forex_dojo import PPONN, SimpleNN

            input_size = len(model_data["W1"])
            hidden_size = len(model_data["b1"])
            output_size = len(model_data["b2"])
            lr = model_data.get("lr", 0.001)

            if model_data.get("architecture") == "ppo":
                nn = PPONN(input_size, hidden_size, output_size, lr,
                          clip_epsilon=model_data.get("clip_epsilon", 0.2),
                          gae_lambda=model_data.get("gae_lambda", 0.95))
            else:
                nn = SimpleNN(input_size, hidden_size, output_size, lr)

            # Load weights
            nn.W1 = np.array(model_data["W1"])
            nn.b1 = np.array(model_data["b1"])
            nn.W2 = np.array(model_data["W2"])
            nn.b2 = np.array(model_data["b2"])

            self.nn_cache[key] = nn
            return nn
        except Exception as exc:
            logger.warning("failed to instantiate NN for %s: %s", key, exc)
            return None

    def _build_features(self, symbol: str, timeframe: str = "H1", lookback: int = 100) -> np.ndarray | None:
        """Build feature matrix from live market data."""
        try:
            client = get_client()
            client.ensure_connected()
            frame = client.rates(symbol, timeframe, lookback)
        except Exception as exc:
            logger.warning("cannot get data for %s: %s", symbol, exc)
            return None

        if frame is None or len(frame) < 50:
            return None

        # Compute indicators
        close = frame["close"].values
        high = frame["high"].values
        low = frame["low"].values
        volume = frame.get("tick_volume", frame.get("volume", np.ones(len(frame)))).values

        # Returns
        returns = np.diff(close) / close[:-1]
        returns = np.concatenate([[0.0], returns])

        # ATR
        atr_series = atr(frame, 14)
        atr_vals = atr_series.values if atr_series is not None else np.ones(len(frame))

        # RSI
        rsi_series = rsi(frame, 14)
        rsi_vals = rsi_series.values if rsi_series is not None else np.full(len(frame), 50.0)

        # EMA fast/slow
        ema_fast = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema_slow = pd.Series(close).ewm(span=26, adjust=False).mean().values

        # MACD
        macd = ema_fast - ema_slow

        # Bollinger Band position
        bb_mid = pd.Series(close).rolling(20).mean().values
        bb_std = pd.Series(close).rolling(20).std().values
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_pos = np.where(bb_std > 0, (close - bb_lower) / (bb_upper - bb_lower + 1e-10), 0.5)

        # Volume ratio
        vol_mean = pd.Series(volume).rolling(20).mean().values
        vol_ratio = np.where(vol_mean > 0, volume / (vol_mean + 1e-10), 1.0)

        # Hour encoding
        hours = pd.to_datetime(frame["time"]).dt.hour.values
        hour_sin = np.sin(2 * np.pi * hours / 24)
        hour_cos = np.cos(2 * np.pi * hours / 24)

        # Stack features
        features = np.column_stack([
            returns, atr_vals, rsi_vals, ema_fast, ema_slow, macd,
            bb_pos, vol_ratio, hour_sin, hour_cos,
        ])

        return features

    def _build_state(self, features: np.ndarray, position: int = 0, entry_price: float = 0.0,
                     balance: float = 10000.0, current_atr: float = 0.0, bars_in_trade: int = 0) -> list[float]:
        """Build state vector for the NN (matches forex_dojo._get_state)."""
        bar = features[-1]  # latest bar
        state = list(bar)

        # Position info
        state.append(float(position))
        state.append(float(entry_price > 0))

        if position != 0 and entry_price > 0:
            current_price = float(bar[0])  # approximate with first feature (returns)
            # Use close price from features (we'd need to pass it properly)
            pnl_pct = 0.0  # simplified for live trading
            state.append(pnl_pct)
            state.append(1.0)  # dist_to_sl neutral
            state.append(min(bars_in_trade / 50.0, 1.0))
        else:
            state.append(0.0)
            state.append(1.0)
            state.append(0.0)

        state.append(balance / 10000.0 - 1.0)  # normalized balance change

        return state

    def decide(self, symbol: str, timeframe: str = "H1", confidence_threshold: float = 0.6) -> dict:
        """Get trading decision from RL model.

        Returns:
            {
                "action": "BUY"|"SELL"|"NONE",
                "confidence": 0.0-1.0,
                "probabilities": [p_flat, p_buy, p_sell],
                "model_loaded": bool,
                "reason": str,
            }
        """
        result = {
            "action": "NONE",
            "confidence": 0.0,
            "probabilities": [0.0, 0.0, 0.0],
            "model_loaded": False,
            "reason": "",
        }

        nn = self._get_nn(symbol, timeframe)
        if nn is None:
            result["reason"] = f"no trained model for {symbol}_{timeframe}"
            return result

        result["model_loaded"] = True

        features = self._build_features(symbol, timeframe, lookback=100)
        if features is None:
            result["reason"] = "cannot build features (no data)"
            return result

        state = self._build_state(features, position=0, entry_price=0.0, balance=10000.0)

        try:
            probs = nn.forward(state)
            result["probabilities"] = probs

            # Actions: 0=FLAT/CLOSE, 1=BUY, 2=SELL
            p_flat, p_buy, p_sell = probs

            # Pick action with highest probability
            if p_buy > p_sell and p_buy > p_flat:
                if p_buy >= confidence_threshold:
                    result["action"] = "BUY"
                    result["confidence"] = p_buy
                else:
                    result["reason"] = f"BUY prob {p_buy:.3f} < threshold {confidence_threshold}"
            elif p_sell > p_buy and p_sell > p_flat:
                if p_sell >= confidence_threshold:
                    result["action"] = "SELL"
                    result["confidence"] = p_sell
                else:
                    result["reason"] = f"SELL prob {p_sell:.3f} < threshold {confidence_threshold}"
            else:
                result["reason"] = f"FLAT prob {p_flat:.3f} is highest"

        except Exception as exc:
            result["reason"] = f"NN forward pass failed: {exc}"
            logger.warning("RL decision failed for %s: %s", symbol, exc)

        return result


# Singleton instance
_rl_trader: RLTrader | None = None


def get_rl_trader() -> RLTrader:
    global _rl_trader
    if _rl_trader is None:
        _rl_trader = RLTrader()
    return _rl_trader


def rl_decide(symbol: str, timeframe: str = "H1", confidence_threshold: float = 0.6) -> dict:
    """Convenience function: get RL trading decision for a symbol."""
    trader = get_rl_trader()
    return trader.decide(symbol, timeframe, confidence_threshold)
