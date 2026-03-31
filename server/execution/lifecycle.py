import asyncio
import json
import time
import logging
from enum import Enum

from solders.keypair import Keypair

from server.config import STATE_DIR, ORCA_WHIRLPOOL_SOL_USDC
from server.execution.orca import OrcaExecutor
from server.execution.marginfi import MarginFiLender

log = logging.getLogger("lifecycle")

LIFECYCLE_FILE = STATE_DIR / "lifecycle.json"

MAX_RETRIES = 2
RETRY_DELAY = 5
POST_TX_DELAY = 2


class Phase(str, Enum):
    IDLE = "idle"
    CLOSE_LP = "close_lp"
    SWAP_FOR_REPAY = "swap_for_repay"
    REPAY_WITHDRAW = "repay_withdraw"
    DEPOSIT_COLLATERAL = "deposit_collateral"
    BORROW = "borrow"
    SWAP_FOR_LP = "swap_for_lp"
    OPEN_LP = "open_lp"
    ACTIVE = "active"
    FAILED = "failed"


class PositionLifecycle:
    def __init__(self, orca: OrcaExecutor, lender: MarginFiLender, keypair: Keypair):
        self.orca = orca
        self.lender = lender
        self.keypair = keypair
        self.phase = Phase.IDLE
        self.retries = 0
        self.position_mint: str | None = None
        self.borrowed_usd: float = 0.0
        self.equity_usd: float = 0.0
        self.lower_price: float = 0.0
        self.upper_price: float = 0.0
        self.leverage: float = 1.0
        self.range_pct: float = 0.03
        self.error: str = ""
        self.last_tx: str = ""
        self._load_state()

    def _save_state(self):
        data = {
            "phase": self.phase.value,
            "retries": self.retries,
            "position_mint": self.position_mint,
            "borrowed_usd": self.borrowed_usd,
            "equity_usd": self.equity_usd,
            "lower_price": self.lower_price,
            "upper_price": self.upper_price,
            "leverage": self.leverage,
            "range_pct": self.range_pct,
            "error": self.error,
            "last_tx": self.last_tx,
            "updated_at": time.time(),
        }
        STATE_DIR.mkdir(exist_ok=True)
        LIFECYCLE_FILE.write_text(json.dumps(data, indent=2))

    def _load_state(self):
        if not LIFECYCLE_FILE.exists():
            return
        try:
            data = json.loads(LIFECYCLE_FILE.read_text())
            self.phase = Phase(data.get("phase", "idle"))
            self.retries = data.get("retries", 0)
            self.position_mint = data.get("position_mint")
            self.borrowed_usd = data.get("borrowed_usd", 0)
            self.equity_usd = data.get("equity_usd", 0)
            self.lower_price = data.get("lower_price", 0)
            self.upper_price = data.get("upper_price", 0)
            self.leverage = data.get("leverage", 1.0)
            self.range_pct = data.get("range_pct", 0.03)
            self.error = data.get("error", "")
            self.last_tx = data.get("last_tx", "")
            if self.phase not in (Phase.IDLE, Phase.ACTIVE):
                log.warning(f"Lifecycle recovered in phase {self.phase.value} -- will resume")
        except Exception as e:
            log.error(f"Lifecycle state load failed: {e}")

    def get_state(self) -> dict:
        return {
            "phase": self.phase.value,
            "position_mint": self.position_mint,
            "borrowed_usd": self.borrowed_usd,
            "equity_usd": self.equity_usd,
            "leverage": self.leverage,
            "range_pct": self.range_pct,
            "error": self.error,
            "retries": self.retries,
        }

    def sync_mint(self, mint: str):
        if mint and mint != self.position_mint:
            log.info(f"Syncing lifecycle mint: {self.position_mint} -> {mint}")
            self.position_mint = mint
            self._save_state()

    async def _query_wallet(self) -> tuple[float, float]:
        from solders.pubkey import Pubkey
        from server.execution.orca import _derive_ata
        from server.config import USDC_MINT
        w = self.keypair.pubkey()
        bal = await self.orca.rpc.get_balance(w)
        sol = bal.value / 1e9

        usdc_ata = _derive_ata(w, Pubkey.from_string(USDC_MINT))
        try:
            resp = await self.orca.rpc.get_token_account_balance(usdc_ata)
            usdc = float(resp.value.ui_amount or 0)
        except Exception:
            usdc = 0.0
        return sol, usdc

    async def _has_lp_position(self) -> bool:
        if not self.position_mint:
            return False
        try:
            from solders.pubkey import Pubkey
            from server.execution.orca import _derive_position_pda
            mint = Pubkey.from_string(self.position_mint)
            pda, _ = _derive_position_pda(mint)
            resp = await self.orca.rpc.get_account_info(pda)
            return resp.value is not None
        except Exception:
            return False

    async def _has_marginfi_position(self) -> bool:
        await self.lender.recover_state()
        return self.lender.get_state()["has_position"]

    async def open(self, sol_price: float, equity_usd: float, leverage: float, range_pct: float,
                   lower_price: float = 0, upper_price: float = 0) -> dict:
        self.equity_usd = equity_usd
        self.leverage = leverage
        self.range_pct = range_pct
        if lower_price > 0 and upper_price > 0:
            self.lower_price = lower_price
            self.upper_price = upper_price
        self.error = ""
        self.retries = 0

        if await self._has_lp_position():
            log.warning("BLOCKED: LP position already exists on-chain")
            return {"status": "blocked", "reason": "position_exists"}

        if leverage > 1.0:
            self.phase = Phase.DEPOSIT_COLLATERAL
        else:
            self.phase = Phase.SWAP_FOR_LP
        self._save_state()

        return await self.resume(sol_price)

    async def close(self, sol_price: float) -> dict:
        self.error = ""
        self.retries = 0

        if not await self._has_lp_position():
            log.info("No LP position on-chain, skipping close")
            if await self._has_marginfi_position():
                self.phase = Phase.SWAP_FOR_REPAY
            else:
                self.phase = Phase.IDLE
            self._save_state()
            return await self.resume(sol_price)

        self.phase = Phase.CLOSE_LP
        self._save_state()
        return await self.resume(sol_price)

    async def resume(self, sol_price: float) -> dict:
        from server.alerts import alerts

        while self.phase not in (Phase.IDLE, Phase.ACTIVE, Phase.FAILED):
            try:
                log.info(f"Lifecycle phase: {self.phase.value} (retry {self.retries})")
                if self.phase == Phase.CLOSE_LP:
                    await self._do_close_lp()
                elif self.phase == Phase.SWAP_FOR_REPAY:
                    await self._do_swap_for_repay(sol_price)
                elif self.phase == Phase.REPAY_WITHDRAW:
                    await self._do_repay_withdraw()
                elif self.phase == Phase.DEPOSIT_COLLATERAL:
                    await self._do_deposit_collateral(sol_price)
                elif self.phase == Phase.BORROW:
                    await self._do_borrow(sol_price)
                elif self.phase == Phase.SWAP_FOR_LP:
                    await self._do_swap_for_lp(sol_price)
                elif self.phase == Phase.OPEN_LP:
                    await self._do_open_lp(sol_price)
                self.retries = 0
            except Exception as e:
                self.retries += 1
                self.error = f"{self.phase.value}: {str(e)[:200]}"
                log.error(f"Phase {self.phase.value} failed (attempt {self.retries}/{MAX_RETRIES}): {e}")
                self._save_state()

                await alerts.error_alert(
                    f"lifecycle_{self.phase.value}",
                    f"Retry {self.retries}/{MAX_RETRIES}: {str(e)[:100]}",
                )

                if self.retries > MAX_RETRIES:
                    log.error(f"Max retries exceeded in {self.phase.value}. Attempting cleanup.")
                    await self._cleanup_failed(sol_price)
                    return {"status": "failed", "phase": self.phase.value, "error": self.error}
                await asyncio.sleep(RETRY_DELAY)

        return {
            "status": self.phase.value,
            "position_mint": self.position_mint,
            "borrowed_usd": self.borrowed_usd,
            "equity_usd": self.equity_usd,
            "leverage": self.leverage,
        }

    async def _cleanup_failed(self, sol_price: float):
        from server.alerts import alerts
        log.warning("FAILED cleanup: attempting to recover funds")

        try:
            if await self._has_lp_position():
                log.info("Cleanup: closing orphaned LP position")
                await self.orca.close_position(self.keypair, self.position_mint)
                self.position_mint = None
        except Exception as e:
            log.error(f"Cleanup: LP close failed: {e}")

        try:
            if await self._has_marginfi_position():
                sol_bal, usdc_bal = await self._query_wallet()
                if self.borrowed_usd > 0:
                    usdc_needed = self.borrowed_usd * 1.02
                    shortfall = usdc_needed - usdc_bal
                    if shortfall > 0.50:
                        swap_sol = min(shortfall * 1.03 / sol_price, sol_bal - 0.05)
                        if swap_sol > 0.001:
                            await self.orca.swap(
                                self.keypair, ORCA_WHIRLPOOL_SOL_USDC,
                                int(swap_sol * 1e9), a_to_b=True,
                            )
                log.info("Cleanup: repaying MarginFi")
                await self.lender.repay_and_withdraw(self.keypair)
                self.borrowed_usd = 0
        except Exception as e:
            log.error(f"Cleanup: MarginFi repay failed: {e}")
            await alerts.error_alert("lifecycle_cleanup", f"Funds may be stuck in MarginFi: {e}")

        self.phase = Phase.FAILED
        self._save_state()

    async def _do_close_lp(self):
        if not self.position_mint:
            log.info("No position mint, skipping close_lp")
            self.phase = Phase.SWAP_FOR_REPAY if self.borrowed_usd > 0 else Phase.IDLE
            self._save_state()
            return

        if not await self._has_lp_position():
            log.info("LP already closed on-chain, advancing")
            self.phase = Phase.SWAP_FOR_REPAY if self.borrowed_usd > 0 else Phase.IDLE
            self._save_state()
            return

        result = await self.orca.close_position(self.keypair, self.position_mint)
        self.last_tx = result.get("signature", "")
        log.info(f"LP closed: {self.last_tx}")
        await asyncio.sleep(POST_TX_DELAY)

        if await self._has_lp_position():
            raise Exception(f"LP position still exists after close tx {self.last_tx}")

        self.position_mint = None

        if self.borrowed_usd > 0:
            self.phase = Phase.SWAP_FOR_REPAY
        else:
            self.phase = Phase.IDLE
        self._save_state()

    async def _do_swap_for_repay(self, sol_price: float):
        if not await self._has_marginfi_position():
            log.info("No MarginFi position, skipping repay")
            self.borrowed_usd = 0
            self.phase = Phase.IDLE
            self._save_state()
            return

        sol_bal, usdc_bal = await self._query_wallet()

        usdc_needed = self.borrowed_usd * 1.02
        usdc_shortfall = usdc_needed - usdc_bal
        if usdc_shortfall > 0.50:
            swap_sol = min(usdc_shortfall * 1.03 / sol_price, sol_bal - 0.05)
            if swap_sol < 0.001:
                raise Exception(f"Not enough SOL to swap for repay: need {usdc_shortfall:.2f} USDC, have {sol_bal:.4f} SOL")
            swap_lamports = int(swap_sol * 1e9)
            result = await self.orca.swap(
                self.keypair, ORCA_WHIRLPOOL_SOL_USDC, swap_lamports, a_to_b=True,
            )
            self.last_tx = result.get("signature", "")
            log.info(f"Swapped {swap_sol:.4f} SOL -> USDC for repay of ${self.borrowed_usd:.2f}: {self.last_tx}")
            await asyncio.sleep(POST_TX_DELAY)

            _, usdc_after = await self._query_wallet()
            if usdc_after < self.borrowed_usd * 0.95:
                log.warning(f"Post-swap USDC balance ${usdc_after:.2f} may be insufficient for ${self.borrowed_usd:.2f} repay")
        else:
            log.info(f"Enough USDC for repay ({usdc_bal:.2f} >= {usdc_needed:.2f})")

        self.phase = Phase.REPAY_WITHDRAW
        self._save_state()

    async def _do_repay_withdraw(self):
        if not await self._has_marginfi_position():
            log.info("No MarginFi position, nothing to repay")
            self.borrowed_usd = 0
            self.phase = Phase.IDLE
            self._save_state()
            return

        result = await self.lender.repay_and_withdraw(self.keypair)
        self.last_tx = result.get("signature", "")
        log.info(f"Repay+withdraw: {self.last_tx}")
        self.borrowed_usd = 0
        self.phase = Phase.IDLE
        self._save_state()

    async def _do_deposit_collateral(self, sol_price: float):
        has_mfi = await self._has_marginfi_position()

        if has_mfi and self.borrowed_usd > 0:
            log.info("MarginFi position with borrow already exists, skipping to SWAP_FOR_LP")
            self.phase = Phase.SWAP_FOR_LP
            self._save_state()
            return

        if has_mfi and self.borrowed_usd <= 0:
            log.info("MarginFi deposit exists but no borrow yet, advancing to BORROW")
            self.phase = Phase.BORROW
            self._save_state()
            return

        sol_bal, _ = await self._query_wallet()
        reserve_sol = 0.1
        max_sol = self.equity_usd / sol_price
        collateral_sol = min(max_sol, sol_bal - reserve_sol)

        if collateral_sol < 0.01:
            raise Exception(f"Not enough SOL for collateral: have {sol_bal:.4f}, need {max_sol:.4f}")

        max_borrow = self.lender.get_max_borrow(collateral_sol, sol_price, ltv=0.65)
        desired_borrow = self.equity_usd * (self.leverage - 1)
        borrow_usd = min(desired_borrow, max_borrow * 0.9)

        if borrow_usd < 0.50:
            log.warning(f"Borrow too small (${borrow_usd:.2f}), falling back to 1x")
            self.leverage = 1.0
            self.borrowed_usd = 0
            self.phase = Phase.SWAP_FOR_LP
            self._save_state()
            return

        result = await self.lender.deposit_and_borrow(self.keypair, collateral_sol, borrow_usd)
        self.last_tx = str(result)
        self.borrowed_usd = borrow_usd
        log.info(f"Deposited {collateral_sol:.4f} SOL, borrowed ${borrow_usd:.2f}: {self.last_tx}")

        self.phase = Phase.SWAP_FOR_LP
        self._save_state()

    async def _do_borrow(self, sol_price: float):
        _, usdc_bal = await self._query_wallet()
        if usdc_bal > 0.50:
            log.info(f"Already have ${usdc_bal:.2f} USDC, borrow likely already done")
            if self.borrowed_usd <= 0:
                self.borrowed_usd = usdc_bal
            self.phase = Phase.SWAP_FOR_LP
            self._save_state()
            return

        max_borrow = self.lender.get_max_borrow(self.equity_usd / sol_price, sol_price, ltv=0.65)
        desired_borrow = self.equity_usd * (self.leverage - 1)
        borrow_usd = min(desired_borrow, max_borrow * 0.9)

        if borrow_usd < 0.50:
            self.leverage = 1.0
            self.borrowed_usd = 0
            self.phase = Phase.SWAP_FOR_LP
            self._save_state()
            return

        from solders.pubkey import Pubkey
        from solders.instruction import Instruction, AccountMeta
        from solders.transaction import VersionedTransaction
        from solders.message import MessageV0
        from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
        from solana.rpc.types import TxOpts
        from server.execution.marginfi import (
            MARGINFI_PROGRAM, MARGINFI_GROUP, MFI_ACCOUNT,
            USDC_BANK, USDC_VAULT, TOKEN_PROGRAM, DISC,
            _derive_ata,
        )
        from server.config import USDC_MINT
        import struct

        w = self.keypair.pubkey()
        usdc_ata = _derive_ata(w, Pubkey.from_string(USDC_MINT))
        vault_auth, _ = Pubkey.find_program_address([b"liquidity_vault_auth", bytes(USDC_BANK)], MARGINFI_PROGRAM)
        usdc_atoms = int(borrow_usd * 1e6)

        borrow_ixs = [set_compute_unit_limit(400_000), set_compute_unit_price(100_000)]
        borrow_ixs.append(Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=True),
                AccountMeta(pubkey=MFI_ACCOUNT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=w, is_signer=True, is_writable=False),
                AccountMeta(pubkey=USDC_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=usdc_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=vault_auth, is_signer=False, is_writable=False),
                AccountMeta(pubkey=USDC_VAULT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ] + self.lender._health_remaining_accounts(),
            data=DISC["borrow"] + struct.pack("<Q", usdc_atoms),
        ))

        bh = (await self.lender.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(payer=w, instructions=borrow_ixs, address_lookup_table_accounts=[], recent_blockhash=bh)
        tx = VersionedTransaction(msg, [self.keypair])
        r = await self.lender.rpc.send_raw_transaction(bytes(tx), opts=TxOpts(skip_preflight=True))
        await self.lender._confirm(str(r))
        self.borrowed_usd = borrow_usd
        log.info(f"Borrow ${borrow_usd:.2f} USDC: {r}")

        self.phase = Phase.SWAP_FOR_LP
        self._save_state()

    async def _do_swap_for_lp(self, sol_price: float):
        sol_bal, usdc_bal = await self._query_wallet()
        total_target = self.equity_usd + self.borrowed_usd

        if self.leverage > 1.0 and self.borrowed_usd > 0:
            remaining_sol = sol_bal - 0.05
            if remaining_sol > 0.005:
                swap_lamports = int(remaining_sol * 1e9)
                await self.orca.swap(self.keypair, ORCA_WHIRLPOOL_SOL_USDC, swap_lamports, a_to_b=True)
                log.info(f"Swapped remaining {remaining_sol:.4f} SOL -> USDC")
                sol_bal, usdc_bal = await self._query_wallet()

            usdc_for_sol = min(usdc_bal / 2, total_target / 2)
            swap_atoms = int(usdc_for_sol * 1e6)
            if swap_atoms > 0:
                result = await self.orca.swap(self.keypair, ORCA_WHIRLPOOL_SOL_USDC, swap_atoms, a_to_b=False)
                self.last_tx = result.get("signature", "")
                log.info(f"Swapped ${usdc_for_sol:.2f} USDC -> SOL: {self.last_tx}")
        else:
            equity_sol = self.equity_usd / sol_price
            available_sol = sol_bal - 0.1
            sol_to_use = min(equity_sol, available_sol)
            sol_for_swap = sol_to_use / 2

            if sol_for_swap < 0.005:
                raise Exception(f"Not enough SOL for LP: have {sol_bal:.4f}, need {equity_sol:.4f}")

            swap_lamports = int(sol_for_swap * 1e9)
            result = await self.orca.swap(self.keypair, ORCA_WHIRLPOOL_SOL_USDC, swap_lamports, a_to_b=True)
            self.last_tx = result.get("signature", "")
            log.info(f"Swapped {sol_for_swap:.4f} SOL -> USDC (${sol_for_swap * sol_price:.2f}): {self.last_tx}")

        self.phase = Phase.OPEN_LP
        self._save_state()

    async def _do_open_lp(self, sol_price: float):
        if await self._has_lp_position():
            log.warning("LP position already exists, not opening another")
            self.phase = Phase.ACTIVE
            self._save_state()
            return

        sol_bal, usdc_bal = await self._query_wallet()

        if self.lower_price <= 0 or self.upper_price <= 0:
            self.lower_price = sol_price * (1 - self.range_pct)
            self.upper_price = sol_price * (1 + self.range_pct)

        pool_state = await self.orca.fetch_whirlpool_state()
        current_price = pool_state["current_price"]

        lower_tick = self.orca.price_to_tick(self.lower_price)
        upper_tick = self.orca.price_to_tick(self.upper_price)

        total_target = self.equity_usd + self.borrowed_usd
        max_sol_for_lp = min(sol_bal - 0.05, total_target / 2 / sol_price)
        max_usdc_for_lp = min(usdc_bal, total_target / 2)

        sol_for_lp = max(max_sol_for_lp, 0)
        usdc_for_lp = max(max_usdc_for_lp, 0)

        actual_sol, actual_usdc, liquidity = self.orca.calculate_liquidity_from_tokens(
            sol_for_lp, usdc_for_lp, current_price, self.lower_price, self.upper_price,
        )

        if liquidity <= 0:
            raise Exception(f"Zero liquidity from {sol_for_lp:.4f} SOL + ${usdc_for_lp:.2f} USDC")

        log.info(f"Opening LP: sol={actual_sol:.6f} usdc={actual_usdc:.2f} liq={liquidity} (target=${total_target:.2f})")

        result = await self.orca.open_position(
            self.keypair, ORCA_WHIRLPOOL_SOL_USDC,
            lower_tick, upper_tick, liquidity,
            actual_sol, actual_usdc,
        )

        self.position_mint = result.get("position_mint")
        self.lower_price = result.get("lower_price", self.lower_price)
        self.upper_price = result.get("upper_price", self.upper_price)
        self.last_tx = result.get("signature", "")
        log.info(f"LP opened: mint={self.position_mint} sig={self.last_tx}")

        self.phase = Phase.ACTIVE
        self._save_state()

    def reset(self):
        self.phase = Phase.IDLE
        self.position_mint = None
        self.borrowed_usd = 0
        self.equity_usd = 0
        self.error = ""
        self.retries = 0
        self._save_state()
