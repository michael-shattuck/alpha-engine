import time
import logging
import asyncio
import json
import subprocess
from typing import Optional
from pathlib import Path

import httpx

log = logging.getLogger("flash_trade")

FLASH_MARKETS = {
    "SOL", "BTC", "ETH", "BNB",
    "JUP", "PYTH", "JTO", "RAY", "KMNO",
    "BONK", "PENGU", "WIF", "FARTCOIN", "PUMP",
}

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
}

BRIDGE_DIR = Path(__file__).parent / "flash_bridge"


class FlashTradeExecutor:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self._started = False
        self._paper_positions: dict[str, dict] = {}
        self._oracle_prices: dict[str, float] = {}
        self._last_price_fetch: float = 0
        self.client = None

    async def start(self):
        if self._started:
            return
        await self._fetch_prices()
        self._started = True
        mode = "paper" if self.paper_mode else "live"
        log.info(f"Flash Trade executor started ({mode}, {len(FLASH_MARKETS)} markets)")

    async def stop(self):
        self._started = False

    async def _fetch_prices(self):
        now = time.time()
        if now - self._last_price_fetch < 5:
            return
        try:
            feed_ids = {sym: fid for sym, fid in PYTH_FEED_IDS.items() if sym in FLASH_MARKETS}
            params = [("ids[]", fid) for fid in feed_ids.values()]
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get("http://20.120.229.168:4160/api/latest_price_feeds", params=params)
                if r.status_code == 200:
                    feeds = r.json()
                    fid_to_symbol = {fid.removeprefix("0x"): sym for sym, fid in feed_ids.items()}
                    for feed in feeds:
                        fid = feed.get("id", "")
                        symbol = fid_to_symbol.get(fid)
                        if symbol:
                            pd = feed.get("price", {})
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
        return list(FLASH_MARKETS)

    async def open_perp_position(self, market: str, direction: str, size_usd: float, leverage: float) -> dict:
        market = market.upper()
        if market not in FLASH_MARKETS:
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
            return {
                "status": "simulated",
                "market": market,
                "direction": direction,
                "oracle_price": oracle_price,
            }

        return await self._execute_live_open(market, direction, size_usd, leverage, oracle_price)

    async def _execute_live_open(self, market, direction, size_usd, leverage, oracle_price):
        log.warning(f"Flash Trade live execution not yet implemented for {market}")
        return {"status": "error", "market": market, "error": "live not implemented yet"}

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
        log.warning(f"Flash Trade live close not yet implemented for {market}")
        return {"status": "error", "market": market, "error": "live not implemented yet"}

    async def get_position(self, market: str) -> Optional[dict]:
        market = market.upper()

        if self.paper_mode:
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
                "market": market,
                "market_index": -1,
                "direction": pos["direction"],
                "size": pos["size_usd"] / entry if entry > 0 else 0,
                "size_raw": 0,
                "entry_price": entry,
                "unrealized_pnl": unrealized,
            }

        return None

    def get_account_summary(self) -> Optional[dict]:
        if self.paper_mode:
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
            return {
                "collateral": total_collateral,
                "unrealized_pnl": total_upnl,
                "net_value": total_collateral + total_upnl,
            }
        return None
