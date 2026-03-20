import asyncio
import math
import struct
import base58
import base64
import logging

import httpx
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.signature import Signature
from solders.instruction import Instruction, AccountMeta
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.sysvar import RENT
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

from server.config import (
    SOLANA_RPC_URL,
    ORCA_WHIRLPOOL_SOL_USDC,
    ORCA_WHIRLPOOL_PROGRAM,
    WALLET_PRIVATE_KEY,
    SOL_MINT,
    USDC_MINT,
)

log = logging.getLogger(__name__)

WHIRLPOOL_PROGRAM_ID = Pubkey.from_string(ORCA_WHIRLPOOL_PROGRAM)
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
METADATA_PROGRAM = Pubkey.from_string("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s")

SOL_DECIMALS = 9
USDC_DECIMALS = 6
TICK_SPACING = 4

DISCRIMINATORS = {
    "open_position": bytes([0x87, 0x80, 0x2F, 0x4D, 0x0F, 0x98, 0xF0, 0x31]),
    "increase_liquidity": bytes([0x2E, 0x9C, 0xF3, 0x76, 0x0D, 0xC7, 0x6C, 0xA4]),
    "decrease_liquidity": bytes([0xA0, 0x26, 0xD0, 0x6F, 0x68, 0x5B, 0x2C, 0x01]),
    "collect_fees": bytes([0xA4, 0x98, 0xCF, 0x63, 0x1E, 0xBA, 0x13, 0x31]),
    "close_position": bytes([0x7B, 0x86, 0x51, 0x0C, 0xF8, 0x32, 0x75, 0x68]),
    "swap": bytes([0xF8, 0xC6, 0x9E, 0x91, 0xE1, 0x75, 0x87, 0xC8]),
}


def _derive_position_pda(position_mint: Pubkey) -> tuple[Pubkey, int]:
    return Pubkey.find_program_address(
        [b"position", bytes(position_mint)],
        WHIRLPOOL_PROGRAM_ID,
    )


def _derive_tick_array_pda(whirlpool: Pubkey, start_tick_index: int) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [b"tick_array", bytes(whirlpool), struct.pack("<i", start_tick_index)],
        WHIRLPOOL_PROGRAM_ID,
    )
    return pda


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    pda, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM,
    )
    return pda


def _align_tick(tick: int, spacing: int) -> int:
    if tick >= 0:
        return (tick // spacing) * spacing
    return ((tick - spacing + 1) // spacing) * spacing


def _tick_array_start(tick: int, spacing: int) -> int:
    ticks_per_array = spacing * 88
    return _align_tick(tick, ticks_per_array)


class OrcaExecutor:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.rpc: AsyncClient | None = None
        self.http: httpx.AsyncClient | None = None
        self.keypair: Keypair | None = None

    async def start(self):
        self.rpc = AsyncClient(SOLANA_RPC_URL, commitment=Confirmed)
        self.http = httpx.AsyncClient(timeout=30.0)
        if WALLET_PRIVATE_KEY:
            self.keypair = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))

    async def stop(self):
        if self.http:
            await self.http.aclose()
        if self.rpc:
            await self.rpc.close()

    def price_to_tick(self, price: float) -> int:
        adjusted = price / (10 ** (SOL_DECIMALS - USDC_DECIMALS))
        return int(math.log(adjusted) / math.log(1.0001))

    def tick_to_price(self, tick: int) -> float:
        return math.pow(1.0001, tick) * (10 ** (SOL_DECIMALS - USDC_DECIMALS))

    def _tick_to_sqrt_price_x64(self, tick: int) -> int:
        return int(math.pow(1.0001, tick / 2) * (2**64))

    async def fetch_whirlpool_state(self, pool_address: str | None = None) -> dict:
        address = Pubkey.from_string(pool_address or ORCA_WHIRLPOOL_SOL_USDC)
        resp = await self.rpc.get_account_info(address)
        if not resp.value:
            raise ValueError(f"Pool account not found: {address}")

        data = bytes(resp.value.data)
        offset = 8 + 32 + 1
        tick_spacing = struct.unpack_from("<H", data, offset)[0]
        offset += 2 + 2
        fee_rate = struct.unpack_from("<H", data, offset)[0]
        offset += 2 + 2
        liquidity = struct.unpack_from("<Q", data, offset)[0]
        offset += 16
        sqrt_price = struct.unpack_from("<Q", data, offset)[0]
        offset += 16
        tick_current = struct.unpack_from("<i", data, offset)[0]
        offset += 4 + 8 + 8
        token_mint_a = Pubkey.from_bytes(data[offset : offset + 32])
        offset += 32
        token_vault_a = Pubkey.from_bytes(data[offset : offset + 32])
        offset += 32 + 16
        token_mint_b = Pubkey.from_bytes(data[offset : offset + 32])
        offset += 32
        token_vault_b = Pubkey.from_bytes(data[offset : offset + 32])

        current_price = (sqrt_price / (2**64)) ** 2 * (10 ** (SOL_DECIMALS - USDC_DECIMALS))

        return {
            "sqrt_price": sqrt_price,
            "tick": tick_current,
            "liquidity": liquidity,
            "fee_rate": fee_rate,
            "tick_spacing": tick_spacing,
            "current_price": current_price,
            "token_mint_a": str(token_mint_a),
            "token_mint_b": str(token_mint_b),
            "token_vault_a": str(token_vault_a),
            "token_vault_b": str(token_vault_b),
        }

    def calculate_liquidity(
        self,
        amount_usd: float,
        current_price: float,
        lower_price: float,
        upper_price: float,
    ) -> tuple[float, float, int]:
        sqrt_p = math.sqrt(current_price)
        sqrt_l = math.sqrt(lower_price)
        sqrt_u = math.sqrt(upper_price)

        if current_price <= lower_price:
            sol_amount = amount_usd / current_price
            usdc_amount = 0.0
            liquidity = int(
                sol_amount
                * 1e9
                * self._tick_to_sqrt_price_x64(self.price_to_tick(upper_price))
                * self._tick_to_sqrt_price_x64(self.price_to_tick(lower_price))
                / (
                    (
                        self._tick_to_sqrt_price_x64(self.price_to_tick(upper_price))
                        - self._tick_to_sqrt_price_x64(self.price_to_tick(lower_price))
                    )
                    * (2**64)
                )
            )
        elif current_price >= upper_price:
            sol_amount = 0.0
            usdc_amount = amount_usd
            liquidity = int(
                usdc_amount
                * 1e6
                * (2**64)
                / (
                    self._tick_to_sqrt_price_x64(self.price_to_tick(upper_price))
                    - self._tick_to_sqrt_price_x64(self.price_to_tick(lower_price))
                )
            )
        else:
            ratio_sol = (sqrt_u - sqrt_p) / (sqrt_p * (sqrt_u - sqrt_l) / sqrt_l + (sqrt_u - sqrt_p))
            sol_value_usd = amount_usd * ratio_sol
            usdc_value_usd = amount_usd * (1 - ratio_sol)

            sol_amount = sol_value_usd / current_price
            usdc_amount = usdc_value_usd

            sol_lamports = int(sol_amount * 1e9)
            usdc_atoms = int(usdc_amount * 1e6)

            sqrt_price_current = self._tick_to_sqrt_price_x64(self.price_to_tick(current_price))
            sqrt_price_lower = self._tick_to_sqrt_price_x64(self.price_to_tick(lower_price))
            sqrt_price_upper = self._tick_to_sqrt_price_x64(self.price_to_tick(upper_price))

            liq_a = (
                sol_lamports * sqrt_price_current * sqrt_price_lower
            ) // ((sqrt_price_current - sqrt_price_lower) * (2**64))
            liq_b = (usdc_atoms * (2**64)) // (sqrt_price_upper - sqrt_price_current)
            liquidity = min(liq_a, liq_b)

        return sol_amount, usdc_amount, liquidity

    async def open_position(
        self,
        wallet: Keypair,
        pool: str,
        lower_tick: int,
        upper_tick: int,
        liquidity: int,
        sol_amount: float,
        usdc_amount: float,
    ) -> dict:
        pool_pubkey = Pubkey.from_string(pool)
        lower_tick = _align_tick(lower_tick, TICK_SPACING)
        upper_tick = _align_tick(upper_tick, TICK_SPACING)
        if upper_tick <= lower_tick:
            upper_tick = lower_tick + TICK_SPACING

        if self.paper_mode:
            return {
                "status": "simulated",
                "pool": pool,
                "lower_tick": lower_tick,
                "upper_tick": upper_tick,
                "lower_price": self.tick_to_price(lower_tick),
                "upper_price": self.tick_to_price(upper_tick),
                "liquidity": liquidity,
                "sol_amount": sol_amount,
                "usdc_amount": usdc_amount,
                "position_mint": "simulated_mint",
                "signature": "simulated_signature",
            }

        pool_state = await self.fetch_whirlpool_state(pool)

        position_mint_kp = Keypair()
        position_mint = position_mint_kp.pubkey()
        position_pda, position_bump = _derive_position_pda(position_mint)
        position_token_account = _derive_ata(wallet.pubkey(), position_mint)

        open_ix_data = DISCRIMINATORS["open_position"]
        open_ix_data += struct.pack("<B", position_bump)
        open_ix_data += struct.pack("<i", lower_tick)
        open_ix_data += struct.pack("<i", upper_tick)

        open_ix = Instruction(
            program_id=WHIRLPOOL_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=False, is_writable=False),
                AccountMeta(pubkey=position_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=position_mint, is_signer=True, is_writable=True),
                AccountMeta(pubkey=position_token_account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=False),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=RENT, is_signer=False, is_writable=False),
                AccountMeta(pubkey=ASSOCIATED_TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=open_ix_data,
        )

        token_vault_a = Pubkey.from_string(pool_state["token_vault_a"])
        token_vault_b = Pubkey.from_string(pool_state["token_vault_b"])
        owner_ata_a = _derive_ata(wallet.pubkey(), Pubkey.from_string(SOL_MINT))
        owner_ata_b = _derive_ata(wallet.pubkey(), Pubkey.from_string(USDC_MINT))

        tick_array_lower = _derive_tick_array_pda(
            pool_pubkey, _tick_array_start(lower_tick, TICK_SPACING)
        )
        tick_array_upper = _derive_tick_array_pda(
            pool_pubkey, _tick_array_start(upper_tick, TICK_SPACING)
        )

        sol_lamports = int(sol_amount * 1e9)
        usdc_atoms = int(usdc_amount * 1e6)
        slippage = 1.02
        token_max_a = int(sol_lamports * slippage)
        token_max_b = int(usdc_atoms * slippage)

        increase_data = DISCRIMINATORS["increase_liquidity"]
        increase_data += struct.pack("<Q", liquidity)
        increase_data += bytes(8)
        increase_data += struct.pack("<Q", token_max_a)
        increase_data += struct.pack("<Q", token_max_b)

        increase_ix = Instruction(
            program_id=WHIRLPOOL_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=position_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=position_token_account, is_signer=False, is_writable=False),
                AccountMeta(pubkey=owner_ata_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_ata_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=tick_array_lower, is_signer=False, is_writable=True),
                AccountMeta(pubkey=tick_array_upper, is_signer=False, is_writable=True),
            ],
            data=increase_data,
        )

        blockhash_resp = await self.rpc.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[
                set_compute_unit_limit(400_000),
                set_compute_unit_price(50_000),
                open_ix,
                increase_ix,
            ],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )

        tx = VersionedTransaction(msg, [wallet, position_mint_kp])
        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info("open_position tx: %s", signature)
        await self._confirm_transaction(signature)

        return {
            "status": "confirmed",
            "pool": pool,
            "lower_tick": lower_tick,
            "upper_tick": upper_tick,
            "lower_price": self.tick_to_price(lower_tick),
            "upper_price": self.tick_to_price(upper_tick),
            "liquidity": liquidity,
            "sol_amount": sol_amount,
            "usdc_amount": usdc_amount,
            "position_mint": str(position_mint),
            "signature": signature,
        }

    async def close_position(self, wallet: Keypair, position_mint: str) -> dict:
        mint_pubkey = Pubkey.from_string(position_mint)

        if self.paper_mode:
            return {
                "status": "simulated",
                "position_mint": position_mint,
                "signature": "simulated_signature",
            }

        position_pda, _ = _derive_position_pda(mint_pubkey)
        position_token_account = _derive_ata(wallet.pubkey(), mint_pubkey)

        pool_state = await self.fetch_whirlpool_state()
        pool_pubkey = Pubkey.from_string(ORCA_WHIRLPOOL_SOL_USDC)
        token_vault_a = Pubkey.from_string(pool_state["token_vault_a"])
        token_vault_b = Pubkey.from_string(pool_state["token_vault_b"])
        owner_ata_a = _derive_ata(wallet.pubkey(), Pubkey.from_string(SOL_MINT))
        owner_ata_b = _derive_ata(wallet.pubkey(), Pubkey.from_string(USDC_MINT))

        position_data = await self._fetch_position_data(mint_pubkey)

        tick_array_lower = _derive_tick_array_pda(
            pool_pubkey, _tick_array_start(position_data["tick_lower"], TICK_SPACING)
        )
        tick_array_upper = _derive_tick_array_pda(
            pool_pubkey, _tick_array_start(position_data["tick_upper"], TICK_SPACING)
        )

        decrease_data = DISCRIMINATORS["decrease_liquidity"]
        decrease_data += struct.pack("<Q", position_data["liquidity"])
        decrease_data += bytes(8)
        decrease_data += struct.pack("<Q", 0)
        decrease_data += struct.pack("<Q", 0)

        decrease_ix = Instruction(
            program_id=WHIRLPOOL_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=position_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=position_token_account, is_signer=False, is_writable=False),
                AccountMeta(pubkey=owner_ata_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_ata_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=tick_array_lower, is_signer=False, is_writable=True),
                AccountMeta(pubkey=tick_array_upper, is_signer=False, is_writable=True),
            ],
            data=decrease_data,
        )

        collect_ix = Instruction(
            program_id=WHIRLPOOL_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=position_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=position_token_account, is_signer=False, is_writable=False),
                AccountMeta(pubkey=owner_ata_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_ata_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=DISCRIMINATORS["collect_fees"],
        )

        close_ix = Instruction(
            program_id=WHIRLPOOL_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=False, is_writable=True),
                AccountMeta(pubkey=position_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=mint_pubkey, is_signer=False, is_writable=True),
                AccountMeta(pubkey=position_token_account, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=DISCRIMINATORS["close_position"],
        )

        blockhash_resp = await self.rpc.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[
                set_compute_unit_limit(400_000),
                set_compute_unit_price(50_000),
                decrease_ix,
                collect_ix,
                close_ix,
            ],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )

        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info("close_position tx: %s", signature)
        await self._confirm_transaction(signature)

        return {
            "status": "confirmed",
            "position_mint": position_mint,
            "signature": signature,
        }

    async def collect_fees(self, wallet: Keypair, position_mint: str) -> dict:
        mint_pubkey = Pubkey.from_string(position_mint)

        if self.paper_mode:
            return {
                "status": "simulated",
                "position_mint": position_mint,
                "signature": "simulated_signature",
            }

        position_pda, _ = _derive_position_pda(mint_pubkey)
        position_token_account = _derive_ata(wallet.pubkey(), mint_pubkey)

        pool_state = await self.fetch_whirlpool_state()
        pool_pubkey = Pubkey.from_string(ORCA_WHIRLPOOL_SOL_USDC)
        token_vault_a = Pubkey.from_string(pool_state["token_vault_a"])
        token_vault_b = Pubkey.from_string(pool_state["token_vault_b"])
        owner_ata_a = _derive_ata(wallet.pubkey(), Pubkey.from_string(SOL_MINT))
        owner_ata_b = _derive_ata(wallet.pubkey(), Pubkey.from_string(USDC_MINT))

        collect_ix = Instruction(
            program_id=WHIRLPOOL_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=position_pda, is_signer=False, is_writable=True),
                AccountMeta(pubkey=position_token_account, is_signer=False, is_writable=False),
                AccountMeta(pubkey=owner_ata_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_ata_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            ],
            data=DISCRIMINATORS["collect_fees"],
        )

        blockhash_resp = await self.rpc.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[
                set_compute_unit_limit(200_000),
                set_compute_unit_price(50_000),
                collect_ix,
            ],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )

        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info("collect_fees tx: %s", signature)

        return {
            "status": "submitted",
            "position_mint": position_mint,
            "signature": signature,
        }

    async def swap(
        self,
        wallet: Keypair,
        pool: str,
        amount: int,
        a_to_b: bool,
        slippage_pct: float = 0.01,
    ) -> dict:
        pool_pubkey = Pubkey.from_string(pool)

        if self.paper_mode:
            pool_state = await self.fetch_whirlpool_state(pool)
            price = pool_state["current_price"]
            if a_to_b:
                out_amount = int(amount * price / (10 ** (SOL_DECIMALS - USDC_DECIMALS)))
            else:
                out_amount = int(amount * (10 ** (SOL_DECIMALS - USDC_DECIMALS)) / price)
            return {
                "status": "simulated",
                "in_amount": amount,
                "out_amount": out_amount,
                "a_to_b": a_to_b,
                "signature": "simulated_signature",
            }

        pool_state = await self.fetch_whirlpool_state(pool)
        sqrt_price_limit = (
            4295048016 if a_to_b else 79226673515401279992447579055
        )

        token_vault_a = Pubkey.from_string(pool_state["token_vault_a"])
        token_vault_b = Pubkey.from_string(pool_state["token_vault_b"])
        owner_ata_a = _derive_ata(wallet.pubkey(), Pubkey.from_string(SOL_MINT))
        owner_ata_b = _derive_ata(wallet.pubkey(), Pubkey.from_string(USDC_MINT))

        tick_current = pool_state["tick"]
        ta0 = _derive_tick_array_pda(pool_pubkey, _tick_array_start(tick_current, TICK_SPACING))
        offset = TICK_SPACING * 88
        if a_to_b:
            ta1 = _derive_tick_array_pda(pool_pubkey, _tick_array_start(tick_current - offset, TICK_SPACING))
            ta2 = _derive_tick_array_pda(pool_pubkey, _tick_array_start(tick_current - 2 * offset, TICK_SPACING))
        else:
            ta1 = _derive_tick_array_pda(pool_pubkey, _tick_array_start(tick_current + offset, TICK_SPACING))
            ta2 = _derive_tick_array_pda(pool_pubkey, _tick_array_start(tick_current + 2 * offset, TICK_SPACING))

        oracle_pda, _ = Pubkey.find_program_address(
            [b"oracle", bytes(pool_pubkey)],
            WHIRLPOOL_PROGRAM_ID,
        )

        swap_data = DISCRIMINATORS["swap"]
        swap_data += struct.pack("<Q", amount)
        swap_data += struct.pack("<Q", 0)
        swap_data += struct.pack("<Q", sqrt_price_limit & 0xFFFFFFFFFFFFFFFF)
        swap_data += struct.pack("<Q", sqrt_price_limit >> 64)
        swap_data += struct.pack("<?", True)
        swap_data += struct.pack("<?", a_to_b)

        swap_ix = Instruction(
            program_id=WHIRLPOOL_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
                AccountMeta(pubkey=wallet.pubkey(), is_signer=True, is_writable=False),
                AccountMeta(pubkey=pool_pubkey, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_ata_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_a, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_ata_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=token_vault_b, is_signer=False, is_writable=True),
                AccountMeta(pubkey=ta0, is_signer=False, is_writable=True),
                AccountMeta(pubkey=ta1, is_signer=False, is_writable=True),
                AccountMeta(pubkey=ta2, is_signer=False, is_writable=True),
                AccountMeta(pubkey=oracle_pda, is_signer=False, is_writable=False),
            ],
            data=swap_data,
        )

        blockhash_resp = await self.rpc.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = MessageV0.try_compile(
            payer=wallet.pubkey(),
            instructions=[
                set_compute_unit_limit(300_000),
                set_compute_unit_price(50_000),
                swap_ix,
            ],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )

        tx = VersionedTransaction(msg, [wallet])
        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info("swap tx: %s (a_to_b=%s, amount=%d)", signature, a_to_b, amount)
        await self._confirm_transaction(signature)

        return {
            "status": "confirmed",
            "in_amount": amount,
            "a_to_b": a_to_b,
            "signature": signature,
        }

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

    async def _fetch_position_data(self, position_mint: Pubkey) -> dict:
        position_pda, _ = _derive_position_pda(position_mint)
        resp = await self.rpc.get_account_info(position_pda)
        if not resp.value:
            raise ValueError(f"Position not found: {position_pda}")

        data = bytes(resp.value.data)
        offset = 8
        whirlpool = Pubkey.from_bytes(data[offset : offset + 32])
        offset += 32
        _position_mint = Pubkey.from_bytes(data[offset : offset + 32])
        offset += 32
        liquidity = struct.unpack_from("<Q", data, offset)[0]
        offset += 16
        tick_lower = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        tick_upper = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        offset += 16 + 16
        fee_owed_a = struct.unpack_from("<Q", data, offset)[0]
        offset += 8
        fee_owed_b = struct.unpack_from("<Q", data, offset)[0]

        return {
            "whirlpool": str(whirlpool),
            "position_mint": str(_position_mint),
            "liquidity": liquidity,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "fee_owed_a": fee_owed_a,
            "fee_owed_b": fee_owed_b,
        }
