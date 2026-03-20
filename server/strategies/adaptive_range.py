import math
import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import ORCA_WHIRLPOOL_SOL_USDC


class AdaptiveRangeStrategy(BaseStrategy):
    STRATEGY_ID = "adaptive_range"
    STRATEGY_NAME = "Adaptive Range"

    MIN_RANGE = 0.02
    MAX_RANGE = 0.12
    BASE_RANGE = 0.05
    VOLATILITY_SCALE = 1.5

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self._current_range = self.BASE_RANGE

    def _optimal_range(self, market_data: dict) -> float:
        vol_1h = market_data.get("volatility_1h", 0)
        vol_24h = market_data.get("volatility_24h", 0)
        vol = max(vol_1h, vol_24h)

        if vol < 0.01:
            return self.MIN_RANGE
        elif vol < 0.03:
            return 0.03
        elif vol < 0.06:
            return self.BASE_RANGE
        elif vol < 0.10:
            return 0.08
        else:
            return self.MAX_RANGE

    async def evaluate(self, market_data: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return {"action": "wait", "reason": "no_price_data"}

        optimal_range = self._optimal_range(market_data)

        for position in self.active_positions:
            if sol_price < position.lower_price or sol_price > position.upper_price:
                return {
                    "action": "rebalance",
                    "position_id": position.id,
                    "reason": "price_exited_range",
                    "new_range": optimal_range,
                }

            current_range = position.metadata.get("range_pct", self.BASE_RANGE)
            range_drift = abs(optimal_range - current_range) / current_range
            if range_drift > 0.5:
                return {
                    "action": "resize",
                    "position_id": position.id,
                    "reason": "volatility_shift",
                    "old_range": current_range,
                    "new_range": optimal_range,
                }

        if not self.active_positions and self.capital_allocated > 0:
            return {
                "action": "open",
                "deposit_usd": self.capital_allocated,
                "range_pct": optimal_range,
            }

        return {"action": "hold"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        range_pct = action.get("new_range", action.get("range_pct", self.BASE_RANGE))

        if action["action"] in ("rebalance", "resize"):
            old = self.close_position(action["position_id"])
            deposit = (old.current_value_usd + old.fees_earned_usd) if old else self.capital_allocated
            rebal_cost = deposit * 0.001
            deposit -= rebal_cost
            rebalance_count = (old.rebalance_count + 1) if old else 1
        elif action["action"] == "open":
            deposit = action["deposit_usd"]
            rebalance_count = 0
        else:
            return None

        self._current_range = range_pct

        position = StrategyPosition(
            id=f"{self.STRATEGY_ID}_{int(time.time())}",
            pool=ORCA_WHIRLPOOL_SOL_USDC,
            entry_price=sol_price,
            lower_price=sol_price * (1 - range_pct),
            upper_price=sol_price * (1 + range_pct),
            deposit_usd=deposit,
            current_value_usd=deposit,
            sol_amount=deposit / 2 / sol_price,
            usdc_amount=deposit / 2,
            rebalance_count=rebalance_count,
            metadata={"range_pct": range_pct},
        )
        self.positions.append(position)
        self.status = "active"
        return position

    async def update(self, market_data: dict):
        sol_price = market_data.get("sol_price", 0)
        pool_apy = market_data.get("pool_apys", {}).get("orca_sol_usdc", 30.0)
        now = time.time()

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            in_range = position.lower_price <= sol_price <= position.upper_price
            position.in_range = in_range

            range_pct = position.metadata.get("range_pct", self.BASE_RANGE)

            if in_range:
                price_ratio = sol_price / position.entry_price
                standard_il = 2 * math.sqrt(price_ratio) / (1 + price_ratio) - 1
                range_width = (position.upper_price - position.lower_price) / position.entry_price
                concentration_factor = min(2.0 / range_width, 10.0) if range_width > 0 else 1.0
                il_raw = standard_il * concentration_factor
                position.il_percent = il_raw * 100
                position.current_value_usd = position.deposit_usd * (1 + il_raw)
            elif sol_price < position.lower_price:
                sol_at_exit = position.deposit_usd / position.lower_price
                position.current_value_usd = sol_at_exit * sol_price
                position.il_percent = (position.current_value_usd / position.deposit_usd - 1) * 100
            else:
                position.current_value_usd = position.deposit_usd
                position.il_percent = 0.0

            if in_range:
                position.hours_in_range += hours_elapsed
                concentration_mult = min(0.10 / range_pct, 8.0)
                hourly_rate = pool_apy / 100 / 365 / 24 * concentration_mult
                position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd
            else:
                position.hours_out_of_range += hours_elapsed

            position.last_update = now

        optimal = self._optimal_range(market_data)
        self.last_update = now
        self.metrics = {
            "current_range": self._current_range,
            "optimal_range": optimal,
            "pool_apy": pool_apy,
            "concentration_mult": min(0.10 / self._current_range, 8.0),
            "total_rebalances": sum(p.rebalance_count for p in self.active_positions),
        }
