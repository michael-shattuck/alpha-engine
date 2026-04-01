import logging
import numpy as np
from enum import Enum
from dataclasses import dataclass
from typing import Optional

from hmmlearn.hmm import GaussianHMM
from sklearn.mixture import GaussianMixture

from server.signals.candles import CandleAggregator, Timeframe
from server.signals import indicators as ind

log = logging.getLogger("micro_regime")

FEATURE_WINDOW = 30
MIN_CANDLES = 50
RETRAIN_INTERVAL = 100


class MicroRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    VOLATILE = "volatile"
    MEAN_REVERTING = "mean_reverting"
    DEAD = "dead"


REGIME_ACTIONS = {
    MicroRegime.TRENDING_UP: "long_only",
    MicroRegime.TRENDING_DOWN: "short_only",
    MicroRegime.VOLATILE: "no_trade",
    MicroRegime.MEAN_REVERTING: "mean_revert",
    MicroRegime.DEAD: "no_trade",
}


@dataclass
class MicroRegimeState:
    regime: MicroRegime
    confidence: float
    allowed_actions: str
    features: dict
    hmm_state: int
    gmm_cluster: int
    trend_bias: float
    volatility_rank: float


class MicroRegimeDetector:
    def __init__(self, n_hmm_states: int = 4, n_gmm_clusters: int = 4):
        self.n_hmm_states = n_hmm_states
        self.n_gmm_clusters = n_gmm_clusters
        self._hmm: Optional[GaussianHMM] = None
        self._gmm: Optional[GaussianMixture] = None
        self._state_labels: dict[int, MicroRegime] = {}
        self._feature_history: list[np.ndarray] = []
        self._ticks_since_train = 0
        self._trained = False
        self._last_state: Optional[MicroRegimeState] = None

    def _extract_features(self, candles: CandleAggregator) -> Optional[np.ndarray]:
        closes_15m = candles.get_closes(Timeframe.M15, FEATURE_WINDOW + 10)
        highs_15m = candles.get_highs(Timeframe.M15, FEATURE_WINDOW + 10)
        lows_15m = candles.get_lows(Timeframe.M15, FEATURE_WINDOW + 10)
        closes_5m = candles.get_closes(Timeframe.M5, FEATURE_WINDOW + 10)

        if len(closes_15m) < 22 or len(closes_5m) < 22:
            return None

        returns = [(closes_15m[i] - closes_15m[i - 1]) / closes_15m[i - 1]
                    for i in range(1, len(closes_15m))]
        if len(returns) < 5:
            return None

        realized_vol = np.std(returns[-10:]) if len(returns) >= 10 else np.std(returns)
        mean_return = np.mean(returns[-5:])

        rsi = ind.rsi(closes_15m)
        rsi_5m = ind.rsi(closes_5m)
        velocity = ind.price_velocity(closes_15m, 3)
        acceleration = ind.price_acceleration(closes_15m, 3)

        ema_9 = ind.ema(closes_15m, 9)
        ema_21 = ind.ema(closes_15m, 21)
        ema_spread = (ema_9 - ema_21) / ema_21 if ema_21 > 0 else 0

        bb_lower, bb_middle, bb_upper = ind.bollinger_bands(closes_15m)
        bb_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0
        bb_position = (closes_15m[-1] - bb_lower) / (bb_upper - bb_lower) if bb_upper > bb_lower else 0.5

        atr = ind.atr(highs_15m, lows_15m, closes_15m, 14) if len(highs_15m) >= 14 else 0
        atr_pct = atr / closes_15m[-1] if closes_15m[-1] > 0 else 0

        highs_1h = candles.get_highs(Timeframe.H1, 30)
        lows_1h = candles.get_lows(Timeframe.H1, 30)
        closes_1h = candles.get_closes(Timeframe.H1, 30)
        adx_val = 0
        plus_di = 0
        minus_di = 0
        if len(closes_1h) >= 14:
            adx_val, plus_di, minus_di = ind.adx_with_di(highs_1h, lows_1h, closes_1h)

        di_spread = (plus_di - minus_di) / max(plus_di + minus_di, 1)

        features = np.array([
            mean_return * 100,
            realized_vol * 100,
            rsi / 100,
            velocity,
            acceleration,
            ema_spread * 100,
            bb_width * 100,
            bb_position,
            atr_pct * 100,
            adx_val / 100,
            di_spread,
        ])

        return features

    def _build_feature_matrix(self) -> Optional[np.ndarray]:
        if len(self._feature_history) < MIN_CANDLES:
            return None
        return np.array(self._feature_history[-500:])

    def _train(self):
        X = self._build_feature_matrix()
        if X is None:
            return

        try:
            self._hmm = GaussianHMM(
                n_components=self.n_hmm_states,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
            self._hmm.fit(X)
        except Exception as e:
            log.warning(f"HMM training failed: {e}")
            self._hmm = None

        try:
            self._gmm = GaussianMixture(
                n_components=self.n_gmm_clusters,
                covariance_type="full",
                n_init=3,
                random_state=42,
            )
            self._gmm.fit(X)
        except Exception as e:
            log.warning(f"GMM training failed: {e}")
            self._gmm = None

        if self._hmm:
            states = self._hmm.predict(X)
            self._label_states(X, states)

        self._trained = True
        self._ticks_since_train = 0
        log.info(f"Micro-regime models trained on {len(X)} samples, labels={self._state_labels}")

    def _label_states(self, X: np.ndarray, states: np.ndarray):
        self._state_labels = {}
        for s in range(self.n_hmm_states):
            mask = states == s
            if not mask.any():
                self._state_labels[s] = MicroRegime.DEAD
                continue

            subset = X[mask]
            avg_return = np.mean(subset[:, 0])
            avg_vol = np.mean(subset[:, 1])
            avg_ema_spread = np.mean(subset[:, 5])
            avg_adx = np.mean(subset[:, 9])
            avg_di_spread = np.mean(subset[:, 10])

            if avg_vol < 0.05 and abs(avg_return) < 0.02:
                self._state_labels[s] = MicroRegime.DEAD
            elif avg_return > 0.03 and avg_ema_spread > 0 and avg_di_spread > 0.1:
                self._state_labels[s] = MicroRegime.TRENDING_UP
            elif avg_return < -0.03 and avg_ema_spread < 0 and avg_di_spread < -0.1:
                self._state_labels[s] = MicroRegime.TRENDING_DOWN
            elif avg_vol > 0.15:
                self._state_labels[s] = MicroRegime.VOLATILE
            else:
                self._state_labels[s] = MicroRegime.MEAN_REVERTING

    def assess(self, candles: CandleAggregator) -> MicroRegimeState:
        features = self._extract_features(candles)
        if features is None:
            return MicroRegimeState(
                regime=MicroRegime.DEAD, confidence=0, allowed_actions="no_trade",
                features={}, hmm_state=-1, gmm_cluster=-1, trend_bias=0, volatility_rank=0,
            )

        self._feature_history.append(features)
        self._ticks_since_train += 1

        if not self._trained or self._ticks_since_train >= RETRAIN_INTERVAL:
            self._train()

        hmm_state = -1
        hmm_regime = MicroRegime.DEAD
        hmm_confidence = 0.0

        if self._hmm and self._trained:
            try:
                recent = np.array(self._feature_history[-20:])
                hmm_state = int(self._hmm.predict(recent)[-1])
                probs = self._hmm.predict_proba(recent)[-1]
                hmm_confidence = float(probs[hmm_state])
                hmm_regime = self._state_labels.get(hmm_state, MicroRegime.DEAD)
            except Exception:
                pass

        gmm_cluster = -1
        if self._gmm and self._trained:
            try:
                gmm_cluster = int(self._gmm.predict(features.reshape(1, -1))[0])
            except Exception:
                pass

        mean_return = features[0]
        realized_vol = features[1]
        ema_spread = features[5]
        di_spread = features[10]

        trend_bias = 0.0
        if mean_return > 0.02 and ema_spread > 0:
            trend_bias = min(mean_return * 10 + ema_spread * 5, 1.0)
        elif mean_return < -0.02 and ema_spread < 0:
            trend_bias = max(mean_return * 10 + ema_spread * 5, -1.0)

        heuristic_regime = MicroRegime.DEAD
        if realized_vol < 0.05:
            heuristic_regime = MicroRegime.DEAD
        elif trend_bias > 0.3:
            heuristic_regime = MicroRegime.TRENDING_UP
        elif trend_bias < -0.3:
            heuristic_regime = MicroRegime.TRENDING_DOWN
        elif realized_vol > 0.2:
            heuristic_regime = MicroRegime.VOLATILE
        else:
            heuristic_regime = MicroRegime.MEAN_REVERTING

        if hmm_confidence > 0.6 and self._trained:
            regime = hmm_regime
            confidence = hmm_confidence
        elif hmm_confidence > 0.4 and self._trained and hmm_regime == heuristic_regime:
            regime = hmm_regime
            confidence = (hmm_confidence + 0.7) / 2
        else:
            regime = heuristic_regime
            confidence = 0.5

        if regime in (MicroRegime.TRENDING_UP, MicroRegime.TRENDING_DOWN):
            if abs(trend_bias) < 0.15:
                regime = MicroRegime.MEAN_REVERTING
                confidence *= 0.7

        state = MicroRegimeState(
            regime=regime,
            confidence=confidence,
            allowed_actions=REGIME_ACTIONS[regime],
            features={
                "mean_return": float(mean_return),
                "realized_vol": float(realized_vol),
                "ema_spread": float(ema_spread),
                "di_spread": float(di_spread),
                "trend_bias": float(trend_bias),
                "bb_position": float(features[7]),
                "rsi": float(features[2] * 100),
                "adx": float(features[9] * 100),
            },
            hmm_state=hmm_state,
            gmm_cluster=gmm_cluster,
            trend_bias=trend_bias,
            volatility_rank=float(realized_vol),
        )

        self._last_state = state
        return state

    @property
    def last_state(self) -> Optional[MicroRegimeState]:
        return self._last_state
