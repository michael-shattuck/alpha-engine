import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition


class FeeCompounderStrategy(BaseStrategy):
    STRATEGY_ID = "fee_compounder"
    STRATEGY_NAME = "Fee Compounder"

    COMPOUND_THRESHOLD = 1.0

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self.metrics = {
            "total_compounded": 0.0,
            "compound_count": 0,
            "last_compound_time": 0.0,
            "effective_apy_boost": 0.0,
        }

    async def evaluate(self, market_data: dict) -> dict:
        strategy_fees = market_data.get("strategy_fees", {})
        total_uncollected = sum(strategy_fees.values())

        if total_uncollected >= self.COMPOUND_THRESHOLD:
            return {
                "action": "compound",
                "total_fees": total_uncollected,
                "fee_breakdown": strategy_fees,
            }

        return {
            "action": "wait",
            "reason": "insufficient_fees",
            "total_uncollected": total_uncollected,
        }

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        if action["action"] != "compound":
            return None

        now = time.time()
        total_fees = action["total_fees"]
        fee_breakdown = action.get("fee_breakdown", {})

        position = StrategyPosition(
            id=f"{self.STRATEGY_ID}_{int(now)}",
            pool="fee_compound",
            entry_price=market_data.get("sol_price", 0),
            deposit_usd=total_fees,
            current_value_usd=total_fees,
            in_range=True,
            metadata={
                "compound_source": fee_breakdown,
                "compound_time": now,
            },
        )
        self.positions.append(position)

        self.metrics["total_compounded"] += total_fees
        self.metrics["compound_count"] += 1
        self.metrics["last_compound_time"] = now

        self.status = "active"
        return position

    async def update(self, market_data: dict):
        now = time.time()

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            position.hours_in_range += hours_elapsed
            position.last_update = now

        total_compounded = self.metrics.get("total_compounded", 0)
        compound_count = self.metrics.get("compound_count", 0)
        first_compound = self.metrics.get("last_compound_time", 0)

        if compound_count > 0 and total_compounded > 0:
            all_positions = [p for p in self.positions if p.metadata.get("compound_time")]
            if len(all_positions) >= 2:
                first_time = min(p.metadata["compound_time"] for p in all_positions)
                hours_since_first = (now - first_time) / 3600
                if hours_since_first > 0:
                    annualized_rate = (total_compounded / hours_since_first) * 8760
                    self.metrics["effective_apy_boost"] = annualized_rate

        self.last_update = now
