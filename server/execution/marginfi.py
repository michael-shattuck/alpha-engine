import asyncio
import struct
import logging
import base58
import hashlib

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

from server.config import WALLET_PRIVATE_KEY, SOL_MINT, USDC_MINT

log = logging.getLogger("marginfi")

MARGINFI_PROGRAM = Pubkey.from_string("MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA")
MARGINFI_GROUP = Pubkey.from_string("9vaJ6UV24qsqjobwmjRGt5mCzmAcE4SrbXSD5CsarhtT")
SOL_BANK = Pubkey.from_string("4uawSqEM2jDPKkQRtnoSTmBjFJ51Ehu79EvGfu3R45o7")
USDC_BANK = Pubkey.from_string("2s37akK2eyBbp8DZgCm7RtsaEz8eJP3Nxd4urLHQv7yB")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

HELIUS_RPC = "https://johnath-nf0ci1-fast-mainnet.helius-rpc.com"

DISC = {
    "init_account": bytes([0x2B, 0x4E, 0x3D, 0xFF, 0x94, 0x34, 0xF9, 0x9A]),
    "deposit": bytes([0xAB, 0x5E, 0xEB, 0x67, 0x52, 0x40, 0xD4, 0x8C]),
    "borrow": bytes([0x04, 0x7E, 0x74, 0x35, 0x30, 0x05, 0xD4, 0x1F]),
    "repay": bytes([0x4F, 0xD1, 0xAC, 0xB1, 0xDE, 0x33, 0xAD, 0x97]),
    "withdraw": bytes([0x24, 0x48, 0x4A, 0x13, 0xD2, 0xD2, 0xC0, 0xC0]),
}


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM
    )
    return pda


class MarginFiLender:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.rpc: AsyncClient | None = None
        self.keypair: Keypair | None = None
        self.marginfi_account: Pubkey | None = None
        self.deposited_sol: float = 0.0
        self.borrowed_usdc: float = 0.0

    async def start(self):
        self.rpc = AsyncClient(HELIUS_RPC, commitment=Confirmed)
        if WALLET_PRIVATE_KEY:
            self.keypair = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))

    async def stop(self):
        if self.rpc:
            await self.rpc.close()

    async def _find_or_create_marginfi_account(self, wallet: Keypair) -> Pubkey:
        if self.marginfi_account:
            return self.marginfi_account

        disc = hashlib.sha256(b"account:MarginfiAccount").digest()[:8]

        resp = await self.rpc.get_program_accounts(
            MARGINFI_PROGRAM,
            encoding="base64",
            filters=[
                {"memcmp": {"offset": 0, "bytes": base58.b58encode(disc).decode()}},
                {"memcmp": {"offset": 40, "bytes": str(wallet.pubkey())}},
            ],
            data_slice={"offset": 0, "length": 8},
        )

        if resp.value:
            self.marginfi_account = resp.value[0].pubkey
            log.info(f"Found existing MarginFi account: {self.marginfi_account}")
            return self.marginfi_account

        account_kp = Keypair()
        self.marginfi_account = account_kp.pubkey()

        ix_data = DISC["init_account"]
        ix = Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=account_kp.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=ix_data,
        )

        blockhash = (await self.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[set_compute_unit_limit(200_000), set_compute_unit_price(50_000), ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(msg, [wallet, account_kp])
        result = await self.rpc.send_transaction(tx)
        sig = str(result.value)
        log.info(f"Created MarginFi account: {self.marginfi_account} sig={sig}")
        await self._confirm(sig)
        return self.marginfi_account

    async def deposit_sol(self, wallet: Keypair, sol_amount: float) -> dict:
        lamports = int(sol_amount * 1e9)

        if self.paper_mode:
            self.deposited_sol += sol_amount
            return {"status": "simulated", "deposited": sol_amount}

        account = await self._find_or_create_marginfi_account(wallet)
        sol_mint = Pubkey.from_string(SOL_MINT)
        signer_ata = _derive_ata(wallet.pubkey(), sol_mint)

        bank_liquidity_vault = await self._get_bank_vault(SOL_BANK, 112)

        ix_data = DISC["deposit"] + struct.pack("<Q", lamports)
        ix = Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=SOL_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=bank_liquidity_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=ix_data,
        )

        blockhash = (await self.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[set_compute_unit_limit(300_000), set_compute_unit_price(50_000), ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        sig = str(result.value)
        log.info(f"Deposit SOL: {sol_amount} sig={sig}")
        await self._confirm(sig)
        self.deposited_sol += sol_amount
        return {"status": "confirmed", "deposited": sol_amount, "signature": sig}

    async def borrow_usdc(self, wallet: Keypair, usdc_amount: float) -> dict:
        atoms = int(usdc_amount * 1e6)

        if self.paper_mode:
            self.borrowed_usdc += usdc_amount
            return {"status": "simulated", "borrowed": usdc_amount}

        account = await self._find_or_create_marginfi_account(wallet)
        usdc_mint = Pubkey.from_string(USDC_MINT)
        signer_ata = _derive_ata(wallet.pubkey(), usdc_mint)

        bank_liquidity_vault = await self._get_bank_vault(USDC_BANK, 112)
        bank_liquidity_vault_authority = await self._derive_bank_authority(USDC_BANK)

        ix_data = DISC["borrow"] + struct.pack("<Q", atoms)
        ix = Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=USDC_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=bank_liquidity_vault_authority, is_signer=False, is_writable=False),
                AccountMeta(pubkey=bank_liquidity_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=ix_data,
        )

        blockhash = (await self.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[set_compute_unit_limit(300_000), set_compute_unit_price(50_000), ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        sig = str(result.value)
        log.info(f"Borrow USDC: {usdc_amount} sig={sig}")
        await self._confirm(sig)
        self.borrowed_usdc += usdc_amount
        return {"status": "confirmed", "borrowed": usdc_amount, "signature": sig}

    async def repay_usdc(self, wallet: Keypair, usdc_amount: float) -> dict:
        atoms = int(usdc_amount * 1e6)

        if self.paper_mode:
            self.borrowed_usdc = max(self.borrowed_usdc - usdc_amount, 0)
            return {"status": "simulated", "repaid": usdc_amount}

        account = await self._find_or_create_marginfi_account(wallet)
        usdc_mint = Pubkey.from_string(USDC_MINT)
        signer_ata = _derive_ata(wallet.pubkey(), usdc_mint)

        bank_liquidity_vault = await self._get_bank_vault(USDC_BANK, 112)

        ix_data = DISC["repay"] + struct.pack("<Q", atoms)
        ix = Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=USDC_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=bank_liquidity_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=ix_data,
        )

        blockhash = (await self.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[set_compute_unit_limit(300_000), set_compute_unit_price(50_000), ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        sig = str(result.value)
        log.info(f"Repay USDC: {usdc_amount} sig={sig}")
        await self._confirm(sig)
        self.borrowed_usdc = max(self.borrowed_usdc - usdc_amount, 0)
        return {"status": "confirmed", "repaid": usdc_amount, "signature": sig}

    async def _get_bank_vault(self, bank: Pubkey, offset: int) -> Pubkey:
        resp = await self.rpc.get_account_info(bank)
        if not resp.value:
            raise ValueError(f"Bank not found: {bank}")
        data = bytes(resp.value.data)
        return Pubkey.from_bytes(data[offset:offset + 32])

    async def _derive_bank_authority(self, bank: Pubkey) -> Pubkey:
        pda, _ = Pubkey.find_program_address(
            [b"liquidity_vault_auth", bytes(bank)],
            MARGINFI_PROGRAM,
        )
        return pda

    def get_max_borrow(self, collateral_sol: float, sol_price: float, ltv: float = 0.65) -> float:
        return collateral_sol * sol_price * ltv

    async def _confirm(self, signature: str, max_retries: int = 30):
        sig = Signature.from_string(signature)
        for _ in range(max_retries):
            resp = await self.rpc.get_signature_statuses([sig])
            if resp.value and resp.value[0]:
                if resp.value[0].err:
                    raise Exception(f"Transaction failed: {resp.value[0].err}")
                if resp.value[0].confirmation_status:
                    return
            await asyncio.sleep(1)
        raise Exception(f"Not confirmed after {max_retries}s: {signature}")
