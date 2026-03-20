import math
import time
import logging
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import ORCA_WHIRLPOOL_SOL_USDC, SOL_MINT, USDC_MINT
from server.execution.orca import OrcaExecutor

log = logging.getLogger("leveraged_lp")


class LeveragedLPStrategy(BaseStrategy):
    STRATEGY_ID = "leveraged_lp"
    STRATEGY_NAME = "Leveraged LP"

    BORROW_RATE_APY = 12.0
    REBALANCE_COST = 0.0008
    TX_COST_USD = 0.15
    MIN_COMPOUND_USD = 0.50

    MAX_ACTIONS_PER_HOUR = 3
    COOLDOWN_AFTER_CLOSE_SEC = 300
    COOLDOWN_AFTER_ERROR_SEC = 600

    def __init__(self, mode: str = "paper", base_leverage: float = 3.0, base_range: float = 0.05):
        super().__init__(mode=mode)
        self.base_leverage = base_leverage
        self.base_range = base_range
        self._price_buffer: list[float] = []
        self.orca: OrcaExecutor | None = None
        self._action_timestamps: list[float] = []
        self._last_close_time: float = 0
        self._last_error_time: float = 0
        self._has_opened_live: bool = False

    async def init_executors(self):
        if self.mode == "live" and not self.orca:
            self.orca = OrcaExecutor(paper_mode=False)
            await self.orca.start()

    def _actions_this_hour(self) -> int:
        cutoff = time.time() - 3600
        self._action_timestamps = [t for t in self._action_timestamps if t > cutoff]
        return len(self._action_timestamps)

    def _record_action(self):
        self._action_timestamps.append(time.time())

    def _in_cooldown(self) -> bool:
        now = time.time()
        if now - self._last_close_time < self.COOLDOWN_AFTER_CLOSE_SEC:
            return True
        if now - self._last_error_time < self.COOLDOWN_AFTER_ERROR_SEC:
            return True
        return False

    def _volatility(self) -> float:
        if len(self._price_buffer) < 3:
            return 0
        returns = [
            (self._price_buffer[i] - self._price_buffer[i - 1]) / self._price_buffer[i - 1]
            for i in range(1, len(self._price_buffer))
        ]
        return (sum(r ** 2 for r in returns) / len(returns)) ** 0.5

    def _optimal_range(self) -> float:
        if getattr(self, '_force_range', None):
            return self._force_range
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

        if self.mode == "live":
            if self._in_cooldown():
                return {"action": "wait", "reason": "cooldown_active"}
            if self._actions_this_hour() >= self.MAX_ACTIONS_PER_HOUR:
                log.warning(f"Max actions/hour ({self.MAX_ACTIONS_PER_HOUR}) reached. Halting.")
                return {"action": "wait", "reason": "max_actions_reached"}

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

            compound_min = max(self.TX_COST_USD * 10, self.MIN_COMPOUND_USD)
            if position.fees_earned_usd > compound_min and net > equity * 1.001:
                return {
                    "action": "compound",
                    "position_id": position.id,
                }

            current_range = position.metadata.get("range_pct", self.base_range)
            optimal = self._optimal_range()
            if current_range > 0 and abs(optimal - current_range) / current_range > 0.5:
                return {
                    "action": "resize",
                    "position_id": position.id,
                    "reason": "volatility_shift",
                }

        if not self.active_positions and self.capital_allocated > 0:
            if self.mode == "live" and self._has_opened_live and self._in_cooldown():
                return {"action": "wait", "reason": "cooldown_after_close"}
            return {"action": "open", "deposit_usd": self.capital_allocated}

        return {"action": "hold"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if self.mode == "live":
            return await self._execute_live(action, market_data)
        return await self._execute_paper(action, market_data)

    async def _execute_paper(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        sol_price = market_data["sol_price"]
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

    async def _execute_live(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        await self.init_executors()
        sol_price = market_data["sol_price"]
        act = action["action"]

        if self._actions_this_hour() >= self.MAX_ACTIONS_PER_HOUR:
            log.error("SAFETY: Max actions/hour reached. Refusing to execute.")
            return None

        if act == "deleverage":
            pos = next((p for p in self.active_positions if p.id == action["position_id"]), None)
            if pos and pos.metadata.get("position_mint"):
                try:
                    result = await self.orca.close_position(self.orca.keypair, pos.metadata["position_mint"])
                    log.info(f"Live close: {result.get('signature')}")
                    self._record_action()
                    self._last_close_time = time.time()
                except Exception as e:
                    log.error(f"Live close failed: {e}")
                    self.error = str(e)
                    self._last_error_time = time.time()
            self.close_position(action["position_id"])
            self.status = "idle"
            return None

        if act in ("rebalance", "resize"):
            old = next((p for p in self.active_positions if p.id == action["position_id"]), None)
            if old and old.metadata.get("position_mint"):
                try:
                    result = await self.orca.close_position(self.orca.keypair, old.metadata["position_mint"])
                    log.info(f"Live close for {act}: {result.get('signature')}")
                    self._record_action()
                    self._last_close_time = time.time()
                except Exception as e:
                    log.error(f"Live close for {act} failed: {e}")
                    self.error = str(e)
                    self._last_error_time = time.time()
                    return None
            self.close_position(action["position_id"])
            log.info(f"Position closed for {act}. Will reopen after cooldown.")
            self.status = "idle"
            return None

        if act == "open":
            rng = self._optimal_range()
            lower_price = sol_price * (1 - rng)
            upper_price = sol_price * (1 + rng)

            pool_state = await self.orca.fetch_whirlpool_state()
            current_price = pool_state["current_price"]

            balance_resp = await self.orca.rpc.get_balance(self.orca.keypair.pubkey())
            sol_balance = balance_resp.value / 1e9
            reserve_sol = 0.1

            target_usd = self.capital_allocated
            max_sol = target_usd / sol_price
            available_sol = max(sol_balance - reserve_sol, 0)
            deposit_sol = min(max_sol, available_sol)
            if deposit_sol * sol_price > target_usd * 1.05:
                deposit_sol = max_sol

            if deposit_sol < 0.05:
                log.error(f"Insufficient SOL: {sol_balance:.4f} available")
                self.error = "insufficient_balance"
                self._last_error_time = time.time()
                return None

            deposit_usd = deposit_sol * sol_price
            sol_for_lp = deposit_sol / 2
            sol_to_swap = deposit_sol / 2

            log.info(f"Live open: {deposit_sol:.4f} SOL (${deposit_usd:.2f})")

            try:
                swap_lamports = int(sol_to_swap * 1e9)
                swap_result = await self.orca.swap(
                    self.orca.keypair, ORCA_WHIRLPOOL_SOL_USDC,
                    swap_lamports, a_to_b=True,
                )
                log.info(f"Live swap: {swap_result.get('signature')}")
                usdc_amount = sol_to_swap * sol_price
            except Exception as e:
                log.error(f"Live swap failed: {e}")
                self.error = str(e)
                self._last_error_time = time.time()
                return None

            lower_tick = self.orca.price_to_tick(lower_price)
            upper_tick = self.orca.price_to_tick(upper_price)
            _, _, liquidity = self.orca.calculate_liquidity(
                deposit_usd, current_price, lower_price, upper_price
            )

            try:
                result = await self.orca.open_position(
                    self.orca.keypair, ORCA_WHIRLPOOL_SOL_USDC,
                    lower_tick, upper_tick, liquidity,
                    sol_for_lp, usdc_amount,
                )
                log.info(f"Live open: {result.get('signature')} mint={result.get('position_mint')}")
            except Exception as e:
                log.error(f"Live LP open failed (swap already done): {e}")
                self.error = f"LP_OPEN_FAILED_AFTER_SWAP: {e}"
                self._last_error_time = time.time()
                self._last_close_time = time.time()
                return None

            self._record_action()
            self._has_opened_live = True

            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=ORCA_WHIRLPOOL_SOL_USDC,
                entry_price=sol_price,
                lower_price=result.get("lower_price", lower_price),
                upper_price=result.get("upper_price", upper_price),
                deposit_usd=deposit_usd,
                current_value_usd=deposit_usd,
                sol_amount=sol_for_lp,
                usdc_amount=usdc_amount,
                metadata={
                    "equity": deposit_usd,
                    "borrowed_usd": 0,
                    "leverage": 1.0,
                    "range_pct": rng,
                    "position_mint": result.get("position_mint"),
                    "open_signature": result.get("signature"),
                },
            )
            self.positions.append(position)
            self.status = "active"
            self.error = ""
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
                ratio = sol_price / position.entry_price if position.entry_price > 0 else 1
                std_il = 2 * math.sqrt(ratio) / (1 + ratio) - 1
                rw = (position.upper_price - position.lower_price) / position.entry_price if position.entry_price > 0 else 0.1
                cf = min(2.0 / rw, 10.0) if rw > 0 else 1
                position.current_value_usd = position.deposit_usd * (1 + std_il * cf)

                concentration = min(0.10 / rng, 8.0) if rng > 0 else 1
                hourly_rate = pool_apy / 100 / 365 / 24 * concentration
                position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd
                position.hours_in_range += hours_elapsed
            elif sol_price < position.lower_price:
                sol_at_exit = position.deposit_usd / position.lower_price if position.lower_price > 0 else 0
                position.current_value_usd = sol_at_exit * sol_price
                position.hours_out_of_range += hours_elapsed
            else:
                position.current_value_usd = position.deposit_usd
                position.hours_out_of_range += hours_elapsed

            borrowed = position.metadata.get("borrowed_usd", 0)
            if borrowed > 0:
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
            if eq > 0 and pos.age_hours > 0.01:
                hourly_return = (net - eq) / eq / pos.age_hours
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
            "actions_this_hour": self._actions_this_hour(),
            "in_cooldown": self._in_cooldown(),
        }
