import asyncio
import logging
import base58
from solders.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

from driftpy.drift_client import DriftClient
from driftpy.drift_user import DriftUser
from driftpy.types import (
    MarketType,
    OrderType,
    OrderParams,
    PositionDirection,
    TxParams,
)
from driftpy.accounts import get_perp_market_account
from driftpy.constants.perp_markets import mainnet_perp_market_configs
from driftpy.keypair import load_keypair

from server.config import SOLANA_RPC_URL, WALLET_PRIVATE_KEY, HELIUS_RPC_URL

log = logging.getLogger("drift")

MARKET_INDEX = {
    "SOL": 0,
    "BTC": 1,
    "ETH": 2,
    "1MBONK": 4,
    "BONK": 4,
    "SUI": 9,
    "PYTH": 18,
    "JTO": 20,
    "SEI": 21,
    "WIF": 23,
    "JUP": 24,
    "W": 27,
    "POPCAT": 34,
    "MOODENG": 45,
    "PENGU": 62,
    "TRUMP": 64,
    "FARTCOIN": 71,
}


class DriftExecutor:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.client: DriftClient | None = None
        self.user: DriftUser | None = None
        self._started = False
        self._paper_positions: dict[str, dict] = {}

    async def start(self):
        if self._started:
            return

        if self.paper_mode:
            self._started = True
            log.info("Drift executor started (paper mode)")
            return

        connection = AsyncClient(HELIUS_RPC_URL or SOLANA_RPC_URL, commitment=Confirmed)
        keypair = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))

        try:
            self.client = DriftClient(connection, keypair)
            await self.client.subscribe()
            self.user = self.client.get_user()
            self._started = True
            log.info("Drift executor started (live)")
        except Exception as e:
            log.error(f"Drift init failed: {e}")
            self.client = None

    async def stop(self):
        if self.client:
            await self.client.unsubscribe()
        self._started = False

    async def open_perp_position(
        self, market: str, direction: str, size_usd: float, leverage: float
    ) -> dict:
        market_index = MARKET_INDEX.get(market.upper())
        if market_index is None:
            raise ValueError(f"Unknown market: {market}")

        perp_direction = PositionDirection.Long() if direction == "long" else PositionDirection.Short()

        if self.paper_mode:
            self._paper_positions[market] = {
                "market": market,
                "direction": direction,
                "size_usd": size_usd,
                "leverage": leverage,
                "market_index": market_index,
            }
            log.info(f"Paper Drift {direction} {market}-PERP: ${size_usd:.2f} at {leverage}x")
            return {"status": "simulated", "market": market, "direction": direction}

        try:
            oracle_price_data = self.client.get_oracle_price_data_for_perp_market(market_index)
            oracle_price = oracle_price_data.price / 1e6
        except Exception:
            oracle_price = size_usd / 0.1

        base_tokens = size_usd / oracle_price
        base_amount = int(base_tokens * 1e9)

        order_params = OrderParams(
            order_type=OrderType.Market(),
            market_type=MarketType.Perp(),
            market_index=market_index,
            direction=perp_direction,
            base_asset_amount=base_amount,
        )

        sig = await self.client.place_perp_order(order_params)
        log.info(f"Drift {direction} {market}-PERP: ${size_usd:.2f} notional ({base_tokens:.4f} {market}), sig={sig}")

        return {
            "status": "confirmed",
            "market": market,
            "direction": direction,
            "signature": str(sig),
        }

    async def close_perp_position(self, market: str) -> dict:
        market_index = MARKET_INDEX.get(market.upper())
        if market_index is None:
            raise ValueError(f"Unknown market: {market}")

        if self.paper_mode:
            pos = self._paper_positions.pop(market, None)
            if pos:
                log.info(f"Paper Drift close {market}-PERP")
            return {"status": "simulated", "market": market}

        position = await self.get_position(market)
        if not position or position["size"] == 0:
            log.info(f"No Drift position for {market}")
            return {"status": "no_position", "market": market}

        close_direction = (
            PositionDirection.Short() if position["direction"] == "long"
            else PositionDirection.Long()
        )

        order_params = OrderParams(
            order_type=OrderType.Market(),
            market_type=MarketType.Perp(),
            market_index=market_index,
            direction=close_direction,
            base_asset_amount=abs(position["size_raw"]),
            reduce_only=True,
        )

        sig = await self.client.place_perp_order(order_params)
        log.info(f"Drift close {market}-PERP: sig={sig}")

        return {"status": "confirmed", "market": market, "signature": str(sig)}

    async def get_position(self, market: str) -> dict | None:
        market_index = MARKET_INDEX.get(market.upper())
        if market_index is None:
            return None

        if self.paper_mode:
            return self._paper_positions.get(market)

        if not self.user:
            return None

        try:
            perp_pos = self.user.get_perp_position(market_index)
            if perp_pos is None or perp_pos.base_asset_amount == 0:
                return None

            return {
                "market": market,
                "market_index": market_index,
                "direction": "long" if perp_pos.base_asset_amount > 0 else "short",
                "size": abs(perp_pos.base_asset_amount) / 1e9,
                "size_raw": perp_pos.base_asset_amount,
                "entry_price": perp_pos.quote_entry_amount / abs(perp_pos.base_asset_amount) if perp_pos.base_asset_amount != 0 else 0,
                "unrealized_pnl": perp_pos.quote_asset_amount / 1e6,
            }
        except Exception:
            return None

    async def get_funding_rate(self, market: str) -> float:
        market_index = MARKET_INDEX.get(market.upper())
        if market_index is None:
            return 0.0

        if self.paper_mode:
            return 0.0

        try:
            perp_market = await get_perp_market_account(self.client.program, market_index)
            rate = perp_market.amm.last_funding_rate / 1e9
            return rate * 8760 * 100
        except Exception:
            return 0.0

    def get_available_markets(self) -> list[str]:
        return list(MARKET_INDEX.keys())
