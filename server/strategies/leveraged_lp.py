import math
import time
import logging
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import ORCA_WHIRLPOOL_SOL_USDC, SOL_MINT, USDC_MINT, HELIUS_RPC_FAST, HELIUS_RPC_URL

HELIUS_RPC = HELIUS_RPC_FAST or HELIUS_RPC_URL
from server.execution.orca import OrcaExecutor
from server.execution.marginfi import MarginFiLender
from server.execution.lifecycle import PositionLifecycle, Phase
from server.execution.fee_tracker import FeeTracker
from server.strategies.optimizer import optimize_for_floor
from server.alerts import alerts

log = logging.getLogger("leveraged_lp")


class LeveragedLPStrategy(BaseStrategy):
    STRATEGY_ID = "leveraged_lp"
    STRATEGY_NAME = "Leveraged LP"

    BORROW_RATE_APY = 12.0
    REBALANCE_COST = 0.0008
    TX_COST_USD = 0.15
    MIN_COMPOUND_USD = 0.50
    RETURN_FLOOR_MONTHLY = 30.0

    MAX_ACTIONS_PER_HOUR = 5
    COOLDOWN_AFTER_CLOSE_SEC = 120
    COOLDOWN_AFTER_ERROR_SEC = 600

    def __init__(self, mode: str = "paper", base_leverage: float = 4.0, base_range: float = 0.03):
        super().__init__(mode=mode)
        self.base_leverage = base_leverage
        self.base_range = base_range
        self._price_history: list[tuple[float, float]] = []
        self.orca: OrcaExecutor | None = None
        self.lender: MarginFiLender | None = None
        self.lifecycle: PositionLifecycle | None = None
        self.fee_tracker: FeeTracker | None = None
        self._force_range: float | None = None
        self._last_fee_read: float = 0
        self._action_timestamps: list[float] = []
        self._last_close_time: float = 0
        self._last_error_time: float = 0
        self._has_opened_live: bool = False

    async def init_executors(self):
        if self.mode == "live" and not self.orca:
            self.orca = OrcaExecutor(paper_mode=False)
            await self.orca.start()
        if self.mode == "live" and not self.lender:
            self.lender = MarginFiLender(paper_mode=False)
            await self.lender.start()
        if self.mode == "live" and self.orca and self.lender and not self.lifecycle:
            self.lifecycle = PositionLifecycle(self.orca, self.lender, self.orca.keypair)
        if self.mode == "live" and self.orca and not self.fee_tracker:
            self.fee_tracker = FeeTracker(self.orca)

    async def recover_onchain_positions(self, sol_price: float):
        if self.mode != "live":
            return
        await self.init_executors()

        if self.lifecycle and self.lifecycle.phase not in (Phase.IDLE, Phase.ACTIVE):
            log.warning(f"Resuming interrupted lifecycle: phase={self.lifecycle.phase.value}")
            result = await self.lifecycle.resume(sol_price)
            if result["status"] == "active" and self.lifecycle.position_mint:
                log.info(f"Lifecycle resumed to ACTIVE: mint={self.lifecycle.position_mint}")
            elif result["status"] == "failed":
                log.error(f"Lifecycle resume failed: {self.lifecycle.error}")
                self.lifecycle.reset()
        try:
            import httpx
            from solders.pubkey import Pubkey
            WHIRLPOOL = Pubkey.from_string("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc")
            TOKEN_PROG = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
            wallet = str(self.orca.keypair.pubkey())
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.post(HELIUS_RPC, json={
                    "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                    "params": [wallet, {"programId": TOKEN_PROG}, {"encoding": "jsonParsed"}]
                })
                accounts = r.json().get("result", {}).get("value", [])
                for acct in accounts:
                    info = acct["account"]["data"]["parsed"]["info"]
                    if int(info["tokenAmount"]["amount"]) == 1 and info["tokenAmount"]["decimals"] == 0:
                        mint = info["mint"]
                        pos_pda, _ = Pubkey.find_program_address(
                            [b"position", bytes(Pubkey.from_string(mint))], WHIRLPOOL
                        )
                        r2 = await http.post(HELIUS_RPC, json={
                            "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                            "params": [str(pos_pda)]
                        })
                        if r2.json().get("result", {}).get("value"):
                            already_tracked = any(
                                p.metadata.get("position_mint") == mint
                                for p in self.active_positions
                            )
                            if not already_tracked:
                                import struct, base64, math
                                r3 = await http.post(HELIUS_RPC, json={
                                    "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                                    "params": [str(pos_pda), {"encoding": "base64"}]
                                })
                                data = base64.b64decode(r3.json()["result"]["value"]["data"][0])
                                tick_lower = struct.unpack_from("<i", data, 88)[0]
                                tick_upper = struct.unpack_from("<i", data, 92)[0]
                                lower_price = math.pow(1.0001, tick_lower) * 1000
                                upper_price = math.pow(1.0001, tick_upper) * 1000
                                rng = (upper_price - lower_price) / ((upper_price + lower_price) / 2)
                                log.warning(f"RECOVERED on-chain position: {mint} range=${lower_price:.2f}-${upper_price:.2f}")
                                position = StrategyPosition(
                                    id=f"{self.STRATEGY_ID}_recovered_{int(time.time())}",
                                    pool=ORCA_WHIRLPOOL_SOL_USDC,
                                    entry_price=sol_price,
                                    lower_price=lower_price,
                                    upper_price=upper_price,
                                    deposit_usd=self.capital_allocated,
                                    current_value_usd=self.capital_allocated,
                                    metadata={
                                        "equity": self.capital_allocated,
                                        "borrowed_usd": 0,
                                        "leverage": 1.0,
                                        "range_pct": rng / 2,
                                        "position_mint": mint,
                                        "recovered": True,
                                    },
                                )
                                self.positions.append(position)
                                self.status = "active"
                                if self.lifecycle:
                                    self.lifecycle.sync_mint(mint)
        except Exception as e:
            log.error(f"Position recovery failed: {e}")

    def _actions_this_hour(self) -> int:
        cutoff = time.time() - 3600
        self._action_timestamps = [t for t in self._action_timestamps if t > cutoff]
        return len(self._action_timestamps)

    def _record_action(self):
        self._action_timestamps.append(time.time())

    def _in_cooldown(self) -> bool:
        now = time.time()
        vol = self._volatility()
        close_cd = self.COOLDOWN_AFTER_CLOSE_SEC * (1 + vol * 20)
        if now - self._last_close_time < close_cd:
            return True
        if now - self._last_error_time < self.COOLDOWN_AFTER_ERROR_SEC:
            return True
        return False

    def _record_price(self, price: float):
        now = time.time()
        self._price_history.append((now, price))
        cutoff = now - 4 * 3600
        self._price_history = [(t, p) for t, p in self._price_history if t > cutoff]

    def _volatility(self, window_hours: float = 2.0) -> float:
        cutoff = time.time() - window_hours * 3600
        prices = [p for t, p in self._price_history if t > cutoff]
        if len(prices) < 5:
            return 0
        returns = [
            (prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(1, len(prices))
        ]
        return (sum(r ** 2 for r in returns) / len(returns)) ** 0.5

    def _trend(self, window_hours: float = 1.0) -> float:
        cutoff = time.time() - window_hours * 3600
        prices = [p for t, p in self._price_history if t > cutoff]
        if len(prices) < 3:
            return 0
        return (prices[-1] - prices[0]) / prices[0]

    def _optimized_params(self, pool_apy: float = 0) -> dict:
        if pool_apy <= 0:
            pool_apy = self.metrics.get("pool_apy", 50.0)
        vol = self._volatility()
        return optimize_for_floor(pool_apy, vol, self.base_leverage, self.RETURN_FLOOR_MONTHLY)

    def _optimal_range(self) -> float:
        if getattr(self, '_force_range', None):
            return self._force_range
        params = self._optimized_params()
        return params.get("range_pct", self.base_range)

    def _range_offset(self, sol_price: float) -> tuple[float, float]:
        trend = self._trend()
        rng = self._optimal_range()
        shift = trend * 0.3
        shift = max(-rng * 0.4, min(rng * 0.4, shift))
        lower = sol_price * (1 - rng + shift)
        upper = sol_price * (1 + rng + shift)
        return lower, upper

    def _current_leverage(self) -> float:
        params = self._optimized_params()
        vol = self._volatility()
        optimized_lev = params.get("leverage", self.base_leverage)
        if vol > 0.04:
            return min(optimized_lev, 1.5)
        if vol > 0.025:
            return min(optimized_lev, 2.0)
        return optimized_lev

    async def evaluate(self, market_data: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return {"action": "wait", "reason": "no_price_data"}

        self._record_price(sol_price)

        if self.mode == "live":
            if self._in_cooldown():
                return {"action": "wait", "reason": "cooldown_active"}
            if self._actions_this_hour() >= self.MAX_ACTIONS_PER_HOUR:
                log.warning(f"Max actions/hour ({self.MAX_ACTIONS_PER_HOUR}) reached. Halting.")
                return {"action": "wait", "reason": "max_actions_reached"}

        for position in self.active_positions:
            out_of_range = sol_price < position.lower_price or sol_price > position.upper_price
            if out_of_range:
                pool_apy = market_data.get("pool_apys", {}).get("orca_sol_usdc", 50.0)
                rng = position.metadata.get("range_pct", self.base_range)
                concentration = min(0.10 / rng, 6.0) if rng > 0 else 1
                hourly_fee_income = pool_apy / 100 / 365 / 24 * concentration * position.deposit_usd
                rebalance_cost = position.deposit_usd * self.REBALANCE_COST + self.TX_COST_USD * 3
                hours_to_recoup = rebalance_cost / hourly_fee_income if hourly_fee_income > 0 else 999

                if hours_to_recoup > 4:
                    log.info(f"Skipping rebalance: cost ${rebalance_cost:.2f} takes {hours_to_recoup:.1f}h to recoup")
                    return {"action": "hold", "reason": "rebalance_not_worth_it"}

                return {
                    "action": "rebalance",
                    "position_id": position.id,
                    "reason": "price_exited_range",
                }

            equity = position.metadata.get("equity", self.capital_allocated)
            borrowed = position.metadata.get("borrowed_usd", 0)
            net = position.current_value_usd + position.fees_earned_usd - borrowed
            health = net / borrowed if borrowed > 0 else 999
            if borrowed > 0 and health < 1.3:
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

        log.info(f"LP execute: mode={self.mode} action={action.get('action')} positions={len(self.active_positions)}")

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
            lower, upper = self._range_offset(sol_price)
            leveraged = equity * lev
            borrowed = leveraged - equity

            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=ORCA_WHIRLPOOL_SOL_USDC,
                entry_price=sol_price,
                lower_price=lower,
                upper_price=upper,
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

        if self.lifecycle.phase == Phase.FAILED:
            log.warning(f"Lifecycle in FAILED state: {self.lifecycle.error}. Attempting resume.")
            result = await self.lifecycle.resume(sol_price)
            if result["status"] == "failed":
                self.error = self.lifecycle.error
                self._last_error_time = time.time()
                await alerts.error_alert("lifecycle_failed", self.lifecycle.error)
                return None

        if act in ("deleverage", "rebalance", "resize", "compound"):
            pos = next((p for p in self.active_positions if p.id == action["position_id"]), None)
            if not pos:
                return None

            self.lifecycle.position_mint = pos.metadata.get("position_mint")
            self.lifecycle.borrowed_usd = pos.metadata.get("borrowed_usd", 0)
            self.lifecycle.equity_usd = pos.metadata.get("equity", self.capital_allocated)

            result = await self.lifecycle.close(sol_price)

            if result["status"] == "failed":
                self.error = self.lifecycle.error
                self._last_error_time = time.time()
                await alerts.error_alert(f"{act}_close", self.lifecycle.error)
                return None

            self._record_action()
            self._last_close_time = time.time()

            borrowed = pos.metadata.get("borrowed_usd", 0)
            net = pos.current_value_usd + pos.fees_earned_usd - borrowed
            if act != "deleverage":
                self.capital_allocated = max(net, self.capital_allocated)
            else:
                self.capital_allocated = max(net, 0)

            pnl = net - pos.metadata.get("equity", 0)
            self.close_position(action["position_id"])
            self.status = "idle"

            if act == "deleverage":
                await alerts.position_closed("deleverage", pnl)
            elif act == "compound":
                log.info(f"Compound: capital updated ${self.capital_allocated:.2f}")
            else:
                await alerts.rebalance(act, self.capital_allocated)
                log.info(f"Position closed for {act}. Capital=${self.capital_allocated:.2f}. Will reopen after cooldown.")

            return None

        if act == "open":
            await self.recover_onchain_positions(sol_price)
            if self.active_positions:
                log.warning("BLOCKED OPEN: on-chain position already exists.")
                return None

            rng = self._optimal_range()
            lev = self._current_leverage()
            equity_usd = self.capital_allocated
            lower, upper = self._range_offset(sol_price)

            result = await self.lifecycle.open(sol_price, equity_usd, lev, rng, lower, upper)

            if result["status"] == "failed":
                self.error = self.lifecycle.error
                self._last_error_time = time.time()
                self._last_close_time = time.time()
                await alerts.error_alert("open", self.lifecycle.error)
                return None

            if result["status"] != "active":
                log.warning(f"Open did not reach ACTIVE: {result}")
                return None

            self._record_action()
            self._has_opened_live = True

            actual_lev = result.get("leverage", 1.0)
            borrowed = result.get("borrowed_usd", 0)
            total_usd = equity_usd + borrowed

            await alerts.position_opened(equity_usd, actual_lev, borrowed, rng)

            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=ORCA_WHIRLPOOL_SOL_USDC,
                entry_price=sol_price,
                lower_price=self.lifecycle.lower_price,
                upper_price=self.lifecycle.upper_price,
                deposit_usd=total_usd,
                current_value_usd=total_usd,
                metadata={
                    "equity": equity_usd,
                    "borrowed_usd": borrowed,
                    "leverage": actual_lev,
                    "range_pct": rng,
                    "position_mint": self.lifecycle.position_mint,
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

        self._record_price(sol_price)

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            rng = position.metadata.get("range_pct", self.base_range)
            in_range = position.lower_price <= sol_price <= position.upper_price
            position.in_range = in_range

            sqrt_lower = math.sqrt(position.lower_price) if position.lower_price > 0 else 1
            sqrt_upper = math.sqrt(position.upper_price) if position.upper_price > 0 else 1
            sqrt_entry = math.sqrt(position.entry_price) if position.entry_price > 0 else 1
            sqrt_current = math.sqrt(sol_price) if sol_price > 0 else 1

            if position.entry_price > 0 and position.lower_price < position.entry_price < position.upper_price:
                L_unit = position.deposit_usd / (
                    (sqrt_entry - sqrt_lower) + sol_price * (1/sqrt_entry - 1/sqrt_upper)
                ) if (sqrt_entry - sqrt_lower + sol_price * (1/sqrt_entry - 1/sqrt_upper)) > 0 else 0
            else:
                L_unit = position.deposit_usd / (2 * (sqrt_upper - sqrt_lower)) if sqrt_upper > sqrt_lower else 0

            if sol_price <= position.lower_price:
                sol_val = L_unit * (1/sqrt_lower - 1/sqrt_upper) * sol_price
                usdc_val = 0
                position.hours_out_of_range += hours_elapsed
            elif sol_price >= position.upper_price:
                sol_val = 0
                usdc_val = L_unit * (sqrt_upper - sqrt_lower)
                position.hours_out_of_range += hours_elapsed
            else:
                sol_val = L_unit * (1/sqrt_current - 1/sqrt_upper) * sol_price
                usdc_val = L_unit * (sqrt_current - sqrt_lower)
                position.hours_in_range += hours_elapsed

            position.current_value_usd = sol_val + usdc_val
            position.sol_amount = sol_val / sol_price if sol_price > 0 else 0
            position.usdc_amount = usdc_val

            if in_range:
                concentration = min(0.10 / rng, 6.0) if rng > 0 else 1
                hourly_rate = pool_apy / 100 / 365 / 24 * concentration
                gross_fees = hourly_rate * hours_elapsed * position.deposit_usd
                if self.mode == "paper":
                    rebalance_drag = 0.30
                    gross_fees *= (1 - rebalance_drag)
                position.fees_earned_usd += gross_fees

            borrowed = position.metadata.get("borrowed_usd", 0)
            if borrowed > 0:
                borrow_cost = borrowed * (self.BORROW_RATE_APY / 100 / 365 / 24) * hours_elapsed
                position.fees_earned_usd -= borrow_cost

            equity = position.metadata.get("equity", self.capital_allocated)
            net = position.current_value_usd + position.fees_earned_usd - borrowed
            position.il_percent = ((net / equity) - 1) * 100 if equity > 0 else 0

            position.last_update = now

        if self.fee_tracker and self.active_positions and self.mode == "live":
            pos = self.active_positions[0]
            mint = pos.metadata.get("position_mint")
            if mint and now - self._last_fee_read > 300:
                try:
                    fee_data = await self.fee_tracker.read_fees(mint)
                    actual_apy = self.fee_tracker.get_actual_apy(pos.deposit_usd)
                    if actual_apy > 0:
                        log.info(f"On-chain fees: ${fee_data['hourly_rate_usd']:.4f}/hr, actual APY: {actual_apy:.0f}%")
                    self._last_fee_read = now
                except Exception as e:
                    log.debug(f"Fee read failed: {e}")

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

        opt = self._optimized_params(pool_apy)
        actual_fee_apy = self.fee_tracker.get_actual_apy(
            self.active_positions[0].deposit_usd
        ) if self.fee_tracker and self.active_positions else 0

        self.last_update = now
        self.metrics = {
            "leverage": effective_lev,
            "target_leverage": opt.get("leverage", self._current_leverage()),
            "range_pct": opt.get("range_pct", self._optimal_range()),
            "volatility": vol,
            "trend_1h": self._trend(1.0),
            "optimizer_monthly": opt.get("monthly", 0),
            "optimizer_gross_apy": opt.get("gross_apy", 0),
            "actual_fee_apy": actual_fee_apy,
            "return_floor": self.RETURN_FLOOR_MONTHLY,
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
