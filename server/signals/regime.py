import time
import logging
from enum import Enum
from dataclasses import dataclass

from server.signals.candles import CandleAggregator, Timeframe
from server.signals import indicators as ind

log = logging.getLogger("regime")


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE_RANGING = "volatile_ranging"
    DEAD = "dead"


@dataclass
class RegimeAssessment:
    regime: MarketRegime
    confidence: float
    adx: float
    plus_di: float
    minus_di: float
    bbw: float
    atr_ratio: float
    ema_9_15m: float
    ema_21_15m: float
    reason: str
    timestamp: float


class RegimeDetector:
    ADX_TREND_THRESHOLD = 25
    BBW_DEAD_THRESHOLD = 0.02
    BBW_RANGING_THRESHOLD = 0.04
    ATR_EXPANSION_THRESHOLD = 1.5
    MIN_REASSESS_INTERVAL = 60

    def __init__(self):
        self._regime = MarketRegime.DEAD
        self._confidence = 0.0
        self._last_assessment: RegimeAssessment | None = None
        self._history: list[RegimeAssessment] = []
        self._last_update = 0.0
        self._regime_since = 0.0

    def assess(self, candles: CandleAggregator) -> RegimeAssessment:
        now = time.time()
        if now - self._last_update < self.MIN_REASSESS_INTERVAL and self._last_assessment:
            return self._last_assessment

        closes_1h = candles.get_closes(Timeframe.H1, 50)
        highs_1h = candles.get_highs(Timeframe.H1, 50)
        lows_1h = candles.get_lows(Timeframe.H1, 50)
        closes_15m = candles.get_closes(Timeframe.M15, 50)
        highs_15m = candles.get_highs(Timeframe.M15, 50)
        lows_15m = candles.get_lows(Timeframe.M15, 50)

        use_1h = len(closes_1h) >= 30
        primary_closes = closes_1h if use_1h else closes_15m
        primary_highs = highs_1h if use_1h else highs_15m
        primary_lows = lows_1h if use_1h else lows_15m

        adx_val, plus_di, minus_di = ind.adx_with_di(primary_highs, primary_lows, primary_closes)
        bbw = ind.bollinger_band_width(primary_closes, 20)
        ema_9 = ind.ema(closes_15m, 9) if len(closes_15m) >= 9 else 0
        ema_21 = ind.ema(closes_15m, 21) if len(closes_15m) >= 21 else 0

        atr_now = ind.atr(primary_highs, primary_lows, primary_closes, 14)
        atr_prev = ind.atr(primary_highs[:-4], primary_lows[:-4], primary_closes[:-4], 14) if len(primary_closes) > 18 else atr_now
        atr_ratio = atr_now / atr_prev if atr_prev > 0 else 1.0

        regime = MarketRegime.DEAD
        confidence = 0.5
        reason = ""

        if adx_val >= self.ADX_TREND_THRESHOLD:
            if plus_di > minus_di:
                regime = MarketRegime.TRENDING_UP
                reason = f"ADX={adx_val:.1f} +DI={plus_di:.1f} > -DI={minus_di:.1f}"
            else:
                regime = MarketRegime.TRENDING_DOWN
                reason = f"ADX={adx_val:.1f} -DI={minus_di:.1f} > +DI={plus_di:.1f}"

            confidence = min(0.5 + (adx_val - self.ADX_TREND_THRESHOLD) / 40, 0.95)

            if ema_9 > 0 and ema_21 > 0:
                ema_agrees = (regime == MarketRegime.TRENDING_UP and ema_9 > ema_21) or \
                             (regime == MarketRegime.TRENDING_DOWN and ema_9 < ema_21)
                if ema_agrees:
                    confidence = min(confidence + 0.1, 0.95)
                else:
                    confidence *= 0.7
                    reason += " (EMA disagrees)"

        else:
            if bbw < self.BBW_DEAD_THRESHOLD and atr_ratio < 1.1:
                regime = MarketRegime.DEAD
                confidence = 0.8
                reason = f"BBW={bbw:.4f} < {self.BBW_DEAD_THRESHOLD}, ATR stable"
            elif bbw < self.BBW_RANGING_THRESHOLD:
                regime = MarketRegime.RANGING
                confidence = 0.6 + (self.BBW_RANGING_THRESHOLD - bbw) / self.BBW_RANGING_THRESHOLD * 0.3
                reason = f"ADX={adx_val:.1f} low, BBW={bbw:.4f} tight"
            else:
                regime = MarketRegime.VOLATILE_RANGING
                confidence = 0.7
                reason = f"ADX={adx_val:.1f} low, BBW={bbw:.4f} wide -- mean revert aggressively"

            if atr_ratio > self.ATR_EXPANSION_THRESHOLD and regime != MarketRegime.DEAD:
                regime = MarketRegime.VOLATILE_RANGING
                confidence = max(confidence, 0.75)
                reason = f"ATR expanding {atr_ratio:.1f}x -- volatile ranging"

        if self._regime != regime:
            time_in_regime = now - self._regime_since
            if time_in_regime < 7200:
                confidence *= 0.85
                reason += " (recent flip penalty)"
            self._regime_since = now

        self._regime = regime
        self._confidence = confidence
        self._last_update = now

        assessment = RegimeAssessment(
            regime=regime,
            confidence=confidence,
            adx=adx_val,
            plus_di=plus_di,
            minus_di=minus_di,
            bbw=bbw,
            atr_ratio=atr_ratio,
            ema_9_15m=ema_9,
            ema_21_15m=ema_21,
            reason=reason,
            timestamp=now,
        )

        self._last_assessment = assessment
        self._history.append(assessment)
        if len(self._history) > 500:
            self._history = self._history[-500:]

        return assessment

    @property
    def regime(self) -> MarketRegime:
        return self._regime

    @property
    def confidence(self) -> float:
        return self._confidence

    def get_state(self) -> dict:
        return {
            "regime": self._regime.value,
            "confidence": self._confidence,
            "last_update": self._last_update,
            "regime_since": self._regime_since,
        }

    def load_state(self, state: dict):
        self._regime = MarketRegime(state.get("regime", "dead"))
        self._confidence = state.get("confidence", 0)
        self._last_update = state.get("last_update", 0)
        self._regime_since = state.get("regime_since", 0)
