#!/usr/bin/env python3
"""
MarginFi lending live test via Helius RPC.
Tests: init account, deposit 0.01 SOL, borrow $0.50 USDC, repay, withdraw.

Run: cd alpha_engine && python3 -m server.marginfi_test
"""

import asyncio
import sys
import base58
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

sys.path.insert(0, ".")

from server.config import WALLET_PRIVATE_KEY
from server.execution.marginfi import MarginFiLender

from solders.keypair import Keypair


async def run():
    kp = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))
    lender = MarginFiLender(paper_mode=False)
    await lender.start()

    print(f"\n{'='*60}")
    print(f"  MARGINFI LENDING TEST (via Helius)")
    print(f"{'='*60}")
    print(f"  Wallet: {kp.pubkey()}\n")

    # Step 1: Find or create MarginFi account
    print("Step 1: Find/create MarginFi account")
    try:
        account = await lender._find_or_create_marginfi_account(kp)
        print(f"  [+] MarginFi account: {account}")
    except Exception as e:
        print(f"  [X] Failed: {e}")
        await lender.stop()
        return

    # Step 2: Deposit 0.01 SOL
    print("\nStep 2: Deposit 0.01 SOL as collateral")
    try:
        result = await lender.deposit_sol(kp, 0.01)
        print(f"  [+] Deposit: {result}")
    except Exception as e:
        print(f"  [X] Deposit failed: {e}")
        await lender.stop()
        return

    # Step 3: Borrow $0.50 USDC
    print("\nStep 3: Borrow $0.50 USDC")
    try:
        result = await lender.borrow_usdc(kp, 0.50)
        print(f"  [+] Borrow: {result}")
    except Exception as e:
        print(f"  [X] Borrow failed: {e}")
        print("  Attempting to repay and clean up...")

    # Step 4: Repay
    print("\nStep 4: Repay $0.50 USDC")
    try:
        result = await lender.repay_usdc(kp, 0.50)
        print(f"  [+] Repay: {result}")
    except Exception as e:
        print(f"  [X] Repay failed: {e}")

    print(f"\n{'='*60}")
    await lender.stop()


if __name__ == "__main__":
    asyncio.run(run())
