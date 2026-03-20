import time
import math
import asyncio
import struct
import httpx
from server.config import JUPITER_API, JUPITER_PRICE_API, DEFILLAMA_API, SOL_MINT, USDC_MINT, SOLANA_RPC_URL, ORCA_WHIRLPOOL_SOL_USDC

PYTH_HERMES_URL = "http://20.120.229.168:4160"
PYTH_SOL_USD_FEED = "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d"


class PriceService:
    def __init__(self):
        self.http: httpx.AsyncClient | None = None
        self.sol_price = 0.0
        self.sol_price_history: list[dict] = []
        self.pool_apys: dict[str, float] = {}
        self.jlp_apy = 0.0
        self.last_price_update = 0.0
        self.last_apy_update = 0.0
        self._volatility_1h = 0.0
        self._volatility_24h = 0.0

    async def start(self):
        self.http = httpx.AsyncClient(timeout=30.0)

    async def stop(self):
        if self.http:
            await self.http.aclose()

    async def _price_from_pyth(self) -> float:
        resp = await self.http.get(
            f"{PYTH_HERMES_URL}/api/latest_price_feeds",
            params={"ids[]": PYTH_SOL_USD_FEED}
        )
        if resp.status_code == 200:
            feeds = resp.json()
            if feeds and len(feeds) > 0:
                price_data = feeds[0].get("price", {})
                return int(price_data["price"]) * (10 ** int(price_data["expo"]))
        return 0

    async def _price_from_orca_rpc(self) -> float:
        resp = await self.http.post(
            SOLANA_RPC_URL,
            json={
                "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                "params": [ORCA_WHIRLPOOL_SOL_USDC, {"encoding": "base64"}]
            }
        )
        if resp.status_code == 200:
            result = resp.json().get("result", {})
            value = result.get("value")
            if value:
                import base64
                data = base64.b64decode(value["data"][0])
                if len(data) >= 73:
                    sqrt_price = int.from_bytes(data[65:73], "little")
                    price = (sqrt_price / (2**64)) ** 2 * (10 ** (9 - 6))
                    return price
        return 0

    async def _price_from_jupiter(self) -> float:
        resp = await self.http.get(
            f"{JUPITER_API}/quote",
            params={
                "inputMint": SOL_MINT,
                "outputMint": USDC_MINT,
                "amount": str(10**9),
                "slippageBps": 50,
            }
        )
        if resp.status_code == 200:
            return int(resp.json()["outAmount"]) / 1e6
        return 0

    async def update_sol_price(self) -> float:
        price = 0
        for source in [self._price_from_pyth, self._price_from_orca_rpc, self._price_from_jupiter]:
            try:
                price = await source()
                if price > 0:
                    break
            except Exception:
                continue

        if price > 0:
            now = time.time()
            self.sol_price = price
            self.sol_price_history.append({"t": now, "p": price})
            self.sol_price_history = [
                h for h in self.sol_price_history if now - h["t"] < 86400
            ]
            self._compute_volatility()
            self.last_price_update = now

        return self.sol_price

    async def get_token_price(self, mint: str) -> float:
        try:
            resp = await self.http.get(
                JUPITER_PRICE_API,
                params={"ids": mint}
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get(mint, {})
                return float(data.get("price", 0))
        except Exception:
            pass
        return 0.0

    async def update_pool_apys(self):
        try:
            resp = await self.http.get(f"{DEFILLAMA_API}/pools")
            if resp.status_code != 200:
                return

            data = resp.json()
            for pool in data.get("data", []):
                if pool.get("chain") != "Solana":
                    continue
                project = pool.get("project", "")
                symbol = pool.get("symbol", "")
                tvl = float(pool.get("tvlUsd", 0) or 0)
                apy = float(pool.get("apy", 0) or 0)

                if project == "orca-dex" and "SOL-USDC" in symbol and tvl > 10_000_000:
                    self.pool_apys["orca_sol_usdc"] = apy

                if project == "orca-dex" and tvl > 1_000_000 and apy > 20:
                    key = f"orca_{symbol.lower().replace('-', '_')}"
                    self.pool_apys[key] = apy

                if project == "jupiter-perps" or (
                    "JLP" in symbol and project in ("jupiter-lend", "jupiter") and tvl > 50_000_000
                ):
                    self.jlp_apy = max(self.jlp_apy, apy)

                if project in ("raydium", "meteora") and tvl > 500_000 and apy > 30:
                    key = f"{project}_{symbol.lower().replace('-', '_')}"
                    self.pool_apys[key] = apy

            if self.jlp_apy <= 0:
                self.jlp_apy = 20.0

            self.last_apy_update = time.time()
        except Exception:
            pass

    def _compute_volatility(self):
        now = time.time()

        prices_1h = [h["p"] for h in self.sol_price_history if now - h["t"] < 3600]
        if len(prices_1h) >= 2:
            returns = [
                (prices_1h[i] - prices_1h[i - 1]) / prices_1h[i - 1]
                for i in range(1, len(prices_1h))
            ]
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            self._volatility_1h = variance ** 0.5
        else:
            self._volatility_1h = 0.0

        prices_24h = [h["p"] for h in self.sol_price_history]
        if len(prices_24h) >= 2:
            returns = [
                (prices_24h[i] - prices_24h[i - 1]) / prices_24h[i - 1]
                for i in range(1, len(prices_24h))
            ]
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            self._volatility_24h = variance ** 0.5
        else:
            self._volatility_24h = 0.0

    @property
    def volatility_1h(self) -> float:
        return self._volatility_1h

    @property
    def volatility_24h(self) -> float:
        return self._volatility_24h

    @property
    def price_change_1h(self) -> float:
        now = time.time()
        prices_1h = [h for h in self.sol_price_history if now - h["t"] < 3600]
        if len(prices_1h) >= 2:
            return (prices_1h[-1]["p"] - prices_1h[0]["p"]) / prices_1h[0]["p"] * 100
        return 0.0

    @property
    def price_change_24h(self) -> float:
        if len(self.sol_price_history) >= 2:
            return (
                (self.sol_price_history[-1]["p"] - self.sol_price_history[0]["p"])
                / self.sol_price_history[0]["p"]
                * 100
            )
        return 0.0

    async def update_funding_rates(self):
        try:
            resp = await self.http.get(
                "https://data.api.drift.trade/fundingRates",
                params={"marketIndex": 0}
            )
            if resp.status_code != 200:
                return

            rates = resp.json().get("fundingRates", [])
            if len(rates) < 24:
                return

            recent = rates[-24:]
            total = 0
            for r in recent:
                rate = int(r["fundingRate"]) / 1e12
                total += rate

            avg_hourly = total / len(recent)
            self._funding_apy = avg_hourly * 8760 * 100
        except Exception:
            pass

    def get_market_data(self) -> dict:
        return {
            "sol_price": self.sol_price,
            "sol_change_1h": self.price_change_1h,
            "sol_change_24h": self.price_change_24h,
            "volatility_1h": self.volatility_1h,
            "volatility_24h": self.volatility_24h,
            "pool_apys": dict(self.pool_apys),
            "jlp_apy": self.jlp_apy,
            "funding_apy": getattr(self, "_funding_apy", 0.0),
            "timestamp": time.time(),
        }

    def get_best_pools(self, min_apy: float = 30.0, limit: int = 10) -> list[dict]:
        pools = [
            {"pool": k, "apy": v}
            for k, v in self.pool_apys.items()
            if v >= min_apy
        ]
        pools.sort(key=lambda x: x["apy"], reverse=True)
        return pools[:limit]
