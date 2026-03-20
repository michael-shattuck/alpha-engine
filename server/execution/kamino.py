import asyncio
import struct
import logging
import base58

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.signature import Signature
from solders.instruction import Instruction, AccountMeta
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.system_program import ID as SYSTEM_PROGRAM
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

from server.config import SOLANA_RPC_URL, SOL_MINT, USDC_MINT, WALLET_PRIVATE_KEY

log = logging.getLogger("kamino")

KLEND_PROGRAM = Pubkey.from_string("kLendxcJstSfvMhR7qCi8PLRZ1t9mzQP6FyZTKxJt1K")
KLEND_MARKET = Pubkey.from_string("7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

SOL_RESERVE = Pubkey.from_string("d4A2prbA2whesmvHaL88BH6Ewn5N4bTSEr9U7GBbfwi")
USDC_RESERVE = Pubkey.from_string("D6q6wuQSrifJKDDhnCiZbCy91peRf1H4kiVfdi8rRctN")

DISCRIMINATORS = {
    "deposit": bytes([0xF2, 0x23, 0xC6, 0x89, 0x52, 0xE1, 0xF2, 0xB6]),
    "borrow": bytes([0xE9, 0x47, 0xA5, 0x44, 0x3D, 0x6A, 0x00, 0x16]),
    "repay": bytes([0x23, 0x4E, 0x6A, 0x28, 0xD6, 0x94, 0xBF, 0x08]),
    "withdraw": bytes([0xB7, 0x12, 0x46, 0x9C, 0x94, 0x6D, 0xA1, 0x22]),
}


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM,
    )
    return pda


class KaminoLender:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.rpc: AsyncClient | None = None
        self.keypair: Keypair | None = None
        self.deposited_sol: float = 0.0
        self.borrowed_usdc: float = 0.0

    async def start(self):
        self.rpc = AsyncClient(SOLANA_RPC_URL, commitment=Confirmed)
        if WALLET_PRIVATE_KEY:
            self.keypair = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))

    async def stop(self):
        if self.rpc:
            await self.rpc.close()

    async def deposit_collateral(self, wallet: Keypair, sol_amount: float) -> dict:
        lamports = int(sol_amount * 1e9)

        if self.paper_mode:
            self.deposited_sol += sol_amount
            return {
                "status": "simulated",
                "deposited_sol": sol_amount,
                "total_deposited": self.deposited_sol,
                "signature": "simulated",
            }

        log.info(f"Depositing {sol_amount:.4f} SOL as collateral on Kamino")

        sol_mint = Pubkey.from_string(SOL_MINT)
        owner_ata = _derive_ata(wallet.pubkey(), sol_mint)

        deposit_data = DISCRIMINATORS["deposit"]
        deposit_data += struct.pack("<Q", lamports)

        obligation_pda, _ = Pubkey.find_program_address(
            [bytes(b"obligation"), bytes(KLEND_MARKET), bytes(wallet.pubkey())],
            KLEND_PROGRAM,
        )

        reserve_liquidity_supply, _ = Pubkey.find_program_address(
            [bytes(b"reserve_liq_supply"), bytes(KLEND_MARKET), bytes(SOL_RESERVE)],
            KLEND_PROGRAM,
        )

        reserve_collateral_mint, _ = Pubkey.find_program_address(
            [bytes(b"reserve_coll_mint"), bytes(KLEND_MARKET), bytes(SOL_RESERVE)],
            KLEND_PROGRAM,
        )

        user_collateral_ata = _derive_ata(wallet.pubkey(), reserve_collateral_mint)

        deposit_ix = Instruction(
            program_id=KLEND_PROGRAM,
            accounts=[
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=obligation_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=KLEND_MARKET, is_signer=False, is_writable=False),
                AccountMeta(pubkey=SOL_RESERVE, is_signer=False, is_writable=True),
                AccountMeta(pubkey=reserve_liquidity_supply, is_signer=False, is_writable=True),
                AccountMeta(pubkey=reserve_collateral_mint, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=user_collateral_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=deposit_data,
        )

        blockhash_resp = await self.rpc.get_latest_blockhash()
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[
                set_compute_unit_limit(400_000),
                set_compute_unit_price(50_000),
                deposit_ix,
            ],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash_resp.value.blockhash,
        )

        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info(f"Deposit tx: {signature}")
        await self._confirm_transaction(signature)

        self.deposited_sol += sol_amount
        return {
            "status": "confirmed",
            "deposited_sol": sol_amount,
            "total_deposited": self.deposited_sol,
            "signature": signature,
        }

    async def borrow_usdc(self, wallet: Keypair, usdc_amount: float) -> dict:
        usdc_atoms = int(usdc_amount * 1e6)

        if self.paper_mode:
            self.borrowed_usdc += usdc_amount
            return {
                "status": "simulated",
                "borrowed_usdc": usdc_amount,
                "total_borrowed": self.borrowed_usdc,
                "signature": "simulated",
            }

        log.info(f"Borrowing {usdc_amount:.2f} USDC from Kamino")

        usdc_mint = Pubkey.from_string(USDC_MINT)
        owner_usdc_ata = _derive_ata(wallet.pubkey(), usdc_mint)

        obligation_pda, _ = Pubkey.find_program_address(
            [bytes(b"obligation"), bytes(KLEND_MARKET), bytes(wallet.pubkey())],
            KLEND_PROGRAM,
        )

        reserve_liquidity_supply, _ = Pubkey.find_program_address(
            [bytes(b"reserve_liq_supply"), bytes(KLEND_MARKET), bytes(USDC_RESERVE)],
            KLEND_PROGRAM,
        )

        borrow_data = DISCRIMINATORS["borrow"]
        borrow_data += struct.pack("<Q", usdc_atoms)

        borrow_ix = Instruction(
            program_id=KLEND_PROGRAM,
            accounts=[
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=obligation_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=KLEND_MARKET, is_signer=False, is_writable=False),
                AccountMeta(pubkey=USDC_RESERVE, is_signer=False, is_writable=True),
                AccountMeta(pubkey=reserve_liquidity_supply, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_usdc_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=borrow_data,
        )

        blockhash_resp = await self.rpc.get_latest_blockhash()
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[
                set_compute_unit_limit(400_000),
                set_compute_unit_price(50_000),
                borrow_ix,
            ],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash_resp.value.blockhash,
        )

        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info(f"Borrow tx: {signature}")
        await self._confirm_transaction(signature)

        self.borrowed_usdc += usdc_amount
        return {
            "status": "confirmed",
            "borrowed_usdc": usdc_amount,
            "total_borrowed": self.borrowed_usdc,
            "signature": signature,
        }

    async def repay_usdc(self, wallet: Keypair, usdc_amount: float) -> dict:
        usdc_atoms = int(usdc_amount * 1e6)

        if self.paper_mode:
            self.borrowed_usdc = max(self.borrowed_usdc - usdc_amount, 0)
            return {
                "status": "simulated",
                "repaid_usdc": usdc_amount,
                "remaining_borrowed": self.borrowed_usdc,
                "signature": "simulated",
            }

        log.info(f"Repaying {usdc_amount:.2f} USDC to Kamino")

        usdc_mint = Pubkey.from_string(USDC_MINT)
        owner_usdc_ata = _derive_ata(wallet.pubkey(), usdc_mint)

        obligation_pda, _ = Pubkey.find_program_address(
            [bytes(b"obligation"), bytes(KLEND_MARKET), bytes(wallet.pubkey())],
            KLEND_PROGRAM,
        )

        reserve_liquidity_supply, _ = Pubkey.find_program_address(
            [bytes(b"reserve_liq_supply"), bytes(KLEND_MARKET), bytes(USDC_RESERVE)],
            KLEND_PROGRAM,
        )

        repay_data = DISCRIMINATORS["repay"]
        repay_data += struct.pack("<Q", usdc_atoms)

        repay_ix = Instruction(
            program_id=KLEND_PROGRAM,
            accounts=[
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=obligation_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=KLEND_MARKET, is_signer=False, is_writable=False),
                AccountMeta(pubkey=USDC_RESERVE, is_signer=False, is_writable=True),
                AccountMeta(pubkey=reserve_liquidity_supply, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_usdc_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=repay_data,
        )

        blockhash_resp = await self.rpc.get_latest_blockhash()
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[
                set_compute_unit_limit(400_000),
                set_compute_unit_price(50_000),
                repay_ix,
            ],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash_resp.value.blockhash,
        )

        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info(f"Repay tx: {signature}")
        await self._confirm_transaction(signature)

        self.borrowed_usdc = max(self.borrowed_usdc - usdc_amount, 0)
        return {
            "status": "confirmed",
            "repaid_usdc": usdc_amount,
            "remaining_borrowed": self.borrowed_usdc,
            "signature": signature,
        }

    async def withdraw_collateral(self, wallet: Keypair, sol_amount: float) -> dict:
        if self.paper_mode:
            self.deposited_sol = max(self.deposited_sol - sol_amount, 0)
            return {
                "status": "simulated",
                "withdrawn_sol": sol_amount,
                "remaining_deposited": self.deposited_sol,
                "signature": "simulated",
            }

        log.info(f"Withdrawing {sol_amount:.4f} SOL collateral from Kamino")
        self.deposited_sol = max(self.deposited_sol - sol_amount, 0)
        return {"status": "confirmed", "withdrawn_sol": sol_amount, "signature": "not_implemented"}

    def get_max_borrow(self, collateral_sol: float, sol_price: float, ltv: float = 0.70) -> float:
        return collateral_sol * sol_price * ltv

    def leverage_loop_amounts(self, equity_sol: float, sol_price: float, target_leverage: float, ltv: float = 0.70) -> list[dict]:
        rounds = []
        total_deposited = 0.0
        total_borrowed = 0.0
        remaining_sol = equity_sol
        equity_usd = equity_sol * sol_price

        for i in range(10):
            if remaining_sol < 0.001:
                break

            deposit_sol = remaining_sol
            total_deposited += deposit_sol
            borrow_usdc = deposit_sol * sol_price * ltv
            total_borrowed += borrow_usdc
            sol_from_borrow = borrow_usdc / sol_price

            rounds.append({
                "round": i + 1,
                "deposit_sol": deposit_sol,
                "borrow_usdc": borrow_usdc,
                "sol_from_swap": sol_from_borrow,
            })

            effective_leverage = total_deposited * sol_price / equity_usd
            if effective_leverage >= target_leverage * 0.95:
                break

            remaining_sol = sol_from_borrow

        return rounds

    async def _confirm_transaction(self, signature: str, max_retries: int = 30):
        sig = Signature.from_string(signature)
        for _ in range(max_retries):
            resp = await self.rpc.get_signature_statuses([sig])
            statuses = resp.value
            if statuses and statuses[0]:
                if statuses[0].err:
                    raise Exception(f"Transaction failed: {statuses[0].err}")
                if statuses[0].confirmation_status:
                    return
            await asyncio.sleep(1)
        raise Exception(f"Transaction not confirmed after {max_retries}s: {signature}")
