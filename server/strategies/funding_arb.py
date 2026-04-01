import time
import logging
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.execution.drift import DriftExecutor, MARKET_INDEX

log = logging.getLogger("funding_arb")

ENTRY_FUNDING_APY = 10.0
EXIT_FUNDING_APY = 5.0
NEGATIVE_EXIT_HOURS = 2


class FundingArbStrategy(BaseStrategy):
    STRATEGY_ID = "funding_arb"
    STRATEGY_NAME = "Funding Arb"

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self.drift: DriftExecutor | None = None
        self._funding_rates: dict[str, float] = {}
        self._funding_history: list[dict] = []
        self._best_market: str = ""
        self._best_funding_apy: float = 0.0
        self._negative_since: float = 0.0

    async def init_drift(self):
        if not self.drift:
            self.drift = DriftExecutor(paper_mode=(self.mode != "live"))
            await self.drift.start()

    async def _fetch_funding_rates(self):
        if not self.drift or not self.drift.client:
            return
        rates = {}
        for market in ["SOL", "BTC", "ETH", "JUP", "JTO", "SUI", "WIF", "BONK"]:
            try:
                rate = await self.drift.get_funding_rate(market)
                rates[market] = rate
            except Exception:
                pass
        self._funding_rates = rates
        if rates:
            best = max(rates, key=rates.get)
            self._best_market = best
            self._best_funding_apy = rates[best]

    async def evaluate(self, market_data: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return {"action": "wait", "reason": "no_price_data"}

        await self.init_drift()
        await self._fetch_funding_rates()

        funding_apy = self._best_funding_apy
        market = self._best_market

        if funding_apy > ENTRY_FUNDING_APY:
            self._negative_since = 0
            if not self.active_positions and self.capital_allocated > 0:
                return {
                    "action": "open",
                    "market": market,
                    "deposit_usd": self.capital_allocated * 0.5,
                    "funding_apy": funding_apy,
                }
        elif funding_apy < -EXIT_FUNDING_APY:
            if self._negative_since == 0:
                self._negative_since = time.time()
            hours_negative = (time.time() - self._negative_since) / 3600
            if hours_negative >= NEGATIVE_EXIT_HOURS and self.active_positions:
                return {
                    "action": "close",
                    "position_id": self.active_positions[0].id,
                    "reason": f"funding negative for {hours_negative:.1f}h",
                }
        elif funding_apy < EXIT_FUNDING_APY and self.active_positions:
            return {
                "action": "close",
                "position_id": self.active_positions[0].id,
                "reason": f"funding dropped to {funding_apy:.1f}% APY",
            }
        else:
            self._negative_since = 0

        return {"action": "hold"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if action["action"] == "close":
            pos = next((p for p in self.active_positions if p.id == action["position_id"]), None)
            if pos:
                market = pos.metadata.get("market", "SOL")
                if self.mode == "live" and self.drift:
                    try:
                        await self.drift.close_perp_position(market)
                        log.info(f"Closed funding arb short on {market}")
                    except Exception as e:
                        log.error(f"Failed to close funding arb: {e}")
                self.close_position(action["position_id"])
                self.status = "idle"
            return None

        if action["action"] == "open":
            market = action.get("market", "SOL")
            deposit = action["deposit_usd"]
            funding_apy = action["funding_apy"]

            if self.mode == "live" and self.drift:
                try:
                    result = await self.drift.open_perp_position(market, "short", deposit, 1.0)
                    log.info(f"Funding arb: short {market} ${deposit:.2f} at {funding_apy:.1f}% APY -> {result.get('status')}")
                except Exception as e:
                    log.error(f"Failed to open funding arb: {e}")
                    return None

            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=f"drift_{market.lower()}_perp",
                entry_price=sol_price,
                deposit_usd=deposit,
                current_value_usd=deposit,
                in_range=True,
                metadata={
                    "market": market,
                    "entry_funding_apy": funding_apy,
                    "hedge_type": "short_perp_delta_neutral",
                    "notional": deposit,
                },
            )
            self.positions.append(position)
            self.status = "active"
            log.info(f"Funding arb opened: short {market} ${deposit:.2f} at {funding_apy:.1f}% APY")
            return position

        return None

    async def update(self, market_data: dict):
        now = time.time()
        await self._fetch_funding_rates()

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            market = position.metadata.get("market", "SOL")
            funding_apy = self._funding_rates.get(market, 0)

            if funding_apy > 0:
                hourly_funding = funding_apy / 100 / 365 / 24
                funding_income = hourly_funding * hours_elapsed * position.deposit_usd
                position.fees_earned_usd += funding_income
            elif funding_apy < 0:
                hourly_cost = abs(funding_apy) / 100 / 365 / 24
                funding_cost = hourly_cost * hours_elapsed * position.deposit_usd
                position.fees_earned_usd -= funding_cost

            position.current_value_usd = position.deposit_usd + position.fees_earned_usd
            position.in_range = True
            position.hours_in_range += hours_elapsed
            position.last_update = now

        self._funding_history.append({
            "timestamp": now,
            "rates": dict(self._funding_rates),
            "best": self._best_market,
            "best_apy": self._best_funding_apy,
        })
        if len(self._funding_history) > 500:
            self._funding_history = self._funding_history[-500:]

        self.last_update = now
        self.metrics = {
            "funding_apy": self._best_funding_apy,
            "funding_direction": "positive" if self._best_funding_apy > ENTRY_FUNDING_APY else "negative" if self._best_funding_apy < -EXIT_FUNDING_APY else "neutral",
            "best_market": self._best_market,
            "all_rates": self._funding_rates,
            "strategy": f"short {self._best_market} at {self._best_funding_apy:.1f}% APY" if self.active_positions else "scanning",
        }
