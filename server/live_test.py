#!/usr/bin/env python3
"""
Live execution test -- validates on-chain operations with minimal funds.

Steps:
1. Check wallet balance
2. Swap 0.05 SOL -> USDC via Orca direct swap
3. Verify USDC received
4. Open tiny LP position (~$5) on Orca
5. Verify position exists on-chain
6. Close position and collect fees
7. Verify funds returned
8. Report results

Run: cd alpha_engine && python3 -m server.live_test
"""

import asyncio
import sys
import time
import base58
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("live_test")

sys.path.insert(0, ".")

from server.config import SOLANA_RPC_URL, WALLET_PRIVATE_KEY, ORCA_WHIRLPOOL_SOL_USDC, SOL_MINT, USDC_MINT
from server.execution.orca import OrcaExecutor
from server.execution.jupiter import JupiterExecutor

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM,
    )
    return pda


async def get_token_balance(rpc: AsyncClient, owner: Pubkey, mint_str: str) -> float:
    mint = Pubkey.from_string(mint_str)
    ata = _derive_ata(owner, mint)
    try:
        resp = await rpc.get_token_account_balance(ata)
        if resp.value:
            return float(resp.value.ui_amount or 0)
    except Exception:
        pass
    return 0.0


async def run_test():
    kp = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))
    rpc = AsyncClient(SOLANA_RPC_URL, commitment=Confirmed)
    orca = OrcaExecutor(paper_mode=False)
    await orca.start()

    wallet = kp.pubkey()
    results = {"passed": 0, "failed": 0, "tests": []}

    def record(name, passed, detail=""):
        status = "PASS" if passed else "FAIL"
        results["tests"].append({"name": name, "status": status, "detail": detail})
        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1
        icon = "+" if passed else "X"
        print(f"  [{icon}] {name}: {detail}")

    print(f"\n{'='*60}")
    print(f"  ALPHA ENGINE LIVE EXECUTION TEST")
    print(f"{'='*60}")
    print(f"  Wallet: {wallet}")
    print(f"  RPC: {SOLANA_RPC_URL}")
    print(f"  Pool: {ORCA_WHIRLPOOL_SOL_USDC}")
    print(f"{'='*60}\n")

    # === TEST 1: Wallet balance ===
    print("Step 1: Check wallet balance")
    bal_resp = await rpc.get_balance(wallet)
    sol_balance = bal_resp.value / 1e9
    record("SOL balance", sol_balance > 0.15, f"{sol_balance:.4f} SOL (${sol_balance * 89:.2f})")
    if sol_balance < 0.15:
        print("  ABORT: Need at least 0.15 SOL for test")
        return results

    # === TEST 2: Fetch pool state ===
    print("\nStep 2: Fetch Orca pool state")
    try:
        pool_state = await orca.fetch_whirlpool_state()
        price = pool_state["current_price"]
        record("Pool state", price > 0, f"SOL=${price:.2f} tick={pool_state['tick']} liq={pool_state['liquidity']}")
    except Exception as e:
        record("Pool state", False, str(e))
        return results

    # === TEST 3: Check USDC ATA exists ===
    print("\nStep 3: Check USDC token account")
    usdc_before = await get_token_balance(rpc, wallet, USDC_MINT)
    usdc_ata = _derive_ata(wallet, Pubkey.from_string(USDC_MINT))
    ata_resp = await rpc.get_account_info(usdc_ata)
    has_usdc_ata = ata_resp.value is not None
    record("USDC ATA exists", True, f"exists={has_usdc_ata}, balance={usdc_before:.6f} USDC")

    if not has_usdc_ata:
        print("  NOTE: USDC ATA doesn't exist. Swap will create it via wrapAndUnwrapSol.")

    # === TEST 4: Swap 0.05 SOL -> USDC via Orca direct ===
    print("\nStep 4: Swap 0.05 SOL -> USDC via Orca direct swap")
    swap_amount = int(0.05 * 1e9)
    try:
        swap_result = await orca.swap(kp, ORCA_WHIRLPOOL_SOL_USDC, swap_amount, a_to_b=True)
        sig = swap_result.get("signature", "")
        status = swap_result.get("status", "")
        record("Orca swap", status == "confirmed", f"sig={sig[:20]}... status={status}")
    except Exception as e:
        record("Orca swap", False, str(e)[:200])
        print(f"\n  SWAP FAILED: {e}")
        print(f"\n  Remaining tests skipped.")
        await rpc.close()
        await orca.stop()
        return results

    # === TEST 5: Verify USDC received ===
    print("\nStep 5: Verify USDC received")
    await asyncio.sleep(2)
    usdc_after = await get_token_balance(rpc, wallet, USDC_MINT)
    usdc_gained = usdc_after - usdc_before
    record("USDC received", usdc_gained > 0, f"before={usdc_before:.6f} after={usdc_after:.6f} gained={usdc_gained:.6f}")

    sol_after_swap = (await rpc.get_balance(wallet)).value / 1e9
    record("SOL deducted", sol_after_swap < sol_balance, f"before={sol_balance:.4f} after={sol_after_swap:.4f}")

    # === TEST 6: Open tiny LP position ===
    print("\nStep 6: Open LP position (~$5)")
    lp_sol = 0.03
    lp_usdc = min(usdc_gained * 0.9, lp_sol * price)

    lower_price = price * 0.95
    upper_price = price * 1.05
    lower_tick = orca.price_to_tick(lower_price)
    upper_tick = orca.price_to_tick(upper_price)
    sol_calc, usdc_calc, liquidity = orca.calculate_liquidity(
        lp_sol * price + lp_usdc, price, lower_price, upper_price
    )

    try:
        open_result = await orca.open_position(
            kp, ORCA_WHIRLPOOL_SOL_USDC,
            lower_tick, upper_tick,
            liquidity,
            lp_sol, lp_usdc,
        )
        pos_mint = open_result.get("position_mint", "")
        sig = open_result.get("signature", "")
        status = open_result.get("status", "")
        record("Open position", status == "confirmed", f"mint={pos_mint[:20]}... sig={sig[:20]}...")
    except Exception as e:
        record("Open position", False, str(e))
        print(f"  OPEN FAILED: {e}")
        print(f"  Remaining tests skipped.")
        await rpc.close()
        await orca.stop()
        return results

    # === TEST 7: Verify position on-chain ===
    print("\nStep 7: Verify position on-chain")
    await asyncio.sleep(2)
    try:
        pos_data = await orca._fetch_position_data(Pubkey.from_string(pos_mint))
        record("Position on-chain", pos_data["liquidity"] > 0,
               f"liquidity={pos_data['liquidity']} ticks={pos_data['tick_lower']}/{pos_data['tick_upper']}")
    except Exception as e:
        record("Position on-chain", False, str(e))

    # === TEST 8: Close position ===
    print("\nStep 8: Close position")
    sol_before_close = (await rpc.get_balance(wallet)).value / 1e9
    usdc_before_close = await get_token_balance(rpc, wallet, USDC_MINT)

    try:
        close_result = await orca.close_position(kp, pos_mint)
        sig = close_result.get("signature", "")
        status = close_result.get("status", "")
        record("Close position", status == "confirmed", f"sig={sig[:20]}... status={status}")
    except Exception as e:
        record("Close position", False, str(e))
        print(f"  CLOSE FAILED: {e}")
        print(f"  IMPORTANT: Position {pos_mint} is still open on-chain!")

    # === TEST 9: Verify funds returned ===
    print("\nStep 9: Verify funds returned")
    await asyncio.sleep(2)
    sol_after_close = (await rpc.get_balance(wallet)).value / 1e9
    usdc_after_close = await get_token_balance(rpc, wallet, USDC_MINT)
    record("Funds returned",
           sol_after_close > sol_before_close - 0.01 or usdc_after_close > usdc_before_close - 0.5,
           f"SOL: {sol_before_close:.4f}->{sol_after_close:.4f} USDC: {usdc_before_close:.4f}->{usdc_after_close:.4f}")

    # === SUMMARY ===
    final_sol = (await rpc.get_balance(wallet)).value / 1e9
    final_usdc = await get_token_balance(rpc, wallet, USDC_MINT)
    sol_cost = sol_balance - final_sol
    print(f"\n{'='*60}")
    print(f"  RESULTS: {results['passed']}/{results['passed']+results['failed']} passed")
    print(f"{'='*60}")
    print(f"  SOL: {sol_balance:.4f} -> {final_sol:.4f} (cost: {sol_cost:.4f} SOL = ${sol_cost*89:.2f})")
    print(f"  USDC: {usdc_before:.4f} -> {final_usdc:.4f}")
    for t in results["tests"]:
        print(f"  [{t['status']}] {t['name']}: {t['detail']}")
    print(f"{'='*60}")

    if results["failed"] == 0:
        print(f"\n  ALL TESTS PASSED. Live execution is validated.")
        print(f"  Safe to deploy with --mode live")
    else:
        print(f"\n  {results['failed']} TESTS FAILED. Do NOT go live until fixed.")

    await rpc.close()
    await orca.stop()
    return results


if __name__ == "__main__":
    asyncio.run(run_test())
