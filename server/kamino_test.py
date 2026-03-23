#!/usr/bin/env python3
"""
Kamino lending live test -- validates deposit/borrow/repay/withdraw with minimal funds.

Steps:
1. Check wallet balance
2. Deposit 0.01 SOL as collateral
3. Borrow ~$0.50 USDC against it
4. Verify USDC received
5. Repay the USDC
6. Withdraw SOL collateral
7. Report results

Run: cd alpha_engine && python3 -m server.kamino_test
"""

import asyncio
import sys
import base58
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("kamino_test")

sys.path.insert(0, ".")

from server.config import SOLANA_RPC_URL, WALLET_PRIVATE_KEY
from server.execution.kamino import KaminoLender

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed


TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
USDC_MINT = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")


def _derive_ata(owner, mint):
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM
    )
    return pda


async def get_usdc_balance(rpc, wallet):
    ata = _derive_ata(wallet, USDC_MINT)
    try:
        resp = await rpc.get_account_info(ata)
        if resp.value:
            import struct
            data = bytes(resp.value.data)
            amount = struct.unpack_from("<Q", data, 64)[0]
            return amount / 1e6
    except:
        pass
    return 0.0


async def run_test():
    kp = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))
    rpc = AsyncClient(SOLANA_RPC_URL, commitment=Confirmed)
    kamino = KaminoLender(paper_mode=False)
    await kamino.start()

    wallet = kp.pubkey()
    results = {"passed": 0, "failed": 0}

    def record(name, passed, detail=""):
        icon = "+" if passed else "X"
        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1
        print(f"  [{icon}] {name}: {detail}")

    print(f"\n{'='*60}")
    print(f"  KAMINO LENDING LIVE TEST")
    print(f"{'='*60}")
    print(f"  Wallet: {wallet}")
    print(f"{'='*60}\n")

    # Step 1: Check balance
    print("Step 1: Check wallet balance")
    bal = await rpc.get_balance(wallet)
    sol = bal.value / 1e9
    usdc_before = await get_usdc_balance(rpc, wallet)
    record("SOL balance", sol > 0.05, f"{sol:.4f} SOL")
    record("USDC balance", True, f"{usdc_before:.6f} USDC")

    if sol < 0.05:
        print("  ABORT: Need at least 0.05 SOL")
        return

    # Step 2: Deposit 0.01 SOL
    print("\nStep 2: Deposit 0.01 SOL as collateral")
    try:
        result = await kamino.deposit_collateral(kp, 0.01)
        sig = result.get("signature", "")
        status = result.get("status", "")
        record("Deposit", status == "confirmed", f"sig={sig[:20]}... status={status}")
    except Exception as e:
        record("Deposit", False, str(e)[:100])
        print(f"\n  DEPOSIT FAILED: {e}")
        print(f"  Kamino account layout may need fixing.")
        print(f"  Remaining tests skipped.")
        await rpc.close()
        return

    # Step 3: Borrow 0.50 USDC
    print("\nStep 3: Borrow $0.50 USDC")
    try:
        result = await kamino.borrow_usdc(kp, 0.50)
        sig = result.get("signature", "")
        status = result.get("status", "")
        record("Borrow", status == "confirmed", f"sig={sig[:20]}... status={status}")
    except Exception as e:
        record("Borrow", False, str(e)[:100])
        print(f"\n  BORROW FAILED: {e}")

    # Step 4: Verify USDC received
    print("\nStep 4: Verify USDC received")
    await asyncio.sleep(2)
    usdc_after = await get_usdc_balance(rpc, wallet)
    gained = usdc_after - usdc_before
    record("USDC received", gained > 0.1, f"before={usdc_before:.6f} after={usdc_after:.6f} gained={gained:.6f}")

    # Step 5: Repay USDC
    print("\nStep 5: Repay $0.50 USDC")
    try:
        result = await kamino.repay_usdc(kp, 0.50)
        sig = result.get("signature", "")
        status = result.get("status", "")
        record("Repay", status == "confirmed", f"sig={sig[:20]}... status={status}")
    except Exception as e:
        record("Repay", False, str(e)[:100])

    # Step 6: Withdraw collateral
    print("\nStep 6: Withdraw 0.01 SOL collateral")
    try:
        result = await kamino.withdraw_collateral(kp, 0.01)
        sig = result.get("signature", "")
        status = result.get("status", "")
        record("Withdraw", status == "confirmed", f"sig={sig[:20]}... status={status}")
    except Exception as e:
        record("Withdraw", False, str(e)[:100])

    # Summary
    sol_after = (await rpc.get_balance(wallet)).value / 1e9
    print(f"\n{'='*60}")
    print(f"  RESULTS: {results['passed']}/{results['passed']+results['failed']} passed")
    print(f"  SOL: {sol:.4f} -> {sol_after:.4f} (cost: {sol - sol_after:.4f})")
    print(f"{'='*60}")

    if results["failed"] == 0:
        print(f"\n  ALL TESTS PASSED. Kamino lending validated.")
    else:
        print(f"\n  {results['failed']} TESTS FAILED. Need fixes before 3x leverage.")

    await rpc.close()


if __name__ == "__main__":
    asyncio.run(run_test())
