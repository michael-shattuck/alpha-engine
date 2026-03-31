import logging
import psycopg2
import psycopg2.extras
import numpy as np

from server.config import DATABASE_URL
from server.signals import indicators as ind

log = logging.getLogger("ml_features")


def load_candles(asset: str, limit: int = 100000) -> list[dict]:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_1m WHERE asset = %s ORDER BY timestamp ASC LIMIT %s",
        (asset, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def compute_features(candles: list[dict], lookback: int = 20) -> list[dict]:
    if len(candles) < lookback + 30:
        return []

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    rsi_series = ind.rsi_series(closes, 14)
    ema_9 = ind.ema_series(closes, 9)
    ema_21 = ind.ema_series(closes, 21)

    features = []
    for i in range(lookback + 30, len(candles)):
        window_closes = closes[i - lookback:i]
        window_highs = highs[i - lookback:i]
        window_lows = lows[i - lookback:i]

        rsi_val = rsi_series[i]
        rsi_prev = rsi_series[i - 1]
        bb_lower, bb_middle, bb_upper = ind.bollinger_bands(window_closes, 20)
        adx_val, plus_di, minus_di = ind.adx_with_di(window_highs, window_lows, window_closes)
        atr_val = ind.atr(window_highs, window_lows, window_closes, 14)
        velocity = ind.price_velocity(window_closes, 5)
        acceleration = ind.price_acceleration(window_closes, 5)

        price = closes[i]
        bb_position = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper > bb_lower else 0.5
        ema_cross = (ema_9[i] - ema_21[i]) / price * 100 if price > 0 else 0

        vol_avg = np.mean(volumes[i - lookback:i]) if volumes[i - lookback] > 0 else 1
        vol_ratio = volumes[i] / vol_avg if vol_avg > 0 else 1

        candle_body = (closes[i] - candles[i]["open"]) / price * 100 if price > 0 else 0
        candle_range = (highs[i] - lows[i]) / price * 100 if price > 0 else 0
        upper_wick = (highs[i] - max(closes[i], candles[i]["open"])) / price * 100 if price > 0 else 0
        lower_wick = (min(closes[i], candles[i]["open"]) - lows[i]) / price * 100 if price > 0 else 0

        returns_1 = (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if closes[i - 1] > 0 else 0
        returns_5 = (closes[i] - closes[i - 5]) / closes[i - 5] * 100 if closes[i - 5] > 0 else 0
        returns_15 = (closes[i] - closes[i - 15]) / closes[i - 15] * 100 if closes[i - 15] > 0 else 0

        future_5 = (closes[min(i + 5, len(closes) - 1)] - closes[i]) / closes[i] * 100 if i + 5 < len(closes) else None
        future_15 = (closes[min(i + 15, len(closes) - 1)] - closes[i]) / closes[i] * 100 if i + 15 < len(closes) else None

        label_5 = None
        if future_5 is not None:
            if future_5 > 0.3:
                label_5 = 1
            elif future_5 < -0.3:
                label_5 = -1
            else:
                label_5 = 0

        features.append({
            "timestamp": candles[i]["timestamp"],
            "price": price,
            "rsi": rsi_val,
            "rsi_prev": rsi_prev,
            "rsi_delta": rsi_val - rsi_prev,
            "bb_position": bb_position,
            "bb_width": (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0,
            "adx": adx_val,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "di_diff": plus_di - minus_di,
            "atr_pct": atr_val / price * 100 if price > 0 else 0,
            "velocity": velocity,
            "acceleration": acceleration,
            "ema_cross": ema_cross,
            "vol_ratio": vol_ratio,
            "candle_body": candle_body,
            "candle_range": candle_range,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "returns_1": returns_1,
            "returns_5": returns_5,
            "returns_15": returns_15,
            "future_5": future_5,
            "future_15": future_15,
            "label_5": label_5,
        })

    return features


FEATURE_COLUMNS = [
    "rsi", "rsi_delta", "bb_position", "bb_width", "adx", "di_diff",
    "atr_pct", "velocity", "acceleration", "ema_cross", "vol_ratio",
    "candle_body", "candle_range", "upper_wick", "lower_wick",
    "returns_1", "returns_5", "returns_15",
]


def features_to_arrays(features: list[dict]):
    X = []
    y = []
    for f in features:
        if f["label_5"] is None:
            continue
        row = [f[col] for col in FEATURE_COLUMNS]
        if any(np.isnan(v) or np.isinf(v) for v in row):
            continue
        X.append(row)
        y.append(f["label_5"])
    return np.array(X), np.array(y)
