import math
import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import ORCA_WHIRLPOOL_SOL_USDC


class TightRangeLPStrategy(BaseStrategy):
    STRATEGY_ID = "tight_range_lp"
    STRATEGY_NAME = "Tight Range LP"

    def __init__(self, mode: str = "paper", range_pct: float = 0.05):
        super().__init__(mode=mode)
        self.range_pct = range_pct

    async def evaluate(self, market_data: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return {"action": "wait", "reason": "no_price_data"}

        for position in self.active_positions:
            if sol_price < position.lower_price or sol_price > position.upper_price:
                return {
                    "action": "rebalance",
                    "position_id": position.id,
                    "reason": "price_exited_range",
                    "current_price": sol_price,
                    "lower": position.lower_price,
                    "upper": position.upper_price,
                }

        if not self.active_positions and self.capital_allocated > 0:
            return {
                "action": "open",
                "pool": ORCA_WHIRLPOOL_SOL_USDC,
                "price": sol_price,
                "lower": sol_price * (1 - self.range_pct),
                "upper": sol_price * (1 + self.range_pct),
                "deposit_usd": self.capital_allocated,
            }

        return {"action": "hold", "reason": "position_in_range"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if action["action"] == "rebalance":
            old_position = self.close_position(action["position_id"])
            deposit = old_position.current_value_usd + old_position.fees_earned_usd if old_position else self.capital_allocated
            rebalance_count = (old_position.rebalance_count + 1) if old_position else 1

            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=ORCA_WHIRLPOOL_SOL_USDC,
                entry_price=sol_price,
                lower_price=sol_price * (1 - self.range_pct),
                upper_price=sol_price * (1 + self.range_pct),
                deposit_usd=deposit,
                current_value_usd=deposit,
                sol_amount=deposit / 2 / sol_price,
                usdc_amount=deposit / 2,
                rebalance_count=rebalance_count,
            )
            self.positions.append(position)
            self.status = "active"
            return position

        if action["action"] == "open":
            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=action["pool"],
                entry_price=sol_price,
                lower_price=action["lower"],
                upper_price=action["upper"],
                deposit_usd=action["deposit_usd"],
                current_value_usd=action["deposit_usd"],
                sol_amount=action["deposit_usd"] / 2 / sol_price,
                usdc_amount=action["deposit_usd"] / 2,
            )
            self.positions.append(position)
            self.status = "active"
            return position

        return None

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

            if in_range:
                price_ratio = sol_price / position.entry_price
                standard_il = 2 * math.sqrt(price_ratio) / (1 + price_ratio) - 1
                range_width = (position.upper_price - position.lower_price) / position.entry_price
                concentration_factor = min(2.0 / range_width, 10.0) if range_width > 0 else 1.0
                il_raw = standard_il * concentration_factor
                position.il_percent = il_raw * 100
                position.current_value_usd = position.deposit_usd * (1 + il_raw)
            elif sol_price < position.lower_price:
                sol_amount_at_exit = position.deposit_usd / position.lower_price
                position.current_value_usd = sol_amount_at_exit * sol_price
                position.il_percent = (position.current_value_usd / position.deposit_usd - 1) * 100
            else:
                position.current_value_usd = position.deposit_usd
                position.il_percent = 0.0

            if in_range:
                position.hours_in_range += hours_elapsed
                avg_lp_range = 0.10
                concentration_ratio = avg_lp_range / self.range_pct
                concentrated_multiplier = min(concentration_ratio, 4.0)
                hourly_rate = pool_apy / 100 / 365 / 24 * concentrated_multiplier
                position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd
            else:
                position.hours_out_of_range += hours_elapsed

            position.sol_amount = position.current_value_usd / 2 / sol_price if sol_price > 0 else 0
            position.usdc_amount = position.current_value_usd / 2
            position.last_update = now

        self.last_update = now
        avg_lp_range = 0.10
        actual_multiplier = min(avg_lp_range / self.range_pct, 4.0)
        self.metrics = {
            "range_pct": self.range_pct,
            "pool_apy": pool_apy,
            "concentrated_multiplier": actual_multiplier,
            "total_rebalances": sum(p.rebalance_count for p in self.active_positions),
            "avg_hours_in_range": self._avg_hours_in_range(),
        }

    def _avg_hours_in_range(self) -> float:
        active = self.active_positions
        if not active:
            return 0.0
        total_in = sum(p.hours_in_range for p in active)
        total_all = sum(p.hours_in_range + p.hours_out_of_range for p in active)
        if total_all <= 0:
            return 0.0
        return (total_in / total_all) * 100
