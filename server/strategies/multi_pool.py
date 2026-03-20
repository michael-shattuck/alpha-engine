import math
import time
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition


class MultiPoolStrategy(BaseStrategy):
    STRATEGY_ID = "multi_pool"
    STRATEGY_NAME = "Multi-Pool"

    RANGE_PCT = 0.05
    MAX_POOLS = 5
    MIN_POOLS = 3
    DRIFT_THRESHOLD = 0.10

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)

    async def evaluate(self, market_data: dict) -> dict:
        pool_apys = market_data.get("pool_apys", {})
        if not pool_apys:
            return {"action": "wait", "reason": "no_pool_data"}

        ranked_pools = self._rank_pools(pool_apys)
        target_pools = ranked_pools[:self.MAX_POOLS]

        if len(target_pools) < self.MIN_POOLS:
            return {"action": "wait", "reason": "insufficient_pools"}

        if not self.active_positions and self.capital_allocated > 0:
            return {
                "action": "open_multi",
                "pools": target_pools,
                "deposit_usd": self.capital_allocated,
            }

        if self.active_positions:
            current_allocation = self._current_allocation()
            target_allocation = self._target_allocation(target_pools)
            max_drift = self._max_drift(current_allocation, target_allocation)

            if max_drift > self.DRIFT_THRESHOLD:
                return {
                    "action": "rebalance_multi",
                    "pools": target_pools,
                    "current_allocation": current_allocation,
                    "target_allocation": target_allocation,
                    "max_drift": max_drift,
                }

            return {"action": "hold", "reason": "allocation_within_threshold"}

        return {"action": "wait", "reason": "no_capital"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        if self.mode != "paper":
            return None

        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if action["action"] == "open_multi":
            return await self._open_multi_positions(action, sol_price, market_data)

        if action["action"] == "rebalance_multi":
            return await self._rebalance_positions(action, sol_price, market_data)

        return None

    async def _open_multi_positions(self, action: dict, sol_price: float, market_data: dict) -> Optional[StrategyPosition]:
        pools = action["pools"]
        total_deposit = action["deposit_usd"]
        total_weight = sum(p["weight"] for p in pools)
        last_position = None

        for pool_info in pools:
            allocation_fraction = pool_info["weight"] / total_weight
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
                metadata={"target_apy": pool_info["apy"], "pool_name": pool_info["pool"]},
            )
            self.positions.append(position)
            last_position = position

        self.status = "active"
        return last_position

    async def _rebalance_positions(self, action: dict, sol_price: float, market_data: dict) -> Optional[StrategyPosition]:
        total_value = sum(p.current_value_usd + p.fees_earned_usd for p in self.active_positions)

        for position in list(self.active_positions):
            self.close_position(position.id)

        pools = action["pools"]
        total_weight = sum(p["weight"] for p in pools)
        last_position = None

        for pool_info in pools:
            allocation_fraction = pool_info["weight"] / total_weight
            pool_deposit = total_value * allocation_fraction

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
                rebalance_count=1,
                metadata={"target_apy": pool_info["apy"], "pool_name": pool_info["pool"]},
            )
            self.positions.append(position)
            last_position = position

        return last_position

    async def update(self, market_data: dict):
        sol_price = market_data.get("sol_price", 0)
        pool_apys = market_data.get("pool_apys", {})
        now = time.time()

        per_pool_performance = {}

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            pool_apy = pool_apys.get(position.pool, position.metadata.get("target_apy", 20.0))

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

            per_pool_performance[position.pool] = {
                "apy": pool_apy,
                "pnl": position.pnl,
                "pnl_percent": position.pnl_percent,
                "fees": position.fees_earned_usd,
                "in_range": in_range,
            }

        self.last_update = now
        self.metrics = {
            "pool_count": len(self.active_positions),
            "per_pool": per_pool_performance,
            "total_pools_tracked": len(pool_apys),
        }

    def _rank_pools(self, pool_apys: dict) -> list[dict]:
        scored = []
        for pool, apy in pool_apys.items():
            risk_adjusted_score = apy * 0.8
            scored.append({"pool": pool, "apy": apy, "weight": risk_adjusted_score})
        scored.sort(key=lambda x: x["weight"], reverse=True)
        return scored

    def _current_allocation(self) -> dict:
        total_value = sum(p.current_value_usd for p in self.active_positions)
        if total_value <= 0:
            return {}
        return {
            p.pool: p.current_value_usd / total_value
            for p in self.active_positions
        }

    def _target_allocation(self, pools: list[dict]) -> dict:
        total_weight = sum(p["weight"] for p in pools)
        if total_weight <= 0:
            return {}
        return {p["pool"]: p["weight"] / total_weight for p in pools}

    def _max_drift(self, current: dict, target: dict) -> float:
        all_pools = set(current.keys()) | set(target.keys())
        if not all_pools:
            return 0.0
        return max(
            abs(current.get(pool, 0) - target.get(pool, 0))
            for pool in all_pools
        )
