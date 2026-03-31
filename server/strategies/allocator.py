import time
import logging

from server.signals.regime import MarketRegime

log = logging.getLogger("allocator")


REGIME_ALLOCATIONS = {
    MarketRegime.DEAD:              {"leveraged_lp": 0.90, "volatility_scalper": 0.10},
    MarketRegime.RANGING:           {"leveraged_lp": 0.50, "volatility_scalper": 0.50},
    MarketRegime.VOLATILE_RANGING:  {"leveraged_lp": 0.30, "volatility_scalper": 0.70},
    MarketRegime.TRENDING_UP:       {"leveraged_lp": 0.30, "volatility_scalper": 0.70},
    MarketRegime.TRENDING_DOWN:     {"leveraged_lp": 0.20, "volatility_scalper": 0.80},
}


class DynamicAllocator:
    REBALANCE_MIN_INTERVAL = 1800
    DRIFT_THRESHOLD = 0.10

    def __init__(self):
        self._last_rebalance: float = 0
        self._last_regime: MarketRegime | None = None
        self._regime_stable_since: float = 0

    def should_rebalance(
        self, current_regime: MarketRegime, current_allocations: dict[str, float]
    ) -> dict[str, float] | None:
        now = time.time()

        if now - self._last_rebalance < self.REBALANCE_MIN_INTERVAL:
            return None

        if current_regime != self._last_regime:
            self._last_regime = current_regime
            self._regime_stable_since = now
            return None

        regime_stable_for = now - self._regime_stable_since
        if regime_stable_for < self.REBALANCE_MIN_INTERVAL:
            return None

        target = REGIME_ALLOCATIONS.get(current_regime, REGIME_ALLOCATIONS[MarketRegime.RANGING])

        needs_rebalance = False
        for key, target_pct in target.items():
            current_pct = current_allocations.get(key, 0)
            if abs(target_pct - current_pct) > self.DRIFT_THRESHOLD:
                needs_rebalance = True
                break

        if not needs_rebalance:
            return None

        log.info(
            f"Rebalancing: regime={current_regime.value} "
            f"current={current_allocations} -> target={target}"
        )
        self._last_rebalance = now
        return target

    def get_state(self) -> dict:
        return {
            "last_rebalance": self._last_rebalance,
            "last_regime": self._last_regime.value if self._last_regime else None,
            "regime_stable_since": self._regime_stable_since,
        }
