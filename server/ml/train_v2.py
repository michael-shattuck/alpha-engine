import logging
import json
import pickle
import os
import time
import numpy as np
from pathlib import Path

from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

from server.ml.features import load_candles
from server.signals import indicators as ind

log = logging.getLogger("train_v2")

MODEL_DIR = Path("state/models_v2")
LOOKAHEAD_MINUTES = 30
PROFIT_THRESHOLD = 0.005
LOSS_THRESHOLD = -0.005


def aggregate(candles_1m, minutes):
    result = []
    for i in range(0, len(candles_1m) - minutes + 1, minutes):
        chunk = candles_1m[i:i + minutes]
        result.append({
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
        })
    return result


def tf_features(closes, price):
    if len(closes) < 15:
        return [0] * 10

    rsi = ind.rsi(closes)
    rsi_prev = ind.rsi(closes[:-1]) if len(closes) > 15 else rsi
    ema_9 = ind.ema(closes, 9)
    ema_21 = ind.ema(closes, 21)
    velocity = ind.price_velocity(closes, 3)
    bb_l, bb_m, bb_u = ind.bollinger_bands(closes)
    bb_pos = (price - bb_l) / (bb_u - bb_l) if bb_u > bb_l else 0.5
    bb_width = (bb_u - bb_l) / bb_m if bb_m > 0 else 0
    ema_spread = (ema_9 - ema_21) / ema_21 if ema_21 > 0 else 0

    recent_move = (closes[-1] - closes[-3]) / closes[-3] if len(closes) >= 3 else 0

    return [
        rsi / 100,
        (rsi - rsi_prev) / 100,
        ema_spread,
        1 if ema_9 > ema_21 else -1,
        velocity,
        bb_pos,
        bb_width,
        recent_move * 100,
        ind.price_acceleration(closes, 3) if len(closes) >= 6 else 0,
        (price - ema_21) / ema_21 if ema_21 > 0 else 0,
    ]


def build_features_and_labels(asset, candles_1m=None):
    if candles_1m is None:
        candles_1m = load_candles(asset, limit=200000)

    c1 = candles_1m
    c5 = aggregate(c1, 5)
    c15 = aggregate(c1, 15)
    c60 = aggregate(c1, 60)
    c240 = aggregate(c1, 240)

    features = []
    labels_dir = []
    labels_mag = []

    for i in range(240, len(c1) - LOOKAHEAD_MINUTES):
        price = c1[i]["close"]
        i5, i15, i60, i240 = i // 5, i // 15, i // 60, i // 240

        if i5 < 15 or i15 < 15 or i60 < 15 or i240 < 5:
            continue

        f1 = tf_features([c["close"] for c in c1[max(0, i - 30):i + 1]], price)
        f5 = tf_features([c["close"] for c in c5[max(0, i5 - 30):i5 + 1]], price)
        f15 = tf_features([c["close"] for c in c15[max(0, i15 - 30):i15 + 1]], price)
        f60 = tf_features([c["close"] for c in c60[max(0, i60 - 30):i60 + 1]], price)
        f240 = tf_features([c["close"] for c in c240[max(0, i240 - 15):i240 + 1]], price)

        future_closes = [c1[i + j]["close"] for j in range(1, LOOKAHEAD_MINUTES + 1)]
        max_up = (max(future_closes) - price) / price
        max_down = (min(future_closes) - price) / price
        end_move = (future_closes[-1] - price) / price

        if max_up > PROFIT_THRESHOLD and max_up > abs(max_down):
            label = 1
        elif max_down < LOSS_THRESHOLD and abs(max_down) > max_up:
            label = -1
        else:
            label = 0

        row = f1 + f5 + f15 + f60 + f240
        features.append(row)
        labels_dir.append(label)
        labels_mag.append(end_move * 100)

    return np.array(features), np.array(labels_dir), np.array(labels_mag)


def train_asset(asset, candles_1m=None):
    log.info(f"Building features for {asset}...")
    X, y_dir, y_mag = build_features_and_labels(asset, candles_1m)
    log.info(f"  {len(X)} samples, {sum(y_dir == 1)} long, {sum(y_dir == -1)} short, {sum(y_dir == 0)} flat")

    if len(X) < 100:
        return {"error": "insufficient data"}

    X_train, X_test, yd_train, yd_test, ym_train, ym_test = train_test_split(
        X, y_dir, y_mag, test_size=0.2, shuffle=False
    )

    log.info(f"  Training direction classifier...")
    dir_model = GradientBoostingClassifier(
        n_estimators=300, max_depth=5, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=20, random_state=42,
    )
    dir_model.fit(X_train, yd_train)

    y_pred = dir_model.predict(X_test)
    acc = accuracy_score(yd_test, y_pred)
    log.info(f"  Direction accuracy: {acc:.1%}")

    log.info(f"  Training magnitude regressor...")
    mag_model = GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, min_samples_leaf=20, random_state=42,
    )
    mag_model.fit(X_train, ym_train)

    long_signals = y_pred == 1
    short_signals = y_pred == -1
    long_actual = yd_test[long_signals]
    short_actual = yd_test[short_signals]

    long_correct = sum(long_actual == 1) if len(long_actual) > 0 else 0
    short_correct = sum(short_actual == -1) if len(short_actual) > 0 else 0

    results = {
        "asset": asset,
        "samples": len(X),
        "accuracy": round(acc * 100, 1),
        "long_precision": round(long_correct / max(sum(long_signals), 1) * 100, 1),
        "short_precision": round(short_correct / max(sum(short_signals), 1) * 100, 1),
        "long_signals": int(sum(long_signals)),
        "short_signals": int(sum(short_signals)),
        "flat_signals": int(sum(y_pred == 0)),
        "class_dist": {
            "long": int(sum(y_dir == 1)),
            "short": int(sum(y_dir == -1)),
            "flat": int(sum(y_dir == 0)),
        },
    }

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_DIR / f"{asset}_dir.pkl", "wb") as f:
        pickle.dump(dir_model, f)
    with open(MODEL_DIR / f"{asset}_mag.pkl", "wb") as f:
        pickle.dump(mag_model, f)
    with open(MODEL_DIR / f"{asset}_meta.json", "w") as f:
        json.dump(results, f, indent=2)

    log.info(f"  Saved models to {MODEL_DIR}")
    return results


class MLSignalPredictor:
    def __init__(self):
        self._dir_models: dict = {}
        self._mag_models: dict = {}
        self._loaded = False

    def load(self):
        if not MODEL_DIR.exists():
            return
        for f in MODEL_DIR.glob("*_dir.pkl"):
            asset = f.stem.replace("_dir", "")
            with open(f, "rb") as fh:
                self._dir_models[asset] = pickle.load(fh)
            mag_path = MODEL_DIR / f"{asset}_mag.pkl"
            if mag_path.exists():
                with open(mag_path, "rb") as fh:
                    self._mag_models[asset] = pickle.load(fh)
        self._loaded = bool(self._dir_models)
        log.info(f"Loaded ML models for: {list(self._dir_models.keys())}")

    def predict(self, asset, features_1m, features_5m, features_15m, features_1h, features_4h):
        if asset not in self._dir_models:
            return 0, 0.0, 0.0

        row = np.array(features_1m + features_5m + features_15m + features_1h + features_4h).reshape(1, -1)

        dir_model = self._dir_models[asset]
        probs = dir_model.predict_proba(row)[0]
        classes = list(dir_model.classes_)

        long_prob = probs[classes.index(1)] if 1 in classes else 0
        short_prob = probs[classes.index(-1)] if -1 in classes else 0

        if long_prob > 0.5:
            direction = 1
            confidence = long_prob
        elif short_prob > 0.5:
            direction = -1
            confidence = short_prob
        else:
            direction = 0
            confidence = max(long_prob, short_prob)

        magnitude = 0.0
        if asset in self._mag_models:
            magnitude = float(self._mag_models[asset].predict(row)[0])

        return direction, confidence, magnitude


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    for asset in ["JUP", "JTO", "PYTH", "SUI"]:
        print(f"\n{'=' * 50}")
        print(f"  Training {asset}")
        print(f"{'=' * 50}")
        result = train_asset(asset)
        print(json.dumps(result, indent=2))
