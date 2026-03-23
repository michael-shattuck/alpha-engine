import math
import time
import logging
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import ORCA_WHIRLPOOL_SOL_USDC, SOL_MINT, USDC_MINT
from server.execution.orca import OrcaExecutor
from server.execution.marginfi import MarginFiLender
from server.alerts import alerts

log = logging.getLogger("leveraged_lp")


class LeveragedLPStrategy(BaseStrategy):
    STRATEGY_ID = "leveraged_lp"
    STRATEGY_NAME = "Leveraged LP"

    BORROW_RATE_APY = 12.0
    REBALANCE_COST = 0.0008
    TX_COST_USD = 0.15
    MIN_COMPOUND_USD = 0.50

    MAX_ACTIONS_PER_HOUR = 5
    COOLDOWN_AFTER_CLOSE_SEC = 120
    COOLDOWN_AFTER_ERROR_SEC = 600

    def __init__(self, mode: str = "paper", base_leverage: float = 4.0, base_range: float = 0.03):
        super().__init__(mode=mode)
        self.base_leverage = base_leverage
        self.base_range = base_range
        self._price_buffer: list[float] = []
        self.orca: OrcaExecutor | None = None
        self.lender: MarginFiLender | None = None
        self._force_range: float | None = None
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

    async def recover_onchain_positions(self, sol_price: float):
        if self.mode != "live":
            return
        await self.init_executors()
        try:
            import httpx
            from solders.pubkey import Pubkey
            WHIRLPOOL = Pubkey.from_string("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc")
            TOKEN_PROG = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
            wallet = str(self.orca.keypair.pubkey())
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.post("https://johnath-nf0ci1-fast-mainnet.helius-rpc.com", json={
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
                        r2 = await http.post("https://johnath-nf0ci1-fast-mainnet.helius-rpc.com", json={
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
                                r3 = await http.post("https://johnath-nf0ci1-fast-mainnet.helius-rpc.com", json={
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
            return 0.03
        if vol < 0.015:
            return 0.04
        if vol < 0.03:
            return 0.06
        if vol < 0.06:
            return 0.10
        return 0.15

    def _current_leverage(self) -> float:
        vol = self._volatility()
        if vol > 0.04:
            return min(self.base_leverage, 1.5)
        if vol > 0.02:
            return min(self.base_leverage, 2.5)
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
                if pos.metadata.get("borrowed_usd", 0) > 0 and self.lender:
                    await self._repay_leverage(sol_price)
                pnl = pos.current_value_usd + pos.fees_earned_usd - pos.metadata.get("equity", 0)
                await alerts.position_closed("deleverage", pnl)
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
                    if old.metadata.get("borrowed_usd", 0) > 0 and self.lender:
                        await self._repay_leverage(sol_price)
                    borrowed = old.metadata.get("borrowed_usd", 0)
                    net = old.current_value_usd + old.fees_earned_usd - borrowed
                    self.capital_allocated = max(net, self.capital_allocated)
                    self.close_position(action["position_id"])
                    log.info(f"Position closed for {act}. Capital=${self.capital_allocated:.2f}. Will reopen after cooldown.")
                    self.status = "idle"
                    await alerts.rebalance(act, self.capital_allocated)
                except Exception as e:
                    log.error(f"REBALANCE CLOSE FAILED - DISABLING STRATEGY: {e}")
                    self.error = f"REBALANCE_FAILED: {str(e)[:100]}"
                    self.enabled = False
                    self._last_error_time = time.time()
                    await alerts.error_alert(f"{act}_close", str(e))
            return None

        if act == "compound":
            old = next((p for p in self.active_positions if p.id == action["position_id"]), None)
            if old and old.metadata.get("position_mint"):
                try:
                    result = await self.orca.close_position(self.orca.keypair, old.metadata["position_mint"])
                    log.info(f"Live close for compound: {result.get('signature')}")
                    self._record_action()
                    self._last_close_time = time.time()
                    if old.metadata.get("borrowed_usd", 0) > 0 and self.lender:
                        await self._repay_leverage(sol_price)
                    borrowed = old.metadata.get("borrowed_usd", 0)
                    net = old.current_value_usd + old.fees_earned_usd - borrowed
                    self.capital_allocated = max(net, self.capital_allocated)
                    log.info(f"Compound: capital updated ${self.capital_allocated:.2f} (was ${old.metadata.get('equity', 0):.2f})")
                    self.close_position(action["position_id"])
                    self.status = "idle"
                except Exception as e:
                    log.error(f"COMPOUND CLOSE FAILED - DISABLING STRATEGY: {e}")
                    self.error = f"COMPOUND_FAILED: {str(e)[:100]}"
                    self.enabled = False
                    self._last_error_time = time.time()
                    await alerts.error_alert("compound_close", str(e))
            return None

        if act == "open":
            await self.recover_onchain_positions(sol_price)
            if self.active_positions:
                log.warning("BLOCKED OPEN: on-chain position already exists. Not opening another.")
                return None

            rng = self._optimal_range()
            lev = self._current_leverage()
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
            equity_sol = min(max_sol, available_sol)

            if equity_sol < 0.02:
                log.error(f"Insufficient SOL: {sol_balance:.4f} available")
                self.error = "insufficient_balance"
                self._last_error_time = time.time()
                return None

            equity_usd = equity_sol * sol_price
            borrowed_usdc = 0.0

            if lev > 1.0 and self.lender:
                collateral_sol = equity_sol
                max_borrow = self.lender.get_max_borrow(collateral_sol, sol_price, ltv=0.65)
                desired_borrow = equity_usd * (lev - 1)
                borrow_usdc = min(desired_borrow, max_borrow * 0.9)

                if borrow_usdc < 0.50:
                    log.warning(f"Borrow too small (${borrow_usdc:.2f}), proceeding at 1x")
                    lev = 1.0
                else:
                    log.info(f"Leverage: depositing {collateral_sol:.4f} SOL, borrowing ${borrow_usdc:.2f} USDC")
                    try:
                        mfi_result = await self.lender.deposit_and_borrow(
                            self.orca.keypair, collateral_sol, borrow_usdc,
                        )
                        log.info(f"MarginFi: {mfi_result}")
                        borrowed_usdc = borrow_usdc
                        equity_sol = 0
                    except Exception as e:
                        log.error(f"MarginFi deposit+borrow failed, falling back to 1x: {e}")
                        lev = 1.0

            total_usd = equity_usd + borrowed_usdc

            if lev > 1.0 and borrowed_usdc > 0:
                sol_for_lp = 0
                sol_to_swap = 0
                usdc_from_borrow = borrowed_usdc

                swap_sol = equity_sol
                if swap_sol > 0.005:
                    try:
                        swap_lamports = int(swap_sol * 1e9)
                        swap_result = await self.orca.swap(
                            self.orca.keypair, ORCA_WHIRLPOOL_SOL_USDC,
                            swap_lamports, a_to_b=True,
                        )
                        log.info(f"Swap remaining SOL to USDC: {swap_result.get('signature')}")
                        usdc_from_borrow += swap_sol * sol_price
                    except Exception as e:
                        log.error(f"SOL->USDC swap failed: {e}")

                usdc_for_sol_side = usdc_from_borrow / 2
                usdc_for_usdc_side = usdc_from_borrow / 2

                try:
                    swap_atoms = int(usdc_for_sol_side * 1e6)
                    swap_result = await self.orca.swap(
                        self.orca.keypair, ORCA_WHIRLPOOL_SOL_USDC,
                        swap_atoms, a_to_b=False,
                    )
                    log.info(f"USDC->SOL swap: {swap_result.get('signature')}")
                    sol_for_lp = usdc_for_sol_side / sol_price
                except Exception as e:
                    log.error(f"USDC->SOL swap failed: {e}")
                    self.error = str(e)
                    self._last_error_time = time.time()
                    return None

                usdc_amount = usdc_for_usdc_side
            else:
                sol_for_lp = equity_sol / 2
                sol_to_swap = equity_sol / 2

                log.info(f"Live open 1x: {equity_sol:.4f} SOL (${equity_usd:.2f})")

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
            actual_sol, actual_usdc, liquidity = self.orca.calculate_liquidity_from_tokens(
                sol_for_lp, usdc_amount, current_price, lower_price, upper_price,
            )
            log.info(f"Liquidity calc: sol={actual_sol:.6f} usdc={actual_usdc:.2f} liq={liquidity}")

            try:
                result = await self.orca.open_position(
                    self.orca.keypair, ORCA_WHIRLPOOL_SOL_USDC,
                    lower_tick, upper_tick, liquidity,
                    actual_sol, actual_usdc,
                )
                log.info(f"Live open: {result.get('signature')} mint={result.get('position_mint')}")
            except Exception as e:
                log.error(f"Live LP open failed (swaps already done): {e}")
                self.error = f"LP_OPEN_FAILED_AFTER_SWAP: {e}"
                self._last_error_time = time.time()
                self._last_close_time = time.time()
                return None

            self._record_action()
            self._has_opened_live = True
            await alerts.position_opened(equity_usd, lev if borrowed_usdc > 0 else 1.0, borrowed_usdc, rng)

            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=ORCA_WHIRLPOOL_SOL_USDC,
                entry_price=sol_price,
                lower_price=result.get("lower_price", lower_price),
                upper_price=result.get("upper_price", upper_price),
                deposit_usd=total_usd,
                current_value_usd=total_usd,
                sol_amount=actual_sol,
                usdc_amount=actual_usdc,
                metadata={
                    "equity": equity_usd,
                    "borrowed_usd": borrowed_usdc,
                    "leverage": lev if borrowed_usdc > 0 else 1.0,
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

    async def _repay_leverage(self, sol_price: float):
        if not self.lender:
            return
        await self.lender.recover_state()
        if self.lender.borrowed_usdc <= 0 and self.lender.deposited_sol <= 0:
            return
        if self.lender.borrowed_usdc > 0:
            try:
                usdc_owed = self.lender.borrowed_usdc
                swap_sol = (usdc_owed * 1.05) / sol_price
                balance_resp = await self.orca.rpc.get_balance(self.orca.keypair.pubkey())
                available_sol = balance_resp.value / 1e9 - 0.05
                swap_sol = min(swap_sol, max(available_sol, 0))
                if swap_sol > 0.001:
                    swap_lamports = int(swap_sol * 1e9)
                    swap_result = await self.orca.swap(
                        self.orca.keypair, ORCA_WHIRLPOOL_SOL_USDC,
                        swap_lamports, a_to_b=True,
                    )
                    log.info(f"Swapped {swap_sol:.4f} SOL->USDC for repay: {swap_result.get('signature')}")
            except Exception as e:
                log.error(f"SOL->USDC swap for repay failed: {e}")
        try:
            result = await self.lender.repay_and_withdraw(self.orca.keypair)
            log.info(f"MarginFi repay+withdraw: {result}")
        except Exception as e:
            log.error(f"MarginFi repay+withdraw failed: {e}")
            self.error = f"REPAY_FAILED: {str(e)[:100]}"

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
                position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd

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
