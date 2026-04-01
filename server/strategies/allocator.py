import time
import logging

from server.signals.regime import MarketRegime

log = logging.getLogger("allocator")


REGIME_ALLOCATIONS = {
    MarketRegime.DEAD:             {"leveraged_lp": 0.70, "volatility_scalper": 0.00, "funding_arb": 0.20, "jlp": 0.10, "smart_money_mirror": 0.00},
    MarketRegime.RANGING:          {"leveraged_lp": 0.45, "volatility_scalper": 0.25, "funding_arb": 0.15, "jlp": 0.05, "smart_money_mirror": 0.10},
    MarketRegime.VOLATILE_RANGING: {"leveraged_lp": 0.25, "volatility_scalper": 0.30, "funding_arb": 0.15, "jlp": 0.10, "smart_money_mirror": 0.20},
    MarketRegime.TRENDING_UP:      {"leveraged_lp": 0.20, "volatility_scalper": 0.35, "funding_arb": 0.15, "jlp": 0.05, "smart_money_mirror": 0.25},
    MarketRegime.TRENDING_DOWN:    {"leveraged_lp": 0.15, "volatility_scalper": 0.20, "funding_arb": 0.10, "jlp": 0.35, "smart_money_mirror": 0.20},
}

MAX_REBALANCES_PER_DAY = 12


class DynamicAllocator:
    REBALANCE_MIN_INTERVAL = 900
    DRIFT_THRESHOLD = 0.10
    MIN_REGIME_CONFIDENCE = 0.6

    def __init__(self):
        self._last_rebalance: float = 0
        self._last_regime: MarketRegime | None = None
        self._regime_stable_since: float = 0
        self._rebalance_count_today: int = 0
        self._day_start: float = 0

    def should_rebalance(
        self, current_regime: MarketRegime, current_allocations: dict[str, float],
        regime_confidence: float = 1.0, funding_apy: float = 0.0, volatility_2h: float = 0.0,
    ) -> dict[str, float] | None:
        now = time.time()

        today = int(now // 86400) * 86400
        if self._day_start < today:
            self._rebalance_count_today = 0
            self._day_start = today

        if self._rebalance_count_today >= MAX_REBALANCES_PER_DAY:
            return None

        if now - self._last_rebalance < self.REBALANCE_MIN_INTERVAL:
            return None

        if regime_confidence < self.MIN_REGIME_CONFIDENCE:
            return None

        if current_regime != self._last_regime:
            self._last_regime = current_regime
            self._regime_stable_since = now
            return None

        regime_stable_for = now - self._regime_stable_since
        if regime_stable_for < self.REBALANCE_MIN_INTERVAL:
            return None

        target = dict(REGIME_ALLOCATIONS.get(current_regime, REGIME_ALLOCATIONS[MarketRegime.RANGING]))

        if funding_apy > 30:
            shift = 0.15
            from_key = "leveraged_lp" if target.get("leveraged_lp", 0) > 0.20 else "volatility_scalper"
            target[from_key] = max(target.get(from_key, 0) - shift, 0.05)
            target["funding_arb"] = target.get("funding_arb", 0) + shift

        if volatility_2h > 0.05:
            lp_excess = max(target.get("leveraged_lp", 0) - 0.25, 0)
            if lp_excess > 0:
                target["leveraged_lp"] = 0.25
                target["jlp"] = target.get("jlp", 0) + lp_excess

        needs_rebalance = False
        for key, target_pct in target.items():
            current_pct = current_allocations.get(key, 0)
            if abs(target_pct - current_pct) > self.DRIFT_THRESHOLD:
                needs_rebalance = True
                break

        if not needs_rebalance:
            return None

        log.info(
            f"Rebalancing: regime={current_regime.value} conf={regime_confidence:.2f} "
            f"funding={funding_apy:.1f}% vol2h={volatility_2h:.4f} "
            f"current={current_allocations} -> target={target}"
        )
        self._last_rebalance = now
        self._rebalance_count_today += 1
        return target

    def get_state(self) -> dict:
        return {
            "last_rebalance": self._last_rebalance,
            "last_regime": self._last_regime.value if self._last_regime else None,
            "regime_stable_since": self._regime_stable_since,
            "rebalances_today": self._rebalance_count_today,
        }
