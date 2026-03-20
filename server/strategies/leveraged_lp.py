import math
import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import ORCA_WHIRLPOOL_SOL_USDC


class LeveragedLPStrategy(BaseStrategy):
    STRATEGY_ID = "leveraged_lp"
    STRATEGY_NAME = "Leveraged LP"

    BORROW_RATE_APY = 12.0
    COMPOUND_THRESHOLD = 0.002
    REBALANCE_COST = 0.0008

    def __init__(self, mode: str = "paper", base_leverage: float = 3.0, base_range: float = 0.05):
        super().__init__(mode=mode)
        self.base_leverage = base_leverage
        self.base_range = base_range
        self._price_buffer: list[float] = []

    def _volatility(self) -> float:
        if len(self._price_buffer) < 3:
            return 0
        returns = [
            (self._price_buffer[i] - self._price_buffer[i - 1]) / self._price_buffer[i - 1]
            for i in range(1, len(self._price_buffer))
        ]
        return (sum(r ** 2 for r in returns) / len(returns)) ** 0.5

    def _optimal_range(self) -> float:
        vol = self._volatility()
        if vol < 0.005:
            return 0.02
        if vol < 0.015:
            return 0.03
        if vol < 0.03:
            return 0.05
        if vol < 0.06:
            return 0.08
        return 0.12

    def _current_leverage(self) -> float:
        vol = self._volatility()
        if vol > 0.04:
            return min(self.base_leverage, 1.5)
        if vol > 0.02:
            return min(self.base_leverage, 2.0)
        return self.base_leverage

    async def evaluate(self, market_data: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return {"action": "wait", "reason": "no_price_data"}

        self._price_buffer.append(sol_price)
        if len(self._price_buffer) > 24:
            self._price_buffer = self._price_buffer[-24:]

        for position in self.active_positions:
            if sol_price < position.lower_price or sol_price > position.upper_price:
                return {
                    "action": "rebalance",
                    "position_id": position.id,
                    "reason": "price_exited_range",
                }

            equity = position.metadata.get("equity", self.capital_allocated)
            borrowed = position.metadata.get("borrowed_usd", 0)
            net = position.current_value_usd + position.fees_earned_usd - borrowed
            if borrowed > 0 and net / borrowed < 0.2:
                return {
                    "action": "deleverage",
                    "position_id": position.id,
                    "reason": "near_liquidation",
                }

            if equity > 0 and position.fees_earned_usd > equity * self.COMPOUND_THRESHOLD:
                if net > equity * 1.001:
                    return {
                        "action": "compound",
                        "position_id": position.id,
                    }

            current_range = position.metadata.get("range_pct", self.base_range)
            optimal = self._optimal_range()
            if abs(optimal - current_range) / current_range > 0.5:
                return {
                    "action": "resize",
                    "position_id": position.id,
                    "reason": "volatility_shift",
                }

        if not self.active_positions and self.capital_allocated > 0:
            return {"action": "open", "deposit_usd": self.capital_allocated}

        return {"action": "hold"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        act = action["action"]

        if act == "deleverage":
            pos = self.close_position(action["position_id"])
            if pos:
                borrowed = pos.metadata.get("borrowed_usd", 0)
                net = pos.current_value_usd + pos.fees_earned_usd - borrowed
                self.capital_allocated = max(net, 0)
            self.status = "idle"
            return None

        if act in ("open", "rebalance", "compound", "resize"):
            if act in ("rebalance", "compound", "resize"):
                old = self.close_position(action["position_id"])
                if old:
                    borrowed = old.metadata.get("borrowed_usd", 0)
                    net = old.current_value_usd + old.fees_earned_usd - borrowed
                    cost = net * self.REBALANCE_COST if act != "compound" else 0
                    equity = max(net - cost, 1)
                else:
                    equity = self.capital_allocated
            else:
                equity = action["deposit_usd"]

            lev = self._current_leverage()
            rng = self._optimal_range()
            leveraged = equity * lev
            borrowed = leveraged - equity

            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=ORCA_WHIRLPOOL_SOL_USDC,
                entry_price=sol_price,
                lower_price=sol_price * (1 - rng),
                upper_price=sol_price * (1 + rng),
                deposit_usd=leveraged,
                current_value_usd=leveraged,
                sol_amount=leveraged / 2 / sol_price,
                usdc_amount=leveraged / 2,
                metadata={
                    "equity": equity,
                    "borrowed_usd": borrowed,
                    "leverage": lev,
                    "range_pct": rng,
                },
            )
            self.positions.append(position)
            self.status = "active"
            return position

        return None

    async def update(self, market_data: dict):
        sol_price = market_data.get("sol_price", 0)
        pool_apy = market_data.get("pool_apys", {}).get("orca_sol_usdc", 30.0)
        now = time.time()

        self._price_buffer.append(sol_price)
        if len(self._price_buffer) > 24:
            self._price_buffer = self._price_buffer[-24:]

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            rng = position.metadata.get("range_pct", self.base_range)
            in_range = position.lower_price <= sol_price <= position.upper_price
            position.in_range = in_range

            if in_range:
                ratio = sol_price / position.entry_price
                std_il = 2 * math.sqrt(ratio) / (1 + ratio) - 1
                rw = (position.upper_price - position.lower_price) / position.entry_price
                cf = min(2.0 / rw, 10.0) if rw > 0 else 1
                position.current_value_usd = position.deposit_usd * (1 + std_il * cf)

                concentration = min(0.10 / rng, 8.0)
                hourly_rate = pool_apy / 100 / 365 / 24 * concentration
                position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd
                position.hours_in_range += hours_elapsed
            elif sol_price < position.lower_price:
                sol_at_exit = position.deposit_usd / position.lower_price
                position.current_value_usd = sol_at_exit * sol_price
                position.hours_out_of_range += hours_elapsed
            else:
                position.current_value_usd = position.deposit_usd
                position.hours_out_of_range += hours_elapsed

            borrowed = position.metadata.get("borrowed_usd", 0)
            borrow_cost = borrowed * (self.BORROW_RATE_APY / 100 / 365 / 24) * hours_elapsed
            position.fees_earned_usd -= borrow_cost

            equity = position.metadata.get("equity", self.capital_allocated)
            net = position.current_value_usd + position.fees_earned_usd - borrowed
            position.il_percent = ((net / equity) - 1) * 100 if equity > 0 else 0

            position.last_update = now

        vol = self._volatility()
        equity = sum(p.metadata.get("equity", 0) for p in self.active_positions)
        borrowed = sum(p.metadata.get("borrowed_usd", 0) for p in self.active_positions)
        net_value = sum(
            p.current_value_usd + p.fees_earned_usd - p.metadata.get("borrowed_usd", 0)
            for p in self.active_positions
        )
        effective_lev = (equity + borrowed) / equity if equity > 0 else 0
        health = net_value / borrowed if borrowed > 0 else 999

        projected_apy = 0
        if self.active_positions:
            pos = self.active_positions[0]
            eq = pos.metadata.get("equity", 0)
            net = pos.current_value_usd + pos.fees_earned_usd - pos.metadata.get("borrowed_usd", 0)
            if eq > 0 and pos.age_hours > 0.1:
                hourly_return = (net - eq) / eq / pos.age_hours if pos.age_hours > 0.01 else 0
                projected_apy = hourly_return * 8760 * 100

        self.last_update = now
        self.metrics = {
            "leverage": effective_lev,
            "target_leverage": self._current_leverage(),
            "range_pct": self._optimal_range(),
            "volatility": vol,
            "equity": equity,
            "borrowed": borrowed,
            "net_value": net_value,
            "health_factor": health,
            "borrow_rate_apy": self.BORROW_RATE_APY,
            "pool_apy": pool_apy,
            "projected_apy": projected_apy,
        }
