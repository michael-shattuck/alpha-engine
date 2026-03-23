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
from solana.rpc.types import TxOpts

from server.config import WALLET_PRIVATE_KEY, SOL_MINT, USDC_MINT

log = logging.getLogger("marginfi")

MARGINFI_PROGRAM = Pubkey.from_string("MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA")
MARGINFI_GROUP = Pubkey.from_string("4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8")
SOL_BANK = Pubkey.from_string("CCKtUs6Cgwo4aaQUmBPmyoApH2gUDErxNZCAntD6LYGh")
USDC_BANK = Pubkey.from_string("2s37akK2eyBbp8DZgCm7RtsaEz8eJP3Nxd4urLHQv7yB")
SOL_VAULT = Pubkey.from_string("2eicbpitfJXDwqCuFAmPgDP7t2oUotnAzbGzRKLMgSLe")
USDC_VAULT = Pubkey.from_string("7jaiZR5Sk8hdYN9MxTpczTcwbWpb5WEoxSANuUwveuat")
SOL_ORACLE = Pubkey.from_string("4Hmd6PdjVA9auCoScE12iaBogfwS4ZXQ6VZoBeqanwWW")
USDC_ORACLE = Pubkey.from_string("Dpw1EAVrSB1ibxiDQyTAW6Zip3J4Btk2x4SgApQCeFbX")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=92dc56e5-bd3e-402e-b30c-9c949008b793"

DISC = {
    "init_account": hashlib.sha256(b"global:marginfi_account_initialize").digest()[:8],
    "deposit": hashlib.sha256(b"global:lending_account_deposit").digest()[:8],
    "borrow": hashlib.sha256(b"global:lending_account_borrow").digest()[:8],
    "repay": hashlib.sha256(b"global:lending_account_repay").digest()[:8],
    "withdraw": hashlib.sha256(b"global:lending_account_withdraw").digest()[:8],
    "accrue": hashlib.sha256(b"global:lending_pool_accrue_bank_interest").digest()[:8],
}

MFI_ACCOUNT = Pubkey.from_string("C9qzJQLMw2CK8nYMiHVUcKRXjgeG6zsZdUQpPBZh6G6o")


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM
    )
    return pda


def _create_ata_ix(payer: Pubkey, owner: Pubkey, mint: Pubkey) -> Instruction:
    ata = _derive_ata(owner, mint)
    return Instruction(
        program_id=ATA_PROGRAM,
        accounts=[
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner, is_signer=False, is_writable=False),
            AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
        ],
        data=bytes(),
    )


class MarginFiLender:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.rpc: AsyncClient | None = None
        self.keypair: Keypair | None = None
        self.deposited_sol: float = 0.0
        self.borrowed_usdc: float = 0.0

    async def start(self):
        self.rpc = AsyncClient(HELIUS_RPC, commitment=Confirmed)
        if WALLET_PRIVATE_KEY:
            self.keypair = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))

    async def stop(self):
        if self.rpc:
            await self.rpc.close()

    def _accrue_ix(self, bank: Pubkey) -> Instruction:
        return Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=True),
                AccountMeta(pubkey=bank, is_signer=False, is_writable=True),
            ],
            data=DISC["accrue"],
        )

    def _health_remaining_accounts(self) -> list[AccountMeta]:
        return [
            AccountMeta(pubkey=SOL_BANK, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SOL_ORACLE, is_signer=False, is_writable=False),
            AccountMeta(pubkey=USDC_BANK, is_signer=False, is_writable=False),
            AccountMeta(pubkey=USDC_ORACLE, is_signer=False, is_writable=False),
        ]

    async def deposit_and_borrow(self, wallet: Keypair, sol_amount: float, usdc_borrow: float) -> dict:
        if self.paper_mode:
            self.deposited_sol += sol_amount
            self.borrowed_usdc += usdc_borrow
            return {"status": "simulated", "deposited": sol_amount, "borrowed": usdc_borrow}

        w = wallet.pubkey()
        sol_mint = Pubkey.from_string(SOL_MINT)
        usdc_mint = Pubkey.from_string(USDC_MINT)
        wsol_ata = _derive_ata(w, sol_mint)
        usdc_ata = _derive_ata(w, usdc_mint)
        lamports = int(sol_amount * 1e9)
        usdc_atoms = int(usdc_borrow * 1e6)
        vault_auth, _ = Pubkey.find_program_address([b"liquidity_vault_auth", bytes(USDC_BANK)], MARGINFI_PROGRAM)

        ixs = [
            set_compute_unit_limit(800_000),
            set_compute_unit_price(100_000),
        ]

        ata_resp = await self.rpc.get_account_info(wsol_ata)
        if not ata_resp.value:
            ixs.append(_create_ata_ix(w, w, sol_mint))

        ixs.append(Instruction(
            program_id=SYSTEM_PROGRAM,
            accounts=[
                AccountMeta(pubkey=w, is_signer=True, is_writable=True),
                AccountMeta(pubkey=wsol_ata, is_signer=False, is_writable=True),
            ],
            data=struct.pack("<IQ", 2, lamports),
        ))
        ixs.append(Instruction(
            program_id=TOKEN_PROGRAM,
            accounts=[AccountMeta(pubkey=wsol_ata, is_signer=False, is_writable=True)],
            data=bytes([17]),
        ))

        ixs.append(Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=MFI_ACCOUNT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=w, is_signer=True, is_writable=False),
                AccountMeta(pubkey=SOL_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=wsol_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=SOL_VAULT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=DISC["deposit"] + struct.pack("<Q", lamports) + b"\x00",
        ))

        ixs.append(self._accrue_ix(SOL_BANK))
        ixs.append(self._accrue_ix(USDC_BANK))

        ixs.append(Instruction(
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
            ] + self._health_remaining_accounts(),
            data=DISC["borrow"] + struct.pack("<Q", usdc_atoms),
        ))

        blockhash = (await self.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(
            payer=w, instructions=ixs,
            address_lookup_table_accounts=[], recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_raw_transaction(bytes(tx), opts=TxOpts(skip_preflight=True))
        sig = str(result)
        log.info(f"Deposit+borrow: deposit={sol_amount} SOL, borrow={usdc_borrow} USDC, tx={sig}")
        await self._confirm(sig)
        self.deposited_sol += sol_amount
        self.borrowed_usdc += usdc_borrow
        return {"status": "confirmed", "deposited": sol_amount, "borrowed": usdc_borrow, "signature": sig}

    async def repay_and_withdraw(self, wallet: Keypair) -> dict:
        if self.paper_mode:
            result = {"deposited": self.deposited_sol, "borrowed": self.borrowed_usdc}
            self.deposited_sol = 0
            self.borrowed_usdc = 0
            return {"status": "simulated", **result}

        w = wallet.pubkey()
        sol_mint = Pubkey.from_string(SOL_MINT)
        usdc_mint = Pubkey.from_string(USDC_MINT)
        wsol_ata = _derive_ata(w, sol_mint)
        usdc_ata = _derive_ata(w, usdc_mint)
        sol_vault_auth, _ = Pubkey.find_program_address([b"liquidity_vault_auth", bytes(SOL_BANK)], MARGINFI_PROGRAM)

        ixs = [
            set_compute_unit_limit(800_000),
            set_compute_unit_price(100_000),
            self._accrue_ix(SOL_BANK),
            self._accrue_ix(USDC_BANK),
        ]

        ixs.append(Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=False),
                AccountMeta(pubkey=MFI_ACCOUNT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=w, is_signer=True, is_writable=False),
                AccountMeta(pubkey=USDC_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=usdc_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=USDC_VAULT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=DISC["repay"] + struct.pack("<Q", 2**63 - 1) + b"\x01\x01",
        ))

        ata_resp = await self.rpc.get_account_info(wsol_ata)
        if not ata_resp.value:
            ixs.append(_create_ata_ix(w, w, sol_mint))

        ixs.append(Instruction(
            program_id=MARGINFI_PROGRAM,
            accounts=[
                AccountMeta(pubkey=MARGINFI_GROUP, is_signer=False, is_writable=True),
                AccountMeta(pubkey=MFI_ACCOUNT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=w, is_signer=True, is_writable=False),
                AccountMeta(pubkey=SOL_BANK, is_signer=False, is_writable=True),
                AccountMeta(pubkey=wsol_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=sol_vault_auth, is_signer=False, is_writable=False),
                AccountMeta(pubkey=SOL_VAULT, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ] + self._health_remaining_accounts(),
            data=DISC["withdraw"] + struct.pack("<Q", 2**63 - 1) + b"\x01\x01",
        ))

        ixs.append(Instruction(
            program_id=TOKEN_PROGRAM,
            accounts=[
                AccountMeta(pubkey=wsol_ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=w, is_signer=False, is_writable=True),
                AccountMeta(pubkey=w, is_signer=True, is_writable=False),
            ],
            data=bytes([9]),
        ))

        blockhash = (await self.rpc.get_latest_blockhash()).value.blockhash
        msg = MessageV0.try_compile(
            payer=w, instructions=ixs,
            address_lookup_table_accounts=[], recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_raw_transaction(bytes(tx), opts=TxOpts(skip_preflight=True))
        sig = str(result)
        log.info(f"Repay+withdraw: tx={sig}")
        await self._confirm(sig)
        self.deposited_sol = 0
        self.borrowed_usdc = 0
        return {"status": "confirmed", "signature": sig}

    def get_max_borrow(self, collateral_sol: float, sol_price: float, ltv: float = 0.65) -> float:
        return collateral_sol * sol_price * ltv

    async def _confirm(self, signature: str, max_retries: int = 30):
        sig_str = signature.replace("SendTransactionResp(Signature(", "").replace("))", "")
        sig = Signature.from_string(sig_str)
        for _ in range(max_retries):
            resp = await self.rpc.get_signature_statuses([sig])
            if resp.value and resp.value[0]:
                if resp.value[0].err:
                    raise Exception(f"Transaction failed: {resp.value[0].err}")
                if resp.value[0].confirmation_status:
                    return
            await asyncio.sleep(1)
        raise Exception(f"Not confirmed after {max_retries}s: {signature}")
