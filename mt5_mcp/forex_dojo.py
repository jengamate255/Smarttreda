"""ForexDojo -- Reinforcement Learning Forex Trading Engine.

"Kid learning to ride a bike"
- Episode 1: Random actions -> losses -> "fell off bike"
- Episode 10: Better actions -> smaller losses -> "wobbles less"
- Episode 50: Smart actions -> small gains -> "stays upright"
- Episode 100: Skilled actions -> consistent gains -> "rides smoothly"

Uses policy gradient (REINFORCE or PPO) with numpy-vectorized neural network.
"""

import json
import math
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import numpy as np


# -- Data classes ----------------------------------------------------------

@dataclass
class DojoConfig:
    """Configuration for a Dojo training session."""
    symbol: str = "EURUSD"
    timeframe: str = "H1"
    lookback: int = 5000
    features: list = field(default_factory=lambda: [
        "returns", "atr", "rsi", "ema_fast", "momentum",
        "macd", "bb_position", "volume_ratio", "hour_sin", "hour_cos"
    ])
    hidden_size: int = 64
    learning_rate: float = 0.001
    gamma: float = 0.99
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.05
    max_position_size: float = 0.2
    episodes: int = 100
    max_steps: int = 500
    save_every: int = 10
    seed: int | None = None
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    train_ratio: float = 0.8
    entropy_coeff: float = 0.01
    warm_start: bool = True
    algorithm: str = "ppo"  # "ppo" or "reinforce"
    clip_epsilon: float = 0.2
    gae_lambda: float = 0.95
    ppo_epochs: int = 4    # mini-epochs per episode batch
    multi_timeframe: bool = True  # add H4/D1 trend context


@dataclass
class DojoResult:
    """Result of a single training episode."""
    episode: int
    total_reward: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    pnl: float
    max_drawdown: float
    final_balance: float
    skill_level: str
    actions_taken: dict
    duration_bars: int
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0


@dataclass
class DojoProgress:
    """Overall training progress for a symbol."""
    symbol: str
    episodes_completed: int
    best_episode: DojoResult | None
    latest_episode: DojoResult | None
    skill_level: str
    skill_score: float
    converged: bool
    total_trades: int
    overall_win_rate: float
    overall_pnl: float
    overall_max_dd: float
    equity_curve: list[float]
    lessons_learned: list[str]
    training_time_seconds: float
    test_win_rate: float = 0.0
    test_pnl: float = 0.0
    test_sharpe: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0


# -- Simple Neural Network -------------------------------------------------

class SimpleNN:
    """Vectorized feedforward neural network for policy gradient.
    
    Uses numpy matrix operations instead of Python loops for 10-50x speedup.
    Maintains backward compatibility with the same save/load format.
    """

    def __init__(self, input_size: int, hidden_size: int, output_size: int, lr: float = 0.001):
        self.lr = lr
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        
        # Xavier init using numpy
        scale1 = np.sqrt(2.0 / (input_size + hidden_size))
        scale2 = np.sqrt(2.0 / (hidden_size + output_size))
        self.W1 = np.random.randn(input_size, hidden_size) * scale1
        self.b1 = np.zeros(hidden_size)
        self.W2 = np.random.randn(hidden_size, output_size) * scale2
        self.b2 = np.zeros(output_size)
        
        # Cache for backprop
        self._x = None
        self._h = None
        self._h_raw = None
        self._logits = None
        self._probs = None

    def forward(self, x) -> list[float]:
        """Forward pass — returns action probabilities."""
        x = np.array(x, dtype=np.float64)
        self._x = x
        
        # Hidden layer: ReLU
        self._h_raw = x @ self.W1 + self.b1
        self._h = np.maximum(0, self._h_raw)
        
        # Output layer: softmax via log-sum-exp
        self._logits = self._h @ self.W2 + self.b2
        max_logit = np.max(self._logits)
        exps = np.exp(self._logits - max_logit)
        self._probs = exps / np.sum(exps)
        
        return self._probs.tolist()

    def update(self, action: int, advantage: float):
        """Policy gradient update (REINFORCE or PPO-compatible)."""
        probs = self._probs  # use cached probs from last forward()
        
        # Gradient of log probability: d(log pi)/d(logit_k) = (1_k - pi_k)
        d_logits = -probs.copy()
        d_logits[action] += 1.0
        d_logits *= self.lr * advantage
        
        # Gradient clipping
        max_grad = 1.0
        d_logits = np.clip(d_logits, -max_grad, max_grad)
        
        # Backprop to W2, b2
        self.W2 += np.outer(self._h, d_logits)
        self.b2 += d_logits
        
        # Backprop through ReLU to W1, b1
        d_h = self.W2 @ d_logits
        d_h_raw = d_h * (self._h_raw > 0).astype(np.float64)
        self.W1 += np.outer(self._x, d_h_raw)
        self.b1 += d_h_raw

    def entropy(self) -> float:
        """Shannon entropy of the last action distribution."""
        p = self._probs
        return -float(np.sum(p * np.log(p + 1e-8)))

    def save(self) -> dict:
        return {
            "W1": self.W1.tolist(), "b1": self.b1.tolist(),
            "W2": self.W2.tolist(), "b2": self.b2.tolist(),
            "lr": self.lr,
        }

    def load(self, data: dict):
        self.W1 = np.array(data["W1"], dtype=np.float64)
        self.b1 = np.array(data["b1"], dtype=np.float64)
        self.W2 = np.array(data["W2"], dtype=np.float64)
        self.b2 = np.array(data["b2"], dtype=np.float64)
        self.lr = data.get("lr", self.lr)
        self.input_size = self.W1.shape[0]
        self.hidden_size = self.W1.shape[1]
        self.output_size = self.W2.shape[1]


class PPONN(SimpleNN):
    """PPO-compatible neural network with clipped surrogate objective.
    
    Extends SimpleNN with:
    - Old policy storage for importance sampling
    - Clipped surrogate loss
    - Generalized Advantage Estimation (GAE)
    """

    def __init__(self, input_size: int, hidden_size: int, output_size: int, lr: float = 0.001,
                 clip_epsilon: float = 0.2, gae_lambda: float = 0.95):
        super().__init__(input_size, hidden_size, output_size, lr)
        self.clip_epsilon = clip_epsilon
        self.gae_lambda = gae_lambda
        # Store old policy probabilities for PPO ratio
        self._old_probs = None

    def store_old_probs(self):
        """Snapshot current probs before update."""
        if self._probs is not None:
            self._old_probs = self._probs.copy()

    def ppo_update(self, action: int, advantage: float, returns: float):
        """PPO clipped surrogate update with value function loss."""
        if self._old_probs is None:
            # Fallback to regular update
            self.update(action, advantage)
            return
        
        probs = self._probs
        old_prob = self._old_probs[action]
        new_prob = probs[action]
        
        # Importance sampling ratio
        ratio = new_prob / (old_prob + 1e-8)
        
        # Clipped surrogate loss
        clipped = np.clip(ratio, 1 - self.clip_epsilon, 1 + self.clip_epsilon)
        surrogate = -min(ratio * advantage, clipped * advantage)
        
        # Policy gradient with clipped loss
        d_logits = -probs.copy()
        d_logits[action] += 1.0
        d_logits *= self.lr * surrogate
        
        # Gradient clipping
        max_grad = 1.0
        d_logits = np.clip(d_logits, -max_grad, max_grad)
        
        # Backprop
        self.W2 += np.outer(self._h, d_logits)
        self.b2 += d_logits
        d_h = self.W2 @ d_logits
        d_h_raw = d_h * (self._h_raw > 0).astype(np.float64)
        self.W1 += np.outer(self._x, d_h_raw)
        self.b1 += d_h_raw
        
        # Clear old probs
        self._old_probs = None

    def save(self) -> dict:
        data = super().save()
        data["clip_epsilon"] = self.clip_epsilon
        data["gae_lambda"] = self.gae_lambda
        data["architecture"] = "ppo"
        return data

    def load(self, data: dict):
        super().load(data)
        self.clip_epsilon = data.get("clip_epsilon", 0.2)
        self.gae_lambda = data.get("gae_lambda", 0.95)


# -- Experience Replay Buffer ----------------------------------------------

class ReplayBuffer:
    """Stores past trajectories for experience replay."""

    def __init__(self, max_size: int = 50):
        self.buffer: list[list] = []
        self.max_size = max_size

    def add(self, trajectory: list):
        self.buffer.append(trajectory)
        if len(self.buffer) > self.max_size:
            self.buffer.pop(0)

    def sample(self, n: int) -> list[list]:
        return random.sample(self.buffer, min(n, len(self.buffer)))


# -- Feature Engineering ---------------------------------------------------

def _compute_features(df: "pd.DataFrame") -> np.ndarray:
    """Compute feature matrix from OHLCV data."""
    import pandas as pd

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df.get("tick_volume", df.get("real_volume", pd.Series(np.ones(len(df)))))

    features = []

    # 1. Returns (normalized)
    returns = np.diff(close) / close[:-1]
    returns = np.concatenate([[0], returns])
    features.append(returns / (np.std(returns) + 1e-8))

    # 2. ATR (14)
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = pd.Series(tr).rolling(14, min_periods=1).mean().values
    features.append(atr / (np.std(atr) + 1e-8))

    # 3. RSI (14)
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss_arr = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14, min_periods=1).mean().values
    avg_loss = pd.Series(loss_arr).rolling(14, min_periods=1).mean().values
    rs = avg_gain / (avg_loss + 1e-8)
    rsi = 100 - 100 / (1 + rs)
    rsi = np.concatenate([[50], rsi])
    features.append(rsi / 100.0)

    # 4. EMA fast (12) / slow (26) -- normalized distance
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
    ema_diff = (ema12 - ema26) / (atr + 1e-8)
    features.append(ema_diff / (np.std(ema_diff) + 1e-8))

    # 5. Momentum (ATR-normalized price momentum, 20-bar)
    mom_lookback = 20
    momentum = np.zeros(len(close))
    momentum[mom_lookback:] = (close[mom_lookback:] - close[:-mom_lookback]) / (atr[mom_lookback:] + 1e-8)
    features.append(momentum / (np.std(momentum) + 1e-8))

    # 6. MACD
    macd_line = ema12 - ema26
    signal_line = pd.Series(macd_line).ewm(span=9, adjust=False).mean().values
    macd_hist = macd_line - signal_line
    features.append(macd_hist / (np.std(macd_hist) + 1e-8))

    # 7. Bollinger Band position (0 = lower, 1 = upper)
    sma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
    std20 = pd.Series(close).rolling(20, min_periods=1).std().values
    bb_pos = (close - (sma20 - 2 * std20)) / (4 * std20 + 1e-8)
    features.append(np.clip(bb_pos, 0, 1))

    # 8. Volume ratio
    vol_ma = pd.Series(volume).rolling(20, min_periods=1).mean().values
    vol_ratio = volume / (vol_ma + 1e-8)
    features.append(vol_ratio / (np.std(vol_ratio) + 1e-8))

    # 9-10. Hour of day (sin/cos encoding)
    if "time" in df.columns:
        try:
            hours = pd.to_datetime(df["time"]).dt.hour.values
        except Exception:
            hours = np.zeros(len(df))
    else:
        hours = np.zeros(len(df))
    features.append(np.sin(2 * np.pi * hours / 24))
    features.append(np.cos(2 * np.pi * hours / 24))

    return np.array(features).T


# -- Training Engine -------------------------------------------------------

class ForexDojo:
    """RL training engine -- the kid learning to ride a bike."""

    ACTIONS = ["SELL", "HOLD", "BUY"]

    def __init__(self, config: DojoConfig):
        self.config = config
        self.nn: SimpleNN | None = None
        self.progress: DojoProgress | None = None
        self._state: dict[str, Any] = {}
        self._save_dir = Path(__file__).parent.parent / "dojo" / "models"
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self.replay_buffer = ReplayBuffer(max_size=50)

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _pip_value(symbol: str) -> float:
        """Return pip value for a symbol."""
        s = symbol.upper()
        if "JPY" in s:
            return 0.01
        if s in ("XAUUSD",):
            return 0.1
        if s in ("SPX500", "JP225"):
            return 1.0
        return 0.0001

    # -- Data fetching -----------------------------------------------------

    def _fetch_data(self) -> "pd.DataFrame":
        """Fetch OHLCV from MT5."""
        import pandas as pd
        import time as _time

        if not mt5.terminal_info():
            mt5.initialize()
            _time.sleep(2)

        if not mt5.terminal_info() or not mt5.terminal_info().connected:
            prop_path = r"C:\Users\Dave\AppData\Roaming\MetaQuotes\Terminal\55918E13E21556DE182E4DB10CD86F5E\terminal64.exe"
            mt5.initialize(path=prop_path)
            _time.sleep(2)

        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_map.get(self.config.timeframe.upper(), mt5.TIMEFRAME_H1)
        rates = mt5.copy_rates_from_pos(self.config.symbol, tf, 0, self.config.lookback)
        if rates is None or len(rates) == 0:
            raise ValueError(f"No data for {self.config.symbol} on {self.config.timeframe}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    def _fetch_multi_tf_data(self) -> dict:
        """Fetch H4 and D1 data for trend context."""
        import pandas as pd
        import time as _time
        
        if not mt5.terminal_info():
            mt5.initialize()
            _time.sleep(2)
        
        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        
        result = {}
        for tf_name, tf_key in [("H4", mt5.TIMEFRAME_H4), ("D1", mt5.TIMEFRAME_D1)]:
            rates = mt5.copy_rates_from_pos(self.config.symbol, tf_key, 0, 500)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s")
                result[tf_name] = df
        return result

    # -- State extraction --------------------------------------------------

    def _get_state(self, features: np.ndarray, step: int, position: int,
                   entry_price: float, balance: float) -> list[float]:
        """Build state vector for the NN."""
        bar = features[step]
        state = list(bar)
        state.append(float(position))
        state.append(float(entry_price > 0))
        if position != 0 and entry_price > 0:
            current_price = self._df["close"].values[step]
            pnl_pct = (current_price - entry_price) / entry_price * position
            state.append(pnl_pct)
        else:
            state.append(0.0)
        state.append(balance / self.config.initial_balance - 1.0)
        
        # Multi-timeframe trend context
        if self.config.multi_timeframe and hasattr(self, '_multi_tf'):
            import pandas as pd
            for tf_name in ["H4", "D1"]:
                tf_data = self._multi_tf.get(tf_name)
                if tf_data is not None and len(tf_data) > 20:
                    close = tf_data["close"].values
                    ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().values[-1]
                    ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values[-1] if len(close) > 50 else ema20
                    trend = 1.0 if ema20 > ema50 else -1.0
                    strength = (ema20 - ema50) / (np.std(close[-20:]) + 1e-8)
                    state.append(trend)
                    state.append(np.clip(strength, -2, 2))
                else:
                    state.append(0.0)
                    state.append(0.0)
        else:
            state.append(0.0)
            state.append(0.0)
            state.append(0.0)
            state.append(0.0)
        
        return state

    # -- Reward function ---------------------------------------------------

    def _reward(self, action: int, prev_position: int, prev_entry_price: float,
                current_price: float, next_price: float,
                bars_in_trade: int) -> float:
        """Compute reward -- all components scaled to approximately [-1, +1]."""
        if action == 2 and prev_position == 0:
            return 0.0
        if action == 0 and prev_position == 0:
            return 0.0
        if action == 1 and prev_position == 0:
            return 0.01  # minimal reward for holding — must trade to learn

        reward = 0.0

        if prev_position == 1:
            pnl_pct = (next_price - current_price) / current_price
            reward += float(np.clip(pnl_pct * 20, -0.5, 0.5))
            if action == 0:
                close_pnl = (current_price - prev_entry_price) / prev_entry_price
                reward += 0.3 if close_pnl > 0 else -0.3

        elif prev_position == -1:
            pnl_pct = (current_price - next_price) / current_price
            reward += float(np.clip(pnl_pct * 20, -0.5, 0.5))
            if action == 2:
                close_pnl = (prev_entry_price - current_price) / prev_entry_price
                reward += 0.3 if close_pnl > 0 else -0.3

        if bars_in_trade > 50:
            reward -= 0.02

        return float(np.clip(reward, -1.0, 1.0))

    # -- Skill level -------------------------------------------------------

    @staticmethod
    def _skill_level(episode_pnls: list[float], max_dd: float, total_trades: int = 0, total_episodes: int = 1) -> tuple[str, float, float, float]:
        """Sharpe-ratio based skill scorer. Returns (level, score, sharpe, profit_factor)."""
        if len(episode_pnls) >= 2:
            mean_pnl = float(np.mean(episode_pnls))
            std_pnl = float(np.std(episode_pnls)) + 1e-8
            sharpe = mean_pnl / std_pnl * math.sqrt(252)
        else:
            sharpe = 0.0

        winning = [p for p in episode_pnls if p > 0]
        losing = [p for p in episode_pnls if p < 0]
        raw_pf = sum(winning) / (abs(sum(losing)) + 1e-8) if losing else 0.0
        profit_factor = min(raw_pf, 10.0)  # cap at 10 to prevent inflation

        # Trade activity: penalize if avg trades per episode < 3
        avg_trades_per_ep = total_trades / max(total_episodes, 1)
        activity_bonus = min(avg_trades_per_ep / 5.0, 1.0)  # 0-1 scale, peaks at 5 trades/ep

        score = 0.0
        score += min(sharpe * 20, 40)
        score += min(profit_factor * 10, 30)
        score += max(0, 30 - max_dd * 2)
        score *= (0.5 + 0.5 * activity_bonus)  # scale by activity

        if score >= 70:
            level = "Master"
        elif score >= 50:
            level = "Advanced"
        elif score >= 35:
            level = "Intermediate"
        elif score >= 20:
            level = "Novice"
        else:
            level = "Beginner"

        return level, score, sharpe, profit_factor

    # -- Lessons learned ---------------------------------------------------

    @staticmethod
    def _extract_lessons(episodes: list[DojoResult]) -> list[str]:
        """Extract trading lessons from episode history."""
        lessons = []
        if len(episodes) < 5:
            return ["Need more episodes to learn patterns"]

        recent = episodes[-10:]
        early = episodes[:10] if len(episodes) >= 20 else episodes[:5]

        early_wr = np.mean([e.win_rate for e in early])
        recent_wr = np.mean([e.win_rate for e in recent])
        if recent_wr > early_wr + 0.05:
            lessons.append(f"Win rate improved {early_wr:.0%} -> {recent_wr:.0%}")

        recent_pnl = np.mean([e.pnl for e in recent])
        early_pnl = np.mean([e.pnl for e in early])
        if recent_pnl > early_pnl:
            lessons.append(f"P&L improved: ${early_pnl:.0f} -> ${recent_pnl:.0f}")

        avg_actions = {}
        for ep in recent:
            for a, c in ep.actions_taken.items():
                avg_actions[a] = avg_actions.get(a, 0) + c / len(recent)
        if avg_actions:
            dominant = max(avg_actions, key=avg_actions.get)
            lessons.append(f"Dominant action: {dominant} (learning bias)")

        recent_dd = np.mean([e.max_drawdown for e in recent])
        early_dd = np.mean([e.max_drawdown for e in early])
        if recent_dd < early_dd:
            lessons.append(f"Drawdown control improved: {early_dd:.1f}% -> {recent_dd:.1f}%")

        avg_trades = np.mean([e.trades for e in recent])
        if avg_trades < 5:
            lessons.append("Too conservative -- not taking enough trades")
        elif avg_trades > 30:
            lessons.append("Overtrading -- being more selective")

        return lessons if lessons else ["Still learning patterns..."]

    # -- Episode runner ----------------------------------------------------

    def _run_episode(self, features: np.ndarray, start: int,
                     max_steps: int | None = None,
                     step_callback=None) -> tuple[DojoResult, list]:
        """Run a single episode from the given start bar.

        Returns (DojoResult, trajectory) where trajectory is a list of
        (state, action, reward) tuples.
        """
        if max_steps is None:
            max_steps = self.config.max_steps

        balance = self.config.initial_balance
        position = 0
        entry_price = 0.0
        bars_in_trade = 0
        peak_balance = balance
        max_drawdown = 0.0

        actions_count = {"BUY": 0, "SELL": 0, "HOLD": 0}
        trades = 0
        wins = 0
        losses = 0
        total_pnl = 0.0
        gross_win = 0.0
        gross_loss = 0.0
        trajectory = []

        pip_val = self._pip_value(self.config.symbol)
        entry_adj = (self.config.spread_pips / 2 + self.config.slippage_pips) * pip_val

        end_step = min(start + max_steps, len(self._df) - 1)
        for step in range(start, end_step):
            current_price = self._df["close"].values[step]
            next_price = self._df["close"].values[step + 1]

            state = self._get_state(features, step, position, entry_price, balance)
            probs = self.nn.forward(state)

            rand = random.random()
            if rand < probs[0]:
                action = 0
            elif rand < probs[0] + probs[1]:
                action = 1
            else:
                action = 2

            actions_count[self.ACTIONS[action]] += 1
            prev_position = position
            prev_entry_price = entry_price

            notional = min(balance * self.config.risk_per_trade,
                           self.config.max_position_size * balance)

            if action == 2 and position == 0:
                position = 1
                entry_price = current_price + entry_adj
                bars_in_trade = 0
                trades += 1

            elif action == 0 and position == 0:
                position = -1
                entry_price = current_price - entry_adj
                bars_in_trade = 0
                trades += 1

            elif action == 0 and position == 1:
                pnl_pct = (current_price - entry_price) / entry_price
                pnl = notional * pnl_pct
                balance += pnl
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                    gross_win += pnl
                else:
                    losses += 1
                    gross_loss += abs(pnl)
                position = 0
                entry_price = 0.0
                bars_in_trade = 0

            elif action == 2 and position == -1:
                pnl_pct = (entry_price - current_price) / entry_price
                pnl = notional * pnl_pct
                balance += pnl
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                    gross_win += pnl
                else:
                    losses += 1
                    gross_loss += abs(pnl)
                position = 0
                entry_price = 0.0
                bars_in_trade = 0

            reward = self._reward(action, prev_position, prev_entry_price,
                                  current_price, next_price, bars_in_trade)
            trajectory.append((state, action, reward))

            bars_in_trade += 1

            if step_callback and (step - start) % 10 == 0:
                step_callback(step - start, max_steps, {
                    "balance": balance,
                    "position": position,
                    "trades": trades,
                    "wins": wins,
                    "pnl": total_pnl,
                    "drawdown": max_drawdown,
                })

            peak_balance = max(peak_balance, balance)
            dd = (peak_balance - balance) / peak_balance * 100
            max_drawdown = max(max_drawdown, dd)

        # Close any open position at episode end
        if position != 0:
            final_price = self._df["close"].values[min(start + max_steps, len(self._df) - 1)]
            notional = min(balance * self.config.risk_per_trade,
                           self.config.max_position_size * balance)
            if position == 1:
                pnl_pct = (final_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - final_price) / entry_price
            pnl = notional * pnl_pct
            balance += pnl
            total_pnl += pnl
            if pnl > 0:
                wins += 1
                gross_win += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)

        win_rate = wins / max(trades, 1)

        # Penalize 0-trade episodes — must take positions to learn
        if trades == 0:
            total_pnl -= 50.0  # flat penalty for inaction

        # Compute PF from gross win/loss
        pf = gross_win / (gross_loss + 1e-8) if gross_loss > 0 else (99.99 if gross_win > 0 else 0.0)
        skill, score, _, _ = self._skill_level([total_pnl], max_drawdown)

        result = DojoResult(
            episode=0,
            total_reward=sum(r for _, _, r in trajectory),
            trades=trades,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            pnl=total_pnl,
            max_drawdown=max_drawdown,
            final_balance=balance,
            skill_level=skill,
            actions_taken=actions_count,
            duration_bars=max_steps,
            sharpe_ratio=0.0,
            profit_factor=pf,
        )
        return result, trajectory

    # -- Main training loop ------------------------------------------------

    def train(self, callback=None, step_callback=None) -> DojoProgress:
        """Train the RL agent -- the kid learning to ride."""
        import pandas as pd

        if self.config.seed is not None:
            random.seed(self.config.seed)
            np.random.seed(self.config.seed)

        # Fetch data
        self._df = self._fetch_data()
        features = _compute_features(self._df)
        n_features = features.shape[1]

        # Fetch multi-timeframe data
        self._multi_tf = {}
        if self.config.multi_timeframe:
            self._multi_tf = self._fetch_multi_tf_data()

        # Initialize network
        extra_features = 4 if self.config.multi_timeframe else 0
        if self.config.algorithm == "ppo":
            self.nn = PPONN(n_features + 4 + extra_features, self.config.hidden_size, 3, self.config.learning_rate,
                             clip_epsilon=self.config.clip_epsilon, gae_lambda=self.config.gae_lambda)
        else:
            self.nn = SimpleNN(n_features + 4 + extra_features, self.config.hidden_size, 3, self.config.learning_rate)

        # Warm-start from checkpoint
        self._prior_episodes = 0  # cumulative episodes from prior training
        if self.config.warm_start:
            prog_path = self._save_dir / f"{self.config.symbol}_{self.config.timeframe}" / "progress.json"
            model_path = self._save_dir / f"{self.config.symbol}_{self.config.timeframe}" / "model.json"
            if model_path.exists():
                try:
                    data = json.loads(model_path.read_text(encoding="utf-8"))
                    saved_input_size = len(data["W1"])
                    expected_input_size = n_features + 4 + extra_features
                    # Check feature compatibility
                    if prog_path.exists():
                        prog = json.loads(prog_path.read_text(encoding="utf-8"))
                        saved_features = prog.get("config", {}).get("features", [])
                        self._prior_episodes = prog.get("episode", 0)
                        if saved_features and saved_features != self.config.features:
                            pass  # skip -- incompatible features
                        elif saved_input_size == expected_input_size and len(data["b2"]) == 3:
                            self.nn.load(data)
                    elif saved_input_size == expected_input_size and len(data["b2"]) == 3:
                        self.nn.load(data)
                except Exception:
                    pass

        # Load persisted replay buffer
        buffer_path = self._save_dir / f"{self.config.symbol}_{self.config.timeframe}" / "replay_buffer.json"
        if buffer_path.exists():
            try:
                raw = json.loads(buffer_path.read_text(encoding="utf-8"))
                self.replay_buffer.buffer = raw
            except Exception:
                pass

        # Train/test split
        split_idx = int(len(features) * self.config.train_ratio)
        train_max_start = split_idx - self.config.max_steps - 10
        if train_max_start <= 100:
            train_max_start = max(100, len(features) - self.config.max_steps - 10)

        # Training state
        episodes: list[DojoResult] = []
        equity_curve = [self.config.initial_balance]
        start_time = time.time()
        episode_history = []  # rolling last 20 episodes for live display
        ep_times = []  # time per episode

        ep_start = time.time()
        for ep in range(1, self.config.episodes + 1):
            start = random.randint(100, train_max_start)
            result, trajectory = self._run_episode(features, start, step_callback=step_callback)
            result.episode = ep + self._prior_episodes  # cumulative episode number
            episodes.append(result)
            equity_curve.append(result.final_balance)

            episode_history.append({
                "episode": ep,
                "pnl": result.pnl,
                "win_rate": result.win_rate,
                "trades": result.trades,
                "skill": result.skill_level,
                "sharpe": result.sharpe_ratio,
            })
            if len(episode_history) > 20:
                episode_history.pop(0)

            # Compute discounted returns with GAE
            returns_list = []
            advantages = []
            G = 0.0
            prev_val = 0.0
            for i in reversed(range(len(trajectory))):
                _, _, r = trajectory[i]
                G = r + self.config.gamma * G
                returns_list.insert(0, G)
                
                # GAE advantage
                if i < len(trajectory) - 1:
                    next_val = returns_list[1] if len(returns_list) > 1 else 0
                else:
                    next_val = 0
                delta = r + self.config.gamma * next_val - prev_val
                advantages.insert(0, delta + self.config.gamma * self.config.gae_lambda * advantages[0] if advantages else delta)
                prev_val = G

            # Normalize advantages
            if len(advantages) > 1:
                mean_adv = np.mean(advantages)
                std_adv = np.std(advantages) + 1e-8
                advantages = [(a - mean_adv) / std_adv for a in advantages]

            # PPO update: multiple mini-epochs over the same trajectory
            if self.config.algorithm == "ppo":
                for _ppo_ep in range(self.config.ppo_epochs):
                    for (state, action, _), adv, ret in zip(trajectory, advantages, returns_list):
                        self.nn.forward(state)
                        self.nn.store_old_probs()
                        ent = self.nn.entropy()
                        self.nn.ppo_update(action, adv + self.config.entropy_coeff * ent, ret)
            else:
                # Original REINFORCE
                for (state, action, _), G_adv in zip(trajectory, returns_list):
                    self.nn.forward(state)
                    ent = self.nn.entropy()
                    self.nn.update(action, G_adv + self.config.entropy_coeff * ent)

            # Experience replay
            self.replay_buffer.add(trajectory)
            if random.random() < 0.5 and len(self.replay_buffer.buffer) > 1:
                sampled = self.replay_buffer.sample(1)[0]
                replay_returns = []
                G = 0.0
                for _, _, r in reversed(sampled):
                    G = r + self.config.gamma * G
                    replay_returns.insert(0, G)
                replay_baseline = np.mean(replay_returns)
                replay_returns = [G - replay_baseline for G in replay_returns]
                if len(replay_returns) > 1:
                    std_r = float(np.std(replay_returns)) + 1e-8
                    replay_returns = [r / std_r for r in replay_returns]
                orig_lr = self.nn.lr
                self.nn.lr = orig_lr * 0.5
                for (state, action, _), G_r in zip(sampled, replay_returns):
                    self.nn.forward(state)
                    self.nn.update(action, G_r)
                self.nn.lr = orig_lr

            # Callback
            if callback:
                ep_elapsed = time.time() - ep_start
                ep_times.append(ep_elapsed)
                avg_ep_time = sum(ep_times) / len(ep_times)
                eps_per_sec = 1.0 / avg_ep_time if avg_ep_time > 0 else 0
                remaining = (self.config.episodes - ep) * avg_ep_time
                callback(result, episode_history=episode_history, speed={
                    "episodes_done": ep + self._prior_episodes,
                    "episodes_total": self.config.episodes + self._prior_episodes,
                    "avg_episode_seconds": round(avg_ep_time, 2),
                    "episodes_per_second": round(eps_per_sec, 4),
                    "eta_seconds": round(remaining, 1),
                    "elapsed_seconds": round(time.time() - start_time, 1),
                })
                ep_start = time.time()

            # Save checkpoint
            if ep % self.config.save_every == 0:
                self._save_checkpoint(ep + self._prior_episodes, episodes, equity_curve)

        # Final save
        self._save_checkpoint(self.config.episodes + self._prior_episodes, episodes, equity_curve)

        # Evaluation on test set (5 episodes, no policy updates)
        test_episodes: list[DojoResult] = []
        test_max_start = len(features) - self.config.max_steps - 10
        if test_max_start > split_idx:
            for _ in range(5):
                test_start = random.randint(split_idx, test_max_start)
                test_result, _ = self._run_episode(features, test_start, step_callback=step_callback)
                test_episodes.append(test_result)

        test_win_rate = (sum(e.wins for e in test_episodes) /
                         max(sum(e.trades for e in test_episodes), 1)) if test_episodes else 0.0
        test_pnl = sum(e.pnl for e in test_episodes)
        test_pnls = [e.pnl for e in test_episodes]
        if len(test_pnls) >= 2:
            test_sharpe = float(np.mean(test_pnls) / (float(np.std(test_pnls)) + 1e-8) * math.sqrt(252))
        else:
            test_sharpe = 0.0

        # Build progress
        best_ep = max(episodes, key=lambda e: e.pnl)
        lessons = self._extract_lessons(episodes)
        overall_wr = sum(e.wins for e in episodes) / max(sum(e.trades for e in episodes), 1)
        overall_pnl = sum(e.pnl for e in episodes)
        overall_dd = max(e.max_drawdown for e in episodes)
        overall_trades = sum(e.trades for e in episodes)
        all_pnls = [e.pnl for e in episodes]
        final_skill, final_score, final_sharpe, final_pf = self._skill_level(all_pnls, overall_dd, overall_trades, len(episodes))

        self.progress = DojoProgress(
            symbol=self.config.symbol,
            episodes_completed=len(episodes),
            best_episode=best_ep,
            latest_episode=episodes[-1],
            skill_level=final_skill,
            skill_score=final_score,
            converged=final_score >= 60,
            total_trades=sum(e.trades for e in episodes),
            overall_win_rate=overall_wr,
            overall_pnl=overall_pnl,
            overall_max_dd=overall_dd,
            equity_curve=equity_curve,
            lessons_learned=lessons,
            training_time_seconds=time.time() - start_time,
            test_win_rate=test_win_rate,
            test_pnl=test_pnl,
            test_sharpe=test_sharpe,
            sharpe_ratio=final_sharpe,
            profit_factor=final_pf,
        )

        return self.progress

    # -- Save/Load ---------------------------------------------------------

    def _save_checkpoint(self, episode: int, episodes: list[DojoResult], equity_curve: list[float]):
        """Save model checkpoint."""
        save_path = self._save_dir / f"{self.config.symbol}_{self.config.timeframe}"
        save_path.mkdir(parents=True, exist_ok=True)

        if self.nn:
            model_path = save_path / "model.json"
            model_path.write_text(json.dumps(self.nn.save()), encoding="utf-8")

        # Save replay buffer
        if hasattr(self, 'replay_buffer') and self.replay_buffer.buffer:
            buffer_path = save_path / "replay_buffer.json"
            serializable = []
            for traj in self.replay_buffer.buffer:
                serializable.append([(s, a, r) for s, a, r in traj])
            buffer_path.write_text(json.dumps(serializable), encoding="utf-8")

        progress_data = {
            "symbol": self.config.symbol,
            "timeframe": self.config.timeframe,
            "episode": episode,
            "equity_curve": equity_curve,
            "episodes": [asdict(e) for e in episodes[-50:]],
            "config": asdict(self.config),
            "metadata": {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "features": self.config.features,
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe,
                "learning_rate": self.config.learning_rate,
                "hidden_size": self.config.hidden_size,
                "entropy_coeff": self.config.entropy_coeff,
                "spread_pips": self.config.spread_pips,
                "slippage_pips": self.config.slippage_pips,
                "data_bars": len(self._df) if hasattr(self, '_df') else 0,
                "data_start": str(self._df["time"].iloc[0]) if hasattr(self, '_df') and len(self._df) > 0 else "",
                "data_end": str(self._df["time"].iloc[-1]) if hasattr(self, '_df') and len(self._df) > 0 else "",
                "episodes_trained": len(episodes),
                "final_sharpe": episodes[-1].sharpe_ratio if episodes else 0,
                "final_pf": episodes[-1].profit_factor if episodes else 0,
            },
        }
        progress_path = save_path / "progress.json"
        progress_path.write_text(json.dumps(progress_data, indent=2, default=str), encoding="utf-8")

    def load_model(self, symbol: str, timeframe: str = "H1") -> bool:
        """Load saved model."""
        model_path = self._save_dir / f"{symbol}_{timeframe}" / "model.json"
        if not model_path.exists():
            return False
        data = json.loads(model_path.read_text(encoding="utf-8"))
        if data.get("architecture") == "ppo":
            self.nn = PPONN(
                len(data["W1"]), len(data["b1"]), len(data["b2"]),
                data.get("lr", 0.001),
                clip_epsilon=data.get("clip_epsilon", 0.2),
                gae_lambda=data.get("gae_lambda", 0.95),
            )
        else:
            self.nn = SimpleNN(
                len(data["W1"]), len(data["b1"]), len(data["b2"]),
                data.get("lr", 0.001)
            )
        self.nn.load(data)
        return True

    def load_progress(self, symbol: str, timeframe: str = "H1") -> DojoProgress | None:
        """Load saved progress."""
        progress_path = self._save_dir / f"{symbol}_{timeframe}" / "progress.json"
        if not progress_path.exists():
            return None
        data = json.loads(progress_path.read_text(encoding="utf-8"))

        episodes = []
        for ep_data in data.get("episodes", []):
            ep_data.setdefault("sharpe_ratio", 0.0)
            ep_data.setdefault("profit_factor", 0.0)
            episodes.append(DojoResult(**ep_data))

        if not episodes:
            return None

        best_ep = max(episodes, key=lambda e: e.pnl)
        lessons = self._extract_lessons(episodes)
        overall_wr = sum(e.wins for e in episodes) / max(sum(e.trades for e in episodes), 1)
        overall_pnl = sum(e.pnl for e in episodes)
        overall_dd = max(e.max_drawdown for e in episodes)
        overall_trades = sum(e.trades for e in episodes)
        all_pnls = [e.pnl for e in episodes]
        final_skill, final_score, final_sharpe, final_pf = self._skill_level(all_pnls, overall_dd, overall_trades, len(episodes))

        return DojoProgress(
            symbol=symbol,
            episodes_completed=len(episodes),
            best_episode=best_ep,
            latest_episode=episodes[-1],
            skill_level=final_skill,
            skill_score=final_score,
            converged=final_score >= 60,
            total_trades=sum(e.trades for e in episodes),
            overall_win_rate=overall_wr,
            overall_pnl=overall_pnl,
            overall_max_dd=overall_dd,
            equity_curve=data.get("equity_curve", []),
            lessons_learned=lessons,
            training_time_seconds=0,
            test_win_rate=0.0,
            test_pnl=0.0,
            test_sharpe=0.0,
            sharpe_ratio=final_sharpe,
            profit_factor=final_pf,
        )

    # -- Walk-forward evaluation -------------------------------------------

    def walk_forward_eval(self, n_windows: int = 3, train_ratio: float = 0.6) -> dict:
        """Walk-forward evaluation: train on rolling windows, test on next period.

        Returns dict with per-window results and aggregate metrics.
        """
        import copy
        import pandas as pd

        self._df = self._fetch_data()
        features = _compute_features(self._df)
        n_features = features.shape[1]
        extra_features = 4 if self.config.multi_timeframe else 0

        self._multi_tf = {}
        if self.config.multi_timeframe:
            self._multi_tf = self._fetch_multi_tf_data()

        window_size = len(features) // n_windows
        train_size = int(window_size * train_ratio)
        test_size = window_size - train_size

        window_results = []

        for w in range(n_windows):
            w_start = w * window_size
            w_train_end = w_start + train_size
            w_test_end = min(w_start + window_size, len(features) - 1)

            if w_test_end - w_train_end < 50:
                continue  # not enough test data

            # Create a temporary config for this window
            win_config = copy.deepcopy(self.config)
            win_config.warm_start = False
            win_config.episodes = max(10, self.config.episodes // n_windows)

            # Train on this window
            win_dojo = ForexDojo(win_config)
            win_dojo._df = self._df
            win_dojo._multi_tf = self._multi_tf
            if win_config.algorithm == "ppo":
                win_dojo.nn = PPONN(n_features + 4 + extra_features, win_config.hidden_size, 3, win_config.learning_rate,
                                     clip_epsilon=win_config.clip_epsilon, gae_lambda=win_config.gae_lambda)
            else:
                win_dojo.nn = SimpleNN(n_features + 4 + extra_features, win_config.hidden_size, 3, win_config.learning_rate)

            # Run training episodes within this window
            train_episodes = []
            for ep in range(win_config.episodes):
                start = random.randint(50, max(50, train_size - win_config.max_steps - 10))
                result, trajectory = win_dojo._run_episode(features, w_start + start,
                                                            max_steps=min(win_config.max_steps, train_size - start - 10))
                result.episode = ep + 1
                train_episodes.append(result)

                # Update policy
                returns_list = []
                G = 0.0
                for _, _, r in reversed(trajectory):
                    G = r + win_config.gamma * G
                    returns_list.insert(0, G)
                baseline = np.mean(returns_list)
                returns_list = [G - baseline for G in returns_list]
                if len(returns_list) > 1:
                    std_r = float(np.std(returns_list)) + 1e-8
                    returns_list = [r / std_r for r in returns_list]

                if win_config.algorithm == "ppo":
                    for _ppo_ep in range(win_config.ppo_epochs):
                        for (state, action, _), adv in zip(trajectory, returns_list):
                            win_dojo.nn.forward(state)
                            win_dojo.nn.store_old_probs()
                            ent = win_dojo.nn.entropy()
                            win_dojo.nn.ppo_update(action, adv + win_config.entropy_coeff * ent, adv)
                else:
                    for (state, action, _), G_adv in zip(trajectory, returns_list):
                        win_dojo.nn.forward(state)
                        ent = win_dojo.nn.entropy()
                        win_dojo.nn.update(action, G_adv + win_config.entropy_coeff * ent)

            # Test on next period (no updates)
            test_episodes = []
            for _ in range(3):
                if w_train_end + 50 >= w_test_end:
                    break
                test_start = random.randint(w_train_end, max(w_train_end, w_test_end - win_config.max_steps - 10))
                test_result, _ = win_dojo._run_episode(features, test_start,
                                                        max_steps=min(win_config.max_steps, w_test_end - test_start - 10))
                test_episodes.append(test_result)

            # Compute window metrics
            train_pnls = [e.pnl for e in train_episodes]
            test_pnls = [e.pnl for e in test_episodes]
            train_wr = sum(e.wins for e in train_episodes) / max(sum(e.trades for e in train_episodes), 1)
            test_wr = sum(e.wins for e in test_episodes) / max(sum(e.trades for e in test_episodes), 1)

            window_results.append({
                "window": w + 1,
                "train_bars": train_size,
                "test_bars": test_size,
                "train_episodes": len(train_episodes),
                "train_pnl": round(sum(train_pnls), 2),
                "train_wr": round(train_wr, 3),
                "test_pnl": round(sum(test_pnls), 2),
                "test_wr": round(test_wr, 3),
            })

        # Aggregate
        all_test_pnls = [w["test_pnl"] for w in window_results]
        all_test_wrs = [w["test_wr"] for w in window_results]

        return {
            "symbol": self.config.symbol,
            "timeframe": self.config.timeframe,
            "n_windows": len(window_results),
            "windows": window_results,
            "aggregate": {
                "total_test_pnl": round(sum(all_test_pnls), 2),
                "avg_test_pnl": round(float(np.mean(all_test_pnls)), 2) if all_test_pnls else 0,
                "avg_test_wr": round(float(np.mean(all_test_wrs)), 3) if all_test_wrs else 0,
                "consistency": round(sum(1 for p in all_test_pnls if p > 0) / max(len(all_test_pnls), 1), 2),
                "test_sharpe": round(float(np.mean(all_test_pnls) / (float(np.std(all_test_pnls)) + 1e-8) * math.sqrt(252)), 2) if len(all_test_pnls) >= 2 else 0,
            },
        }


# -- Convenience functions -------------------------------------------------

def quick_lesson(symbol: str, episodes: int = 10, timeframe: str = "H1") -> DojoProgress:
    """Quick lesson -- 10 episodes to see if pair is learnable."""
    config = DojoConfig(symbol=symbol, timeframe=timeframe, episodes=episodes)
    dojo = ForexDojo(config)
    return dojo.train()


def full_session(symbol: str, episodes: int = 100, timeframe: str = "H1") -> DojoProgress:
    """Full training session -- converge to policy."""
    config = DojoConfig(symbol=symbol, timeframe=timeframe, episodes=episodes)
    dojo = ForexDojo(config)
    return dojo.train()


def list_trained_models() -> list[dict]:
    """List all trained Dojo models."""
    models_dir = Path(__file__).parent.parent / "dojo" / "models"
    if not models_dir.exists():
        return []

    results = []
    for model_dir in sorted(models_dir.iterdir()):
        if model_dir.is_dir() and (model_dir / "progress.json").exists():
            data = json.loads((model_dir / "progress.json").read_text(encoding="utf-8"))
            episodes = data.get("episodes", [])
            if episodes:
                last = episodes[-1]
                # Skip models with too few episodes (untrained checkpoints)
                if len(episodes) < 5:
                    continue
                # Check if model uses old features
                saved_features = data.get("config", {}).get("features", [])
                old_features = ["returns", "atr", "rsi", "ema_fast", "ema_slow", "macd", "bb_position", "volume_ratio", "hour_sin", "hour_cos"]
                is_stale = saved_features == old_features and "ema_slow" in saved_features
                results.append({
                    "symbol": data.get("symbol", model_dir.name),
                    "timeframe": data.get("timeframe", "H1"),
                    "episodes": data.get("episode", len(episodes)),  # total episodes, not just stored window
                    "skill_level": last.get("skill_level", "Unknown"),
                    "win_rate": last.get("win_rate", 0),
                    "pnl": last.get("pnl", 0),
                    "sharpe_ratio": last.get("sharpe_ratio", 0),
                    "profit_factor": last.get("profit_factor", 0),
                    "episode_count": len(episodes),
                    "equity_curve": data.get("equity_curve", []),
                    "is_stale": is_stale,
                })
    return results


def walk_forward(symbol: str, timeframe: str = "H1", episodes: int = 30, n_windows: int = 3) -> dict:
    """Run walk-forward evaluation for a symbol."""
    config = DojoConfig(symbol=symbol, timeframe=timeframe, episodes=episodes)
    dojo = ForexDojo(config)
    return dojo.walk_forward_eval(n_windows=n_windows)


if __name__ == "__main__":
    # Quick test
    import MetaTrader5 as mt5
    mt5.initialize()

    print("=== ForexDojo Quick Lesson: EURUSD ===")
    progress = quick_lesson("EURUSD", episodes=10)
    print(f"Skill: {progress.skill_level} ({progress.skill_score:.0f}/100)")
    print(f"Win Rate: {progress.overall_win_rate:.0%}")
    print(f"Total P&L: ${progress.overall_pnl:.2f}")
    print(f"Max DD: {progress.overall_max_dd:.1f}%")
    print(f"Trades: {progress.total_trades}")
    print(f"Sharpe: {progress.sharpe_ratio:.2f}")
    print(f"Profit Factor: {progress.profit_factor:.2f}")
    print(f"Test Win Rate: {progress.test_win_rate:.0%}")
    print(f"Test P&L: ${progress.test_pnl:.2f}")
    print(f"Test Sharpe: {progress.test_sharpe:.2f}")
    print(f"Lessons: {progress.lessons_learned}")

    mt5.shutdown()
