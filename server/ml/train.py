import logging
import pickle
import json
import time
from pathlib import Path

import numpy as np

from server.ml.features import load_candles, compute_features, features_to_arrays, FEATURE_COLUMNS
from server.ml.backfill import TOKENS, get_stats
from server.config import STATE_DIR

log = logging.getLogger("ml_train")

MODEL_DIR = STATE_DIR / "models"


def train_model(assets: list[str] | None = None, min_candles: int = 10000):
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, accuracy_score
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        log.error("sklearn not installed. Run: pip install scikit-learn")
        return None

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if assets is None:
        assets = list(TOKENS.keys())

    all_X = []
    all_y = []

    for asset in assets:
        candles = load_candles(asset, limit=200000)
        if len(candles) < min_candles:
            log.warning(f"{asset}: only {len(candles)} candles, need {min_candles}. Skipping.")
            continue

        features = compute_features(candles)
        X, y = features_to_arrays(features)
        log.info(f"{asset}: {len(X)} samples, distribution: long={sum(y==1)} flat={sum(y==0)} short={sum(y==-1)}")
        all_X.append(X)
        all_y.append(y)

    if not all_X:
        log.error("No training data available")
        return None

    X = np.vstack(all_X)
    y = np.concatenate(all_y)
    log.info(f"Total: {len(X)} samples across {len(all_X)} assets")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2, shuffle=False)

    model = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.1,
        subsample=0.8,
        min_samples_leaf=50,
    )

    log.info(f"Training on {len(X_train)} samples, testing on {len(X_test)}...")
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, target_names=["short", "flat", "long"], output_dict=True)

    log.info(f"Accuracy: {accuracy:.3f}")
    log.info(f"Long precision: {report['long']['precision']:.3f}, recall: {report['long']['recall']:.3f}")
    log.info(f"Short precision: {report['short']['precision']:.3f}, recall: {report['short']['recall']:.3f}")

    importances = sorted(
        zip(FEATURE_COLUMNS, model.feature_importances_),
        key=lambda x: x[1], reverse=True,
    )
    log.info("Feature importances:")
    for name, imp in importances[:10]:
        log.info(f"  {name}: {imp:.4f}")

    # pickle is used here for sklearn model serialization only -- these are our own trained models
    model_path = MODEL_DIR / "signal_model.pkl"
    scaler_path = MODEL_DIR / "signal_scaler.pkl"
    meta_path = MODEL_DIR / "signal_meta.json"

    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    with open(scaler_path, "wb") as f:
        pickle.dump(scaler, f)
    with open(meta_path, "w") as f:
        json.dump({
            "trained_at": time.time(),
            "assets": assets,
            "samples": len(X),
            "accuracy": accuracy,
            "report": report,
            "feature_columns": FEATURE_COLUMNS,
            "importances": {name: float(imp) for name, imp in importances},
        }, f, indent=2)

    log.info(f"Model saved to {model_path}")
    return {
        "accuracy": accuracy,
        "report": report,
        "samples": len(X),
        "importances": dict(importances),
    }


class SignalPredictor:
    def __init__(self):
        self.model = None
        self.scaler = None
        self.meta = None
        self._loaded = False

    def load(self):
        model_path = MODEL_DIR / "signal_model.pkl"
        scaler_path = MODEL_DIR / "signal_scaler.pkl"
        meta_path = MODEL_DIR / "signal_meta.json"

        if not model_path.exists():
            return False

        with open(model_path, "rb") as f:
            self.model = pickle.load(f)
        with open(scaler_path, "rb") as f:
            self.scaler = pickle.load(f)
        if meta_path.exists():
            with open(meta_path) as f:
                self.meta = json.load(f)

        self._loaded = True
        log.info(f"ML model loaded: accuracy={self.meta.get('accuracy', 0):.3f}, samples={self.meta.get('samples', 0)}")
        return True

    def predict(self, features: dict) -> tuple[int, float]:
        if not self._loaded:
            return 0, 0.0

        row = [features.get(col, 0) for col in FEATURE_COLUMNS]
        if any(np.isnan(v) or np.isinf(v) for v in row):
            return 0, 0.0

        X = self.scaler.transform([row])
        prediction = self.model.predict(X)[0]
        probas = self.model.predict_proba(X)[0]
        confidence = max(probas)

        return int(prediction), float(confidence)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    stats = get_stats()
    for s in stats:
        print(f"  {s['asset']:5s}: {s['candles']:>8,} candles ({s['days']}d)")

    result = train_model()
    if result:
        print(f"\nModel trained: accuracy={result['accuracy']:.3f}, {result['samples']} samples")
