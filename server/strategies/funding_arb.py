import math
import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition

DRIFT_FUNDING_API = "https://data.api.drift.trade/fundingRates"


class FundingArbStrategy(BaseStrategy):
    STRATEGY_ID = "funding_arb"
    STRATEGY_NAME = "Funding Arb"

    MIN_FUNDING_APY = 15.0

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self._funding_rate_hourly = 0.0
        self._funding_apy = 0.0
        self._funding_direction = "neutral"

    async def evaluate(self, market_data: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return {"action": "wait", "reason": "no_price_data"}

        funding_apy = market_data.get("funding_apy", self._funding_apy)
        self._funding_apy = funding_apy

        if funding_apy > self.MIN_FUNDING_APY:
            self._funding_direction = "positive"
            if not self.active_positions and self.capital_allocated > 0:
                return {
                    "action": "open",
                    "deposit_usd": self.capital_allocated,
                    "funding_apy": funding_apy,
                }
        elif funding_apy < -self.MIN_FUNDING_APY:
            self._funding_direction = "negative"
            for pos in self.active_positions:
                return {
                    "action": "close",
                    "position_id": pos.id,
                    "reason": "funding_turned_negative",
                }
        else:
            self._funding_direction = "neutral"

        return {"action": "hold"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if action["action"] == "close":
            self.close_position(action["position_id"])
            self.status = "idle"
            return None

        if action["action"] == "open":
            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool="drift_sol_perp",
                entry_price=sol_price,
                deposit_usd=action["deposit_usd"],
                current_value_usd=action["deposit_usd"],
                in_range=True,
                metadata={
                    "entry_funding_apy": action["funding_apy"],
                    "hedge_type": "short_perp",
                    "notional": action["deposit_usd"],
                },
            )
            self.positions.append(position)
            self.status = "active"
            return position

        return None

    async def update(self, market_data: dict):
        now = time.time()
        funding_apy = market_data.get("funding_apy", self._funding_apy)

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            sol_price = market_data.get("sol_price", 0)
            if sol_price > 0 and position.entry_price > 0:
                price_change = (sol_price - position.entry_price) / position.entry_price
                short_pnl = -price_change * position.deposit_usd * 0.5
                long_value = position.deposit_usd * 0.5 * (1 + price_change)
                position.current_value_usd = long_value + position.deposit_usd * 0.5 + short_pnl

            if funding_apy > 0:
                hourly_funding = abs(funding_apy) / 100 / 365 / 24
                funding_income = hourly_funding * hours_elapsed * position.deposit_usd * 0.5
                position.fees_earned_usd += funding_income
            else:
                hourly_funding = abs(funding_apy) / 100 / 365 / 24
                funding_cost = hourly_funding * hours_elapsed * position.deposit_usd * 0.5
                position.fees_earned_usd -= funding_cost

            position.il_percent = 0.0
            position.in_range = True
            position.hours_in_range += hours_elapsed
            position.last_update = now

        self.last_update = now
        self.metrics = {
            "funding_apy": funding_apy,
            "funding_direction": self._funding_direction,
            "strategy": "short_perp_hedge + spot_hold" if self._funding_direction == "positive" else "idle",
        }
