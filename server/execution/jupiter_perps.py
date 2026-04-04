import time
import logging
import asyncio
from typing import Optional

import httpx

log = logging.getLogger("jupiter_perps")

JUPITER_PERPS_PROGRAM = "PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu"
JLP_POOL = "5BUwFW4nRbftYTDMbgxykoFWqWHPzahFSNAaaaJtVKsq"

JUPITER_MARKETS = {"SOL", "BTC", "ETH"}

PYTH_FEED_IDS = {
    "SOL": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
}


class JupiterPerpsExecutor:
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
        log.info(f"Jupiter Perps executor started ({mode}, {len(JUPITER_MARKETS)} markets)")

    async def stop(self):
        self._started = False

    async def _fetch_prices(self):
        now = time.time()
        if now - self._last_price_fetch < 5:
            return
        try:
            params = [("ids[]", fid) for fid in PYTH_FEED_IDS.values()]
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get("http://20.120.229.168:4160/api/latest_price_feeds", params=params)
                if r.status_code == 200:
                    feeds = r.json()
                    fid_to_symbol = {fid.removeprefix("0x"): sym for sym, fid in PYTH_FEED_IDS.items()}
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
            log.warning(f"Price fetch failed: {e}")

    def get_oracle_price(self, market: str) -> float:
        return self._oracle_prices.get(market.upper(), 0.0)

    def get_oracle_prices(self) -> dict[str, float]:
        return dict(self._oracle_prices)

    def get_available_markets(self) -> list[str]:
        return list(JUPITER_MARKETS)

    async def open_perp_position(self, market: str, direction: str, size_usd: float, leverage: float) -> dict:
        market = market.upper()
        if market not in JUPITER_MARKETS:
            return {"status": "error", "market": market, "error": f"{market} not on Jupiter Perps"}

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
            log.info(f"Jupiter paper {direction} {market}: ${size_usd:.2f} at {leverage}x, entry=${oracle_price:.2f}")
            return {
                "status": "simulated",
                "market": market,
                "direction": direction,
                "oracle_price": oracle_price,
            }

        log.warning(f"Jupiter Perps live execution not yet implemented for {market}")
        return {"status": "error", "market": market, "error": "live not implemented"}

    async def close_perp_position(self, market: str) -> dict:
        market = market.upper()

        if self.paper_mode:
            pos = self._paper_positions.pop(market, None)
            if not pos:
                return {"status": "no_position", "market": market}
            log.info(f"Jupiter paper close {market}")
            return {"status": "simulated", "market": market}

        log.warning(f"Jupiter Perps live close not yet implemented for {market}")
        return {"status": "error", "market": market, "error": "live not implemented"}

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
