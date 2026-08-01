import numpy as np
from typing import List, Tuple
from dataclasses import dataclass


@dataclass
class OHLCV:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int


def to_ohlcv(rates: List[dict]) -> List[OHLCV]:
    return [OHLCV(r["time"], r["open"], r["high"], r["low"], r["close"], r["tick_volume"]) for r in rates]


def sma(values: np.ndarray, period: int) -> np.ndarray:
    return np.convolve(values, np.ones(period)/period, mode='valid')


def ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    result = np.zeros_like(values)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i-1]
    return result


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gain = np.convolve(gains, np.ones(period)/period, mode='valid')
    avg_loss = np.convolve(losses, np.ones(period)/period, mode='valid')
    
    rs = avg_gain / (avg_loss + 1e-10)
    rsi_vals = 100 - (100 / (1 + rs))
    
    return np.pad(rsi_vals, (period, 0), mode='edge')


def macd(values: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(values: np.ndarray, period: int = 20, std_dev: float = 2) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    middle = sma(values, period)
    std = np.array([np.std(values[i-period+1:i+1]) for i in range(period-1, len(values))])
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.zeros_like(close)
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
    return ema(tr, period)


def stochastic(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int = 14, d_period: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    lowest_low = np.array([np.min(low[max(0,i-k_period+1):i+1]) for i in range(len(low))])
    highest_high = np.array([np.max(high[max(0,i-k_period+1):i+1]) for i in range(len(high))])
    
    k = 100 * (close - lowest_low) / (highest_high - lowest_low + 1e-10)
    d = sma(k, d_period)
    d = np.pad(d, (k_period-1, 0), mode='edge')
    return k, d


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    plus_dm = np.zeros_like(high)
    minus_dm = np.zeros_like(low)
    
    for i in range(1, len(high)):
        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0
    
    tr = np.zeros_like(close)
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    
    atr_val = ema(tr, period)
    plus_di = 100 * ema(plus_dm, period) / (atr_val + 1e-10)
    minus_di = 100 * ema(minus_dm, period) / (atr_val + 1e-10)
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    return ema(dx, period)


def pivot_points(high: float, low: float, close: float) -> dict:
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return {"pp": pp, "r1": r1, "s1": s1, "r2": r2, "s2": s2, "r3": r3, "s3": s3}


def analyze_trend(close: np.ndarray, fast: int = 50, slow: int = 200) -> str:
    if len(close) < slow:
        return "insufficient_data"
    fast_ma = sma(close, fast)[-1]
    slow_ma = sma(close, slow)[-1]
    current = close[-1]
    
    if current > fast_ma > slow_ma:
        return "strong_uptrend"
    elif current > fast_ma and fast_ma < slow_ma:
        return "weak_uptrend"
    elif current < fast_ma < slow_ma:
        return "strong_downtrend"
    elif current < fast_ma and fast_ma > slow_ma:
        return "weak_downtrend"
    return "ranging"


def support_resistance(close: np.ndarray, high: np.ndarray, low: np.ndarray, window: int = 20) -> Tuple[List[float], List[float]]:
    supports = []
    resistances = []
    
    for i in range(window, len(close) - window):
        if low[i] == np.min(low[i-window:i+window+1]):
            supports.append(low[i])
        if high[i] == np.max(high[i-window:i+window+1]):
            resistances.append(high[i])
    
    return sorted(set(round(s, 5) for s in supports))[-5:], sorted(set(round(r, 5) for r in resistances))[-5:]