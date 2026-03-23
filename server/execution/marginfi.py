import asyncio
import base64
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
MARGINFI_GROUP = Pubkey.from_string("4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8")
SOL_BANK = Pubkey.from_string("CCKtUs6Cgwo4aaQUmBPmyoApH2gUDErxNZCAntD6LYGh")
USDC_BANK = Pubkey.from_string("2s37akK2eyBbp8DZgCm7RtsaEz8eJP3Nxd4urLHQv7yB")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

HELIUS_RPC = "https://johnath-nf0ci1-fast-mainnet.helius-rpc.com"

DISC = {
    "init_account": hashlib.sha256(b"global:marginfi_account_initialize").digest()[:8],
    "deposit": hashlib.sha256(b"global:lending_account_deposit").digest()[:8],
    "borrow": hashlib.sha256(b"global:lending_account_borrow").digest()[:8],
    "repay": hashlib.sha256(b"global:lending_account_repay").digest()[:8],
    "withdraw": hashlib.sha256(b"global:lending_account_withdraw").digest()[:8],
}

BANK_LIQUIDITY_VAULT_OFFSET = 112
BANK_ORACLE_SETUP_OFFSET = 617
BANK_ORACLE_KEYS_OFFSET = 618
MAX_ORACLE_KEYS = 5
MARGINFI_ACCOUNT_BALANCES_OFFSET = 72
BALANCE_SIZE = 104
MAX_BALANCES = 16
BALANCE_ACTIVE_OFFSET = 0
BALANCE_BANK_PK_OFFSET = 1
ZERO_PUBKEY = Pubkey.from_string("11111111111111111111111111111111")


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM
    )
    return pda


def _pack_deposit_args(amount: int) -> bytes:
    return DISC["deposit"] + struct.pack("<Q", amount) + b"\x00"


def _pack_borrow_args(amount: int) -> bytes:
    return DISC["borrow"] + struct.pack("<Q", amount)


def _pack_repay_args(amount: int, repay_all: bool = False) -> bytes:
    if repay_all:
        return DISC["repay"] + struct.pack("<Q", amount) + b"\x01\x01"
    return DISC["repay"] + struct.pack("<Q", amount) + b"\x00"


def _pack_withdraw_args(amount: int, withdraw_all: bool = False) -> bytes:
    if withdraw_all:
        return DISC["withdraw"] + struct.pack("<Q", amount) + b"\x01\x01"
    return DISC["withdraw"] + struct.pack("<Q", amount) + b"\x00"


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

        import httpx
        disc = hashlib.sha256(b"account:MarginfiAccount").digest()[:8]
        disc_b64 = base64.b64encode(disc).decode()
        owner_b58 = str(wallet.pubkey())

        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(HELIUS_RPC, json={
                "jsonrpc": "2.0", "id": 1, "method": "getProgramAccounts",
                "params": [str(MARGINFI_PROGRAM), {"encoding": "base64", "filters": [
                    {"memcmp": {"offset": 0, "bytes": disc_b64, "encoding": "base64"}},
                    {"memcmp": {"offset": 40, "bytes": owner_b58}},
                ], "dataSlice": {"offset": 0, "length": 8}}]
            })
            accounts = r.json().get("result", [])

        if accounts:
            self.marginfi_account = Pubkey.from_string(accounts[0]["pubkey"])
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
        ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

        bank_liquidity_vault = await self._get_bank_vault(SOL_BANK, BANK_LIQUIDITY_VAULT_OFFSET)

        pre_ixs = []
        ata_resp = await self.rpc.get_account_info(signer_ata)
        if not ata_resp.value:
            pre_ixs.append(Instruction(
                program_id=ATA_PROGRAM,
                accounts=[
                    AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                    AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=wallet.pubkey(), is_signer=False, is_writable=False),
                    AccountMeta(pubkey=sol_mint, is_signer=False, is_writable=False),
                    AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
                    AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
                ],
                data=bytes(),
            ))

        pre_ixs.append(Instruction(
            program_id=SYSTEM_PROGRAM,
            accounts=[
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
            ],
            data=struct.pack("<IQ", 2, lamports),
        ))
        pre_ixs.append(Instruction(
            program_id=TOKEN_PROGRAM,
            accounts=[AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True)],
            data=bytes([17]),
        ))

        deposit_ix = Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=SOL_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=bank_liquidity_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=_pack_deposit_args(lamports),
        )

        blockhash = (await self.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[set_compute_unit_limit(400_000), set_compute_unit_price(50_000)] + pre_ixs + [deposit_ix],
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

        bank_liquidity_vault = await self._get_bank_vault(USDC_BANK, BANK_LIQUIDITY_VAULT_OFFSET)
        bank_liquidity_vault_authority = await self._derive_bank_authority(USDC_BANK)

        health_accounts = await self._build_health_check_accounts(account)

        ix = Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=USDC_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=bank_liquidity_vault_authority, is_signer=False, is_writable=False),
                AccountMeta(pubkey=bank_liquidity_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ] + health_accounts,
            data=_pack_borrow_args(atoms),
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

        bank_liquidity_vault = await self._get_bank_vault(USDC_BANK, BANK_LIQUIDITY_VAULT_OFFSET)

        ix = Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=USDC_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=signer_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=bank_liquidity_vault, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=_pack_repay_args(atoms),
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

    async def _get_bank_oracle_keys(self, bank: Pubkey) -> list[Pubkey]:
        resp = await self.rpc.get_account_info(bank)
        if not resp.value:
            raise ValueError(f"Bank not found: {bank}")
        data = bytes(resp.value.data)
        oracle_keys = []
        for i in range(MAX_ORACLE_KEYS):
            key_start = BANK_ORACLE_KEYS_OFFSET + (i * 32)
            key = Pubkey.from_bytes(data[key_start:key_start + 32])
            if key != ZERO_PUBKEY:
                oracle_keys.append(key)
        return oracle_keys

    async def _build_health_check_accounts(self, marginfi_account: Pubkey) -> list[AccountMeta]:
        resp = await self.rpc.get_account_info(marginfi_account)
        if not resp.value:
            raise ValueError(f"MarginFi account not found: {marginfi_account}")
        data = bytes(resp.value.data)

        active_banks = set()
        for i in range(MAX_BALANCES):
            balance_start = MARGINFI_ACCOUNT_BALANCES_OFFSET + (i * BALANCE_SIZE)
            active = data[balance_start + BALANCE_ACTIVE_OFFSET]
            if active:
                bank_pk = Pubkey.from_bytes(
                    data[balance_start + BALANCE_BANK_PK_OFFSET:
                         balance_start + BALANCE_BANK_PK_OFFSET + 32]
                )
                active_banks.add(bank_pk)

        remaining_accounts = []
        for bank_pk in active_banks:
            remaining_accounts.append(
                AccountMeta(pubkey=bank_pk, is_signer=False, is_writable=False)
            )
            oracle_keys = await self._get_bank_oracle_keys(bank_pk)
            for oracle_key in oracle_keys:
                remaining_accounts.append(
                    AccountMeta(pubkey=oracle_key, is_signer=False, is_writable=False)
                )

        return remaining_accounts

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
