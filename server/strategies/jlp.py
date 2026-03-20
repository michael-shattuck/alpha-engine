import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import JLP_POOL


class JLPStrategy(BaseStrategy):
    STRATEGY_ID = "jlp"
    STRATEGY_NAME = "Jupiter Perps LP"

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)

    async def evaluate(self, market_data: dict) -> dict:
        if not self.active_positions and self.capital_allocated > 0:
            return {
                "action": "open",
                "pool": JLP_POOL,
                "deposit_usd": self.capital_allocated,
            }

        if self.active_positions:
            return {"action": "hold", "reason": "position_earning"}

        return {"action": "wait", "reason": "no_capital"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if action["action"] == "open":
            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=JLP_POOL,
                entry_price=sol_price,
                deposit_usd=action["deposit_usd"],
                current_value_usd=action["deposit_usd"],
                in_range=True,
                metadata={"jlp_entry_apy": market_data.get("jlp_apy", 0)},
            )
            self.positions.append(position)
            self.status = "active"
            return position

        return None

    async def update(self, market_data: dict):
        now = time.time()

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            fee_apy = 20.0
            hourly_rate = fee_apy / 100 / 365 / 24
            position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd

            sol_price = market_data.get("sol_price", 0)
            if position.entry_price > 0 and sol_price > 0:
                price_ratio = sol_price / position.entry_price
                jlp_exposure = 0.5
                nav_change = 1 + (price_ratio - 1) * jlp_exposure
                position.current_value_usd = position.deposit_usd * nav_change
                position.il_percent = (nav_change - 1) * 100

            position.in_range = True
            position.hours_in_range += hours_elapsed
            position.last_update = now

        effective_apy = 0.0
        if self.active_positions:
            pos = self.active_positions[0]
            total_return = (pos.current_value_usd + pos.fees_earned_usd - pos.deposit_usd) / pos.deposit_usd
            if pos.age_hours > 0:
                effective_apy = total_return / pos.age_hours * 8760 * 100

        self.last_update = now
        self.metrics = {
            "fee_apy": 20.0,
            "sol_exposure": 0.5,
            "effective_apy": effective_apy,
            "total_fees_earned": self.total_fees,
            "position_age_hours": self.active_positions[0].age_hours if self.active_positions else 0,
        }
