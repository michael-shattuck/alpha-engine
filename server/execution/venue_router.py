import time
import logging
from typing import Optional
from dataclasses import dataclass, field

log = logging.getLogger("venue_router")


@dataclass
class VenueHealth:
    consecutive_failures: int = 0
    unhealthy_until: float = 0.0
    total_orders: int = 0
    successful_orders: int = 0

    @property
    def is_healthy(self) -> bool:
        return time.time() > self.unhealthy_until

    def record_success(self):
        self.consecutive_failures = 0
        self.total_orders += 1
        self.successful_orders += 1

    def record_failure(self):
        self.consecutive_failures += 1
        self.total_orders += 1
        if self.consecutive_failures >= 3:
            self.unhealthy_until = time.time() + 300
            log.warning(f"Venue marked unhealthy for 5 min after {self.consecutive_failures} failures")


class VenueRouter:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.venues: dict = {}
        self.venue_health: dict[str, VenueHealth] = {}
        self.market_to_venues: dict[str, list[str]] = {}
        self._started = False
        self.client = None

    async def start(self):
        if self._started:
            return

        from server.execution.jupiter_perps import JupiterPerpsExecutor

        jup = JupiterPerpsExecutor(paper_mode=self.paper_mode)
        await jup.start()
        self.venues["jupiter"] = jup
        self.venue_health["jupiter"] = VenueHealth()

        try:
            from server.execution.drift import DriftExecutor, SETTLEMENT_MARKETS
            drift = DriftExecutor(paper_mode=self.paper_mode)
            await drift.start()
            if drift.client:
                try:
                    drift.client.get_user()
                    self.venues["drift"] = drift
                    self.venue_health["drift"] = VenueHealth()
                    log.info("Drift available and added to router")
                except Exception:
                    log.info("Drift client connected but orders may fail (post-exploit)")
            else:
                log.info("Drift not available")
        except Exception as e:
            log.info(f"Drift init failed: {e}")

        self._build_routing_table()
        self._started = True

        venue_names = list(self.venues.keys())
        market_count = len(self.market_to_venues)
        log.info(f"VenueRouter started: {venue_names}, {market_count} markets routable")

    def _build_routing_table(self):
        self.market_to_venues = {}
        for venue_name, executor in self.venues.items():
            for market in executor.get_available_markets():
                if market not in self.market_to_venues:
                    self.market_to_venues[market] = []
                self.market_to_venues[market].append(venue_name)

    def _route(self, market: str) -> tuple[Optional[str], Optional[object]]:
        venues = self.market_to_venues.get(market.upper(), [])
        for venue_name in venues:
            health = self.venue_health.get(venue_name)
            if health and health.is_healthy:
                return venue_name, self.venues[venue_name]
        if venues:
            name = venues[0]
            return name, self.venues[name]
        return None, None

    async def stop(self):
        for executor in self.venues.values():
            try:
                await executor.stop()
            except Exception:
                pass
        self._started = False

    async def open_perp_position(self, market: str, direction: str, size_usd: float, leverage: float) -> dict:
        venue_name, executor = self._route(market)
        if not executor:
            return {"status": "error", "market": market, "error": f"no venue for {market}"}

        try:
            result = await executor.open_perp_position(market, direction, size_usd, leverage)
            if result.get("status") in ("confirmed", "simulated"):
                self.venue_health[venue_name].record_success()
            else:
                self.venue_health[venue_name].record_failure()
            result["venue"] = venue_name
            return result
        except Exception as e:
            self.venue_health[venue_name].record_failure()
            log.error(f"Venue {venue_name} open failed for {market}: {e}")
            return {"status": "error", "market": market, "venue": venue_name, "error": str(e)}

    async def close_perp_position(self, market: str) -> dict:
        venue_name, executor = self._route(market)
        if not executor:
            return {"status": "error", "market": market, "error": f"no venue for {market}"}

        try:
            result = await executor.close_perp_position(market)
            if result.get("status") in ("confirmed", "simulated"):
                self.venue_health[venue_name].record_success()
            result["venue"] = venue_name
            return result
        except Exception as e:
            self.venue_health[venue_name].record_failure()
            return {"status": "error", "market": market, "venue": venue_name, "error": str(e)}

    async def get_position(self, market: str) -> Optional[dict]:
        for venue_name, executor in self.venues.items():
            if market.upper() in [m.upper() for m in executor.get_available_markets()]:
                pos = await executor.get_position(market)
                if pos:
                    pos["venue"] = venue_name
                    return pos
        return None

    def get_oracle_price(self, market: str) -> float:
        venue_name, executor = self._route(market)
        if executor:
            return executor.get_oracle_price(market)
        return 0.0

    def get_oracle_prices(self) -> dict[str, float]:
        merged = {}
        for executor in self.venues.values():
            merged.update(executor.get_oracle_prices())
        return merged

    def get_available_markets(self) -> list[str]:
        return list(self.market_to_venues.keys())

    def get_account_summary(self) -> Optional[dict]:
        total_col = 0.0
        total_upnl = 0.0
        for executor in self.venues.values():
            summary = executor.get_account_summary()
            if summary:
                total_col += summary.get("collateral", 0)
                total_upnl += summary.get("unrealized_pnl", 0)
        if total_col > 0 or total_upnl != 0:
            return {
                "collateral": total_col,
                "unrealized_pnl": total_upnl,
                "net_value": total_col + total_upnl,
            }
        return None

    def get_venue_status(self) -> dict:
        return {
            name: {
                "healthy": health.is_healthy,
                "markets": executor.get_available_markets(),
                "orders": health.total_orders,
                "success_rate": health.successful_orders / max(health.total_orders, 1),
                "consecutive_failures": health.consecutive_failures,
            }
            for name, (executor, health) in (
                (n, (self.venues[n], self.venue_health[n])) for n in self.venues
            )
        }
