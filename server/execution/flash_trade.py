import time
import logging
import asyncio
import struct
import json
from typing import Optional
from pathlib import Path

import httpx
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.address_lookup_table_account import AddressLookupTableAccount
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

log = logging.getLogger("flash_trade")

FLASH_PROGRAM = Pubkey.from_string("FLASH6Lo6h3iasJKWDs2F8TkW2UKf3s15C8PMGuVfgBn")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
IX_SYSVAR = Pubkey.from_string("Sysvar1nstructions1111111111111111111111111")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

OPEN_DISC = bytes([135, 128, 47, 77, 15, 152, 240, 49])
CLOSE_DISC = bytes([123, 134, 81, 0, 49, 68, 98, 98])

TRANSFER_AUTHORITY_PDA, _ = Pubkey.find_program_address([b"transfer_authority"], FLASH_PROGRAM)
PERPETUALS_PDA, _ = Pubkey.find_program_address([b"perpetuals"], FLASH_PROGRAM)
EVENT_AUTH_PDA, _ = Pubkey.find_program_address([b"__event_authority"], FLASH_PROGRAM)

PYTH_FEED_IDS = {
    "SOL": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "JUP": "0x0a0408d619e9380abad35060f9192039ed5042fa6f82301d0e48bb52be830996",
    "JTO": "0xb43660a5f790c69354b0729a5ef9d50d68f1df92107540210b9cccba1f947cc2",
    "PYTH": "0x0bbf28e9a841a1cc788f6a361b17ca072d0ea3098a1e5df1c3922d06719579ff",
    "RAY": "0x91568baa8beb53db23eb3fb7f22c6e8bd303d103919e19733f2bb642d3e7987a",
    "WIF": "0x4ca4beeca86f0d164160323817a4e42b10010a724c2217c6ee41b54cd4cc61fc",
    "BONK": "0x72b021217ca3fe68922a19aaf990109cb9d84e9ad004b4d2025ad6f529314419",
    "PENGU": "0xbed3097008b9b5e3c93bec20be79cb43986b85a996475589351a21e67bae9b61",
    "FARTCOIN": "0x58cd29ef0e714c5affc44f269b2c1899a52da4169d7acc147b9da692e6953608",
    "BNB": "0x2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f",
    "KMNO": "0x7d669ddcdd23d9ef1fa9a9cc022ba055ec900e91c4cb960f3c20429d4447a411",
    "PUMP": "0xbaf57c37c42ab0798e5580a7e4d41e62c3688cbb7eea3ebafa459b40c3c09038",
    "HYPE": "0x3fa05e094c9cc9e2bc1386c72e408d6397b47e84b0c1e1db2804afd17ea037b0",
}

POOL_CONFIG = None


def _load_pool_config():
    global POOL_CONFIG
    if POOL_CONFIG:
        return POOL_CONFIG

    config_path = Path(__file__).parent / "flash_pool_config.json"
    if config_path.exists():
        with open(config_path) as f:
            POOL_CONFIG = json.load(f)
        return POOL_CONFIG
    return None


def _get_market_info(symbol: str, side: str) -> Optional[dict]:
    cfg = _load_pool_config()
    if not cfg:
        return None

    for pool in cfg.get("pools", []):
        custodies = {c["symbol"]: c for c in pool.get("custodies", [])}
        for m in pool.get("markets", []):
            if m["side"] != side:
                continue
            target_addr = m.get("targetCustody", "")
            for sym, c in custodies.items():
                if c["custodyAccount"] == target_addr and sym == symbol:
                    collateral_addr = m.get("collateralCustody", "")
                    collateral_cust = None
                    target_cust = c
                    for cs, cc in custodies.items():
                        if cc["custodyAccount"] == collateral_addr:
                            collateral_cust = cc
                    if not collateral_cust:
                        continue
                    return {
                        "pool": pool["poolAddress"],
                        "market": m["marketAccount"],
                        "target_custody": target_addr,
                        "collateral_custody": collateral_addr,
                        "target_symbol": sym,
                        "target_oracle": target_cust.get("intOracleAddress", target_cust.get("oracleAddress", "")),
                        "target_mint": target_cust.get("mintKey", ""),
                        "target_token_account": target_cust.get("tokenAccount", ""),
                        "collateral_oracle": collateral_cust.get("intOracleAddress", collateral_cust.get("oracleAddress", "")),
                        "collateral_mint": collateral_cust.get("mintKey", ""),
                        "collateral_token_account": collateral_cust.get("tokenAccount", ""),
                        "alt": pool.get("addressLookupTable", ""),
                    }
    return None


def _get_all_symbols():
    cfg = _load_pool_config()
    if not cfg:
        return set()
    symbols = set()
    for pool in cfg.get("pools", []):
        custodies = {c["symbol"]: c for c in pool.get("custodies", [])}
        for m in pool.get("markets", []):
            target_addr = m.get("targetCustody", "")
            for sym, c in custodies.items():
                if c["custodyAccount"] == target_addr and sym != "USDC":
                    symbols.add(sym)
    return symbols


def _serialize_open_params(price: int, exponent: int, collateral_amount: int, size_amount: int) -> bytes:
    data = OPEN_DISC
    data += struct.pack("<Q", price)
    data += struct.pack("<i", exponent)
    data += struct.pack("<Q", collateral_amount)
    data += struct.pack("<Q", size_amount)
    data += bytes([0])
    return data


def _serialize_close_params(price: int, exponent: int) -> bytes:
    data = CLOSE_DISC
    data += struct.pack("<Q", price)
    data += struct.pack("<i", exponent)
    data += bytes([0])
    return data


class FlashTradeExecutor:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self._started = False
        self._paper_positions: dict[str, dict] = {}
        self._oracle_prices: dict[str, float] = {}
        self._last_price_fetch: float = 0
        self._conn: AsyncClient | None = None
        self._keypair: Keypair | None = None
        self.client = None
        self._available_markets: set[str] = set()

    async def start(self):
        if self._started:
            return

        _load_pool_config()
        self._available_markets = _get_all_symbols()

        if not self.paper_mode:
            from server.config import WALLET_PRIVATE_KEY, HELIUS_RPC_URL
            self._keypair = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))
            self._conn = AsyncClient(HELIUS_RPC_URL, commitment=Confirmed)

        await self._fetch_prices()
        self._started = True
        mode = "paper" if self.paper_mode else "live"
        log.info(f"Flash Trade executor started ({mode}, {len(self._available_markets)} markets)")

    async def stop(self):
        if self._conn:
            await self._conn.close()
        self._started = False

    async def _fetch_prices(self):
        now = time.time()
        if now - self._last_price_fetch < 5:
            return
        try:
            feed_ids = {sym: fid for sym, fid in PYTH_FEED_IDS.items() if sym in self._available_markets}
            params = [("ids[]", fid) for fid in feed_ids.values()]
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get("http://20.120.229.168:4160/v2/updates/price/latest", params=params)
                if r.status_code == 200:
                    data = r.json()
                    fid_to_symbol = {fid.removeprefix("0x"): sym for sym, fid in feed_ids.items()}
                    for entry in data.get("parsed", []):
                        fid = entry.get("id", "")
                        symbol = fid_to_symbol.get(fid)
                        if symbol:
                            pd = entry.get("price", {})
                            price = int(pd.get("price", 0)) * (10 ** int(pd.get("expo", 0)))
                            if price > 0:
                                self._oracle_prices[symbol] = price
            self._last_price_fetch = now
        except Exception as e:
            log.warning(f"Flash price fetch failed: {e}")

    def get_oracle_price(self, market: str) -> float:
        return self._oracle_prices.get(market.upper(), 0.0)

    def get_oracle_prices(self) -> dict[str, float]:
        return dict(self._oracle_prices)

    def get_available_markets(self) -> list[str]:
        return list(self._available_markets)

    async def open_perp_position(self, market: str, direction: str, size_usd: float, leverage: float) -> dict:
        market = market.upper()
        if market not in self._available_markets:
            return {"status": "error", "market": market, "error": f"{market} not on Flash Trade"}

        await self._fetch_prices()
        oracle_price = self._oracle_prices.get(market, 0)

        if self.paper_mode:
            if oracle_price <= 0:
                return {"status": "error", "market": market, "error": "no oracle price"}
            self._paper_positions[market] = {
                "market": market,
                "direction": direction,
                "size_usd": size_usd,
                "leverage": leverage,
                "entry_price": oracle_price,
                "collateral_usd": size_usd / leverage,
                "opened_at": time.time(),
            }
            log.info(f"Flash paper {direction} {market}: ${size_usd:.2f} at {leverage}x, entry=${oracle_price:.6f}")
            return {"status": "simulated", "market": market, "direction": direction, "oracle_price": oracle_price}

        return await self._execute_live_open(market, direction, size_usd, leverage, oracle_price)

    async def _execute_live_open(self, market, direction, size_usd, leverage, oracle_price):
        if not self._conn or not self._keypair:
            return {"status": "error", "market": market, "error": "not initialized for live"}

        side = "long" if direction == "long" else "short"
        info = _get_market_info(market, side)
        if not info:
            return {"status": "error", "market": market, "error": f"no market config for {market} {side}"}

        collateral_usd = size_usd / leverage
        oracle_exp = -8
        oracle_int = int(oracle_price * (10 ** abs(oracle_exp)))

        slippage = 0.02
        if direction == "long":
            slipped_price = int(oracle_int * (1 + slippage))
        else:
            slipped_price = int(oracle_int * (1 - slippage))

        collateral_amount = int(collateral_usd * 1e6)
        size_amount = int(size_usd * 1e6)

        ix_data = _serialize_open_params(slipped_price, oracle_exp, collateral_amount, size_amount)

        wallet = self._keypair.pubkey()
        pool_pk = Pubkey.from_string(info["pool"])
        market_pk = Pubkey.from_string(info["market"])
        target_custody = Pubkey.from_string(info["target_custody"])
        collateral_custody = Pubkey.from_string(info["collateral_custody"])
        target_oracle = Pubkey.from_string(info["target_oracle"])
        collateral_oracle = Pubkey.from_string(info["collateral_oracle"])
        collateral_mint = Pubkey.from_string(info["collateral_mint"])
        collateral_token_acct = Pubkey.from_string(info["collateral_token_account"])

        position_pda, _ = Pubkey.find_program_address(
            [b"position", bytes(wallet), bytes(market_pk)], FLASH_PROGRAM
        )

        funding_ata = Pubkey.find_program_address(
            [bytes(wallet), bytes(TOKEN_PROGRAM), bytes(collateral_mint)], ATA_PROGRAM
        )[0]

        accounts = [
            AccountMeta(wallet, is_signer=True, is_writable=True),
            AccountMeta(wallet, is_signer=True, is_writable=True),
            AccountMeta(funding_ata, is_signer=False, is_writable=True),
            AccountMeta(TRANSFER_AUTHORITY_PDA, is_signer=False, is_writable=False),
            AccountMeta(PERPETUALS_PDA, is_signer=False, is_writable=False),
            AccountMeta(pool_pk, is_signer=False, is_writable=True),
            AccountMeta(position_pda, is_signer=False, is_writable=True),
            AccountMeta(market_pk, is_signer=False, is_writable=True),
            AccountMeta(target_custody, is_signer=False, is_writable=False),
            AccountMeta(target_oracle, is_signer=False, is_writable=False),
            AccountMeta(collateral_custody, is_signer=False, is_writable=True),
            AccountMeta(collateral_oracle, is_signer=False, is_writable=False),
            AccountMeta(collateral_token_acct, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(EVENT_AUTH_PDA, is_signer=False, is_writable=False),
            AccountMeta(FLASH_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(IX_SYSVAR, is_signer=False, is_writable=False),
            AccountMeta(collateral_mint, is_signer=False, is_writable=False),
        ]

        ix = Instruction(FLASH_PROGRAM, ix_data, accounts)

        try:
            cu_ix = set_compute_unit_limit(600_000)
            price_ix = set_compute_unit_price(50_000)

            alts = []
            if info.get("alt"):
                try:
                    alt_resp = await self._conn.get_account_info(Pubkey.from_string(info["alt"]))
                    if alt_resp.value:
                        alt_acct = AddressLookupTableAccount.from_bytes(bytes(alt_resp.value.data))
                        alts.append(alt_acct)
                except Exception:
                    pass

            resp = await self._conn.get_latest_blockhash()
            msg = MessageV0.try_compile(wallet, [cu_ix, price_ix, ix], alts, resp.value.blockhash)
            tx = VersionedTransaction(msg, [self._keypair])

            result = await self._conn.send_raw_transaction(bytes(tx))
            sig = str(result.value)

            log.info(f"Flash live {direction} {market}: ${size_usd:.2f} sig={sig[:20]}...")

            self._paper_positions[market] = {
                "market": market, "direction": direction, "size_usd": size_usd,
                "leverage": leverage, "entry_price": oracle_price,
                "collateral_usd": collateral_usd, "opened_at": time.time(),
            }

            return {"status": "confirmed", "market": market, "direction": direction,
                    "oracle_price": oracle_price, "signature": sig}

        except Exception as e:
            log.error(f"Flash live open failed: {e}")
            return {"status": "error", "market": market, "error": str(e)[:200]}

    async def close_perp_position(self, market: str) -> dict:
        market = market.upper()

        if self.paper_mode:
            pos = self._paper_positions.pop(market, None)
            if not pos:
                return {"status": "no_position", "market": market}
            log.info(f"Flash paper close {market}")
            return {"status": "simulated", "market": market}

        return await self._execute_live_close(market)

    async def _execute_live_close(self, market):
        if not self._conn or not self._keypair:
            return {"status": "error", "market": market, "error": "not initialized"}

        pos = self._paper_positions.get(market)
        if not pos:
            return {"status": "no_position", "market": market}

        side = "long" if pos["direction"] == "long" else "short"
        info = _get_market_info(market, side)
        if not info:
            return {"status": "error", "market": market, "error": f"no market config for {market} {side}"}

        await self._fetch_prices()
        oracle_price = self._oracle_prices.get(market, 0)
        oracle_exp = -8
        oracle_int = int(oracle_price * (10 ** abs(oracle_exp)))

        slippage = 0.02
        if pos["direction"] == "long":
            slipped_price = int(oracle_int * (1 - slippage))
        else:
            slipped_price = int(oracle_int * (1 + slippage))

        ix_data = _serialize_close_params(slipped_price, oracle_exp)

        wallet = self._keypair.pubkey()
        pool_pk = Pubkey.from_string(info["pool"])
        market_pk = Pubkey.from_string(info["market"])
        target_custody = Pubkey.from_string(info["target_custody"])
        collateral_custody = Pubkey.from_string(info["collateral_custody"])
        target_oracle = Pubkey.from_string(info["target_oracle"])
        collateral_oracle = Pubkey.from_string(info["collateral_oracle"])
        collateral_mint = Pubkey.from_string(info["collateral_mint"])
        collateral_token_acct = Pubkey.from_string(info["collateral_token_account"])

        position_pda, _ = Pubkey.find_program_address(
            [b"position", bytes(wallet), bytes(market_pk)], FLASH_PROGRAM
        )

        receiving_ata = Pubkey.find_program_address(
            [bytes(wallet), bytes(TOKEN_PROGRAM), bytes(collateral_mint)], ATA_PROGRAM
        )[0]

        accounts = [
            AccountMeta(wallet, is_signer=True, is_writable=False),
            AccountMeta(wallet, is_signer=True, is_writable=True),
            AccountMeta(receiving_ata, is_signer=False, is_writable=True),
            AccountMeta(TRANSFER_AUTHORITY_PDA, is_signer=False, is_writable=False),
            AccountMeta(PERPETUALS_PDA, is_signer=False, is_writable=False),
            AccountMeta(pool_pk, is_signer=False, is_writable=True),
            AccountMeta(position_pda, is_signer=False, is_writable=True),
            AccountMeta(market_pk, is_signer=False, is_writable=True),
            AccountMeta(target_custody, is_signer=False, is_writable=False),
            AccountMeta(target_oracle, is_signer=False, is_writable=False),
            AccountMeta(collateral_custody, is_signer=False, is_writable=True),
            AccountMeta(collateral_oracle, is_signer=False, is_writable=False),
            AccountMeta(collateral_token_acct, is_signer=False, is_writable=True),
            AccountMeta(TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(EVENT_AUTH_PDA, is_signer=False, is_writable=False),
            AccountMeta(FLASH_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(IX_SYSVAR, is_signer=False, is_writable=False),
            AccountMeta(collateral_mint, is_signer=False, is_writable=False),
        ]

        ix = Instruction(FLASH_PROGRAM, ix_data, accounts)

        try:
            cu_ix = set_compute_unit_limit(600_000)
            price_ix = set_compute_unit_price(50_000)

            alts = []
            if info.get("alt"):
                try:
                    alt_resp = await self._conn.get_account_info(Pubkey.from_string(info["alt"]))
                    if alt_resp.value:
                        alt_acct = AddressLookupTableAccount.from_bytes(bytes(alt_resp.value.data))
                        alts.append(alt_acct)
                except Exception:
                    pass

            resp = await self._conn.get_latest_blockhash()
            msg = MessageV0.try_compile(wallet, [cu_ix, price_ix, ix], alts, resp.value.blockhash)
            tx = VersionedTransaction(msg, [self._keypair])

            result = await self._conn.send_raw_transaction(bytes(tx))
            sig = str(result.value)

            self._paper_positions.pop(market, None)
            log.info(f"Flash live close {market}: sig={sig[:20]}...")

            return {"status": "confirmed", "market": market, "signature": sig}

        except Exception as e:
            log.error(f"Flash live close failed: {e}")
            return {"status": "error", "market": market, "error": str(e)[:200]}

    async def get_position(self, market: str) -> Optional[dict]:
        market = market.upper()
        pos = self._paper_positions.get(market)
        if not pos:
            return None
        await self._fetch_prices()
        current_price = self._oracle_prices.get(market, pos["entry_price"])
        entry = pos["entry_price"]
        if pos["direction"] == "long":
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_pct = (entry - current_price) / entry
        unrealized = pnl_pct * pos["collateral_usd"] * pos["leverage"]
        return {
            "market": market, "market_index": -1,
            "direction": pos["direction"],
            "size": pos["size_usd"] / entry if entry > 0 else 0,
            "size_raw": 0, "entry_price": entry,
            "unrealized_pnl": unrealized,
        }

    def get_account_summary(self) -> Optional[dict]:
        total_collateral = sum(p["collateral_usd"] for p in self._paper_positions.values())
        total_upnl = 0
        for market, pos in self._paper_positions.items():
            price = self._oracle_prices.get(market, pos["entry_price"])
            entry = pos["entry_price"]
            if pos["direction"] == "long":
                pnl_pct = (price - entry) / entry
            else:
                pnl_pct = (entry - price) / entry
            total_upnl += pnl_pct * pos["collateral_usd"] * pos["leverage"]
        if total_collateral > 0 or total_upnl != 0:
            return {"collateral": total_collateral, "unrealized_pnl": total_upnl, "net_value": total_collateral + total_upnl}
        return None
