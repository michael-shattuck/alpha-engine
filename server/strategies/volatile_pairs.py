import math
import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition


class VolatilePairsStrategy(BaseStrategy):
    STRATEGY_ID = "volatile_pairs"
    STRATEGY_NAME = "Volatile Pairs"

    RANGE_PCT = 0.03
    MIN_APY = 100.0
    MIN_TVL = 500_000
    MAX_IL_LOSS_PCT = 3.0

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self.metrics = {
            "avg_apy": 0.0,
            "positions_exited_for_risk": 0,
            "best_performing_pool": "",
        }

    async def evaluate(self, market_data: dict) -> dict:
        pool_apys = market_data.get("pool_apys", {})
        sol_price = market_data.get("sol_price", 0)

        risk_exits = []
        for position in self.active_positions:
            if position.il_percent < -self.MAX_IL_LOSS_PCT:
                risk_exits.append(position.id)

        if risk_exits:
            return {
                "action": "exit_risk",
                "position_ids": risk_exits,
                "reason": "il_exceeds_threshold",
            }

        eligible_pools = self._score_pools(pool_apys)

        if not self.active_positions and self.capital_allocated > 0 and eligible_pools:
            return {
                "action": "open",
                "pools": eligible_pools[:3],
                "deposit_usd": self.capital_allocated,
            }

        if self.active_positions:
            return {"action": "hold", "reason": "monitoring_positions"}

        return {"action": "wait", "reason": "no_eligible_pools"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if action["action"] == "exit_risk":
            for position_id in action["position_ids"]:
                self.close_position(position_id)
                self.metrics["positions_exited_for_risk"] += 1
            return None

        if action["action"] == "open":
            pools = action["pools"]
            total_deposit = action["deposit_usd"]
            total_score = sum(p["score"] for p in pools)
            last_position = None

            for pool_info in pools:
                allocation_fraction = pool_info["score"] / total_score
                pool_deposit = total_deposit * allocation_fraction

                position = StrategyPosition(
                    id=f"{self.STRATEGY_ID}_{pool_info['pool']}_{int(time.time())}",
                    pool=pool_info["pool"],
                    entry_price=sol_price,
                    lower_price=sol_price * (1 - self.RANGE_PCT),
                    upper_price=sol_price * (1 + self.RANGE_PCT),
                    deposit_usd=pool_deposit,
                    current_value_usd=pool_deposit,
                    sol_amount=pool_deposit / 2 / sol_price,
                    usdc_amount=pool_deposit / 2,
                    metadata={
                        "target_apy": pool_info["apy"],
                        "entry_score": pool_info["score"],
                        "pool_name": pool_info["pool"],
                    },
                )
                self.positions.append(position)
                last_position = position

            self.status = "active"
            return last_position

        return None

    async def update(self, market_data: dict):
        sol_price = market_data.get("sol_price", 0)
        pool_apys = market_data.get("pool_apys", {})
        now = time.time()

        active_apys = []
        best_pool = ""
        best_pnl = float("-inf")

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            pool_apy = pool_apys.get(position.pool, position.metadata.get("target_apy", 100.0))
            active_apys.append(pool_apy)

            in_range = position.lower_price <= sol_price <= position.upper_price
            position.in_range = in_range

            if in_range:
                price_ratio = sol_price / position.entry_price if position.entry_price > 0 else 1.0
                il_raw = 2 * math.sqrt(price_ratio) / (1 + price_ratio) - 1
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
                hourly_rate = pool_apy / 100 / 365 / 24
                position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd
            else:
                position.hours_out_of_range += hours_elapsed

            position.sol_amount = position.current_value_usd / 2 / sol_price if sol_price > 0 else 0
            position.usdc_amount = position.current_value_usd / 2
            position.last_update = now

            if position.pnl > best_pnl:
                best_pnl = position.pnl
                best_pool = position.pool

        self.last_update = now
        self.metrics["avg_apy"] = sum(active_apys) / len(active_apys) if active_apys else 0.0
        self.metrics["best_performing_pool"] = best_pool

    def _score_pools(self, pool_apys: dict) -> list[dict]:
        scored = []
        for pool, apy in pool_apys.items():
            if apy < self.MIN_APY:
                continue
            tvl_estimate = self.MIN_TVL
            score = apy * math.sqrt(tvl_estimate)
            scored.append({"pool": pool, "apy": apy, "score": score})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored
