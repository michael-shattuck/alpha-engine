import asyncio
import time
import logging
from typing import Optional

from server.config import (
    ORCHESTRATOR_INTERVAL,
    PRICE_UPDATE_INTERVAL,
    RISK_CHECK_INTERVAL,
    DEFAULT_CAPITAL_ALLOCATION,
    DEFAULT_MODE,
)
from server.state import StateManager
from server.execution.prices import PriceService
from server.strategies.base import BaseStrategy
from server.risk.guardian import Guardian
from server.intelligence import AIOrchestrator as AI
from server.strategies.allocator import DynamicAllocator
from server.alerts import alerts
from server.config import DATABASE_URL

log = logging.getLogger("orchestrator")


class Orchestrator:
    def __init__(self, capital: float, mode: str = DEFAULT_MODE):
        self.capital = capital
        self.mode = mode
        self.state = StateManager()
        self.prices = PriceService()
        self.guardian = Guardian()
        self.ai = AI()
        self.allocator = DynamicAllocator()
        self.strategies: dict[str, BaseStrategy] = {}
        self.running = False
        self._last_price_update = 0.0
        self._last_risk_check = 0.0
        self._last_apy_update = 0.0
        self._last_snapshot = 0.0

    def register_strategy(self, strategy: BaseStrategy, dormant: bool = False):
        self.strategies[strategy.STRATEGY_ID] = strategy
        existing = self.state.get_strategy(strategy.STRATEGY_ID)
        if existing:
            strategy.load_state(existing)
        if dormant:
            strategy.enabled = False

    async def start(self):
        log.info(f"Starting orchestrator: mode={self.mode}, capital=${self.capital:.2f}")
        await self.prices.start()
        await alerts.start()
        self.running = True

        self.state.portfolio.total_capital = self.capital
        self.state.portfolio.mode = self.mode
        self.state.portfolio.circuit_breaker_active = False
        self.state.portfolio.started_at = time.time()

        self._apply_allocations(DEFAULT_CAPITAL_ALLOCATION)

        await self.prices.update_sol_price()
        await self.prices.update_pool_apys()

        for sid, strategy in self.strategies.items():
            if hasattr(strategy, "warmup") and self.prices.sol_price_history:
                try:
                    await strategy.warmup(self.prices.sol_price_history)
                except Exception as e:
                    log.error(f"Strategy {sid} warmup failed: {e}")

        if self.mode == "live":
            market_data = self.prices.get_market_data()
            for sid, strategy in self.strategies.items():
                if hasattr(strategy, "recover_onchain_positions"):
                    await strategy.recover_onchain_positions(market_data.get("sol_price", 0))

        for sid, strategy in self.strategies.items():
            if not strategy.active_positions and strategy.enabled and strategy.capital_allocated > 0:
                market_data = self.prices.get_market_data()
                actions = await strategy.evaluate(market_data)
                action = actions.get("action", "")
                if action and action not in ("hold", "wait"):
                    await strategy.execute(actions, market_data)
                    self.state.add_event(action, sid, {"capital": strategy.capital_allocated})

        self._save_all_states()
        log.info("Orchestrator started, entering main loop")
        await self._main_loop()

    async def stop(self):
        log.info("Stopping orchestrator")
        self.running = False
        self._save_all_states()
        await self.prices.stop()
        await alerts.stop()

    async def _main_loop(self):
        while self.running:
            try:
                now = time.time()

                if now - self._last_price_update >= PRICE_UPDATE_INTERVAL:
                    await self.prices.update_sol_price()
                    self._last_price_update = now

                if now - self._last_apy_update >= 300:
                    await self.prices.update_pool_apys()
                    await self.prices.update_funding_rates()
                    self._last_apy_update = now

                market_data = self.prices.get_market_data()

                for sid, strategy in self.strategies.items():
                    if not strategy.enabled:
                        continue
                    try:
                        await strategy.update(market_data)
                    except Exception as e:
                        log.error(f"Strategy {sid} update error: {e}")
                        strategy.error = str(e)
                        strategy.status = "error"

                if now - self._last_risk_check >= RISK_CHECK_INTERVAL:
                    await self._intelligence_cycle(market_data)
                    # self._check_dynamic_allocation()  # disabled for manual allocation test
                    self._last_risk_check = now

                for sid, strategy in self.strategies.items():
                    if not strategy.enabled:
                        continue
                    try:
                        actions = await strategy.evaluate(market_data)
                        action = actions.get("action", "")
                        if action and action not in ("hold", "wait"):
                            await strategy.execute(actions, market_data)
                            self.state.add_event(action, sid, actions)
                            self.ai.rebalancer.record_rebalance(
                                self.prices.sol_price,
                                strategy.capital_allocated * 0.0008,
                                action,
                            )
                    except Exception as e:
                        log.error(f"Strategy {sid} execute error: {e}")

                if now - self._last_snapshot >= 60:
                    self._save_all_states()
                    self.state.add_snapshot(self.prices.sol_price)
                    self._record_strategy_performance(market_data)
                    self._save_db_snapshot(market_data)
                    self._last_snapshot = now

                await asyncio.sleep(ORCHESTRATOR_INTERVAL)

            except Exception as e:
                log.error(f"Main loop error: {e}")
                await asyncio.sleep(5)

    async def _intelligence_cycle(self, market_data: dict):
        total_equity = self._compute_total_equity()
        portfolio_data = {
            "total_equity": total_equity,
            "capital": self.capital,
            "uptime_hours": (time.time() - self.state.portfolio.started_at) / 3600,
            "strategies": {sid: s.get_state() for sid, s in self.strategies.items()},
        }

        assessment = self.guardian.assess(portfolio_data, market_data)
        self.state.portfolio.risk_level = assessment["risk_level"]

        if assessment["risk_level"] in ("high", "critical"):
            await alerts.risk_alert(
                assessment["risk_level"],
                assessment.get("drawdown_pct", 0),
                f"actions={len(assessment.get('actions', []))}",
            )

        decision = self.ai.decide(portfolio_data, market_data, assessment)

        for action in decision.get("actions", []):
            action_type = action.get("type", "")

            if action_type == "emergency_halt":
                log.warning(f"EMERGENCY HALT FLAGGED: {action.get('reason')}")
                if self.mode == "live":
                    self.state.portfolio.circuit_breaker_active = True
                    self.state.add_event("emergency_halt_flagged", "ai", action)
                    return
                else:
                    self.state.add_event("emergency_halt_flagged_paper", "ai", action)

            elif action_type == "close_position":
                log.warning(f"RISK CLOSE FLAGGED (not auto-executing in live): {action}")
                self.state.add_event("risk_close_flagged", "ai", action)

            elif action_type == "preemptive_rebalance":
                sid = action.get("strategy")
                strategy = self.strategies.get(sid)
                if strategy:
                    await strategy.execute(
                        {"action": "rebalance", "position_id": action["position_id"]},
                        market_data,
                    )
                    self.state.add_event("preemptive_rebalance", sid, action)
                    log.info(f"Preemptive rebalance: {sid}: {action.get('reason')}")

            elif action_type == "activate_strategy":
                sid = action.get("strategy")
                strategy = self.strategies.get(sid)
                if strategy and not strategy.enabled:
                    strategy.enabled = True
                    alloc = self.guardian.get_optimal_allocation(market_data)
                    if sid in alloc:
                        strategy.target_allocation = alloc[sid]
                        strategy.capital_allocated = self.capital * alloc[sid]
                    self.state.add_event("activate", sid, action)
                    log.info(f"Activated dormant strategy: {sid}: {action.get('reason')}")

        if decision.get("leverage_override") is not None:
            for strategy in self.strategies.values():
                if hasattr(strategy, "base_leverage"):
                    strategy.base_leverage = min(
                        strategy.base_leverage, decision["leverage_override"]
                    )

        self.state.portfolio.circuit_breaker_active = False

        if decision.get("reasoning"):
            for r in decision["reasoning"][-3:]:
                log.info(f"AI: {r}")

    async def _emergency_exit(self, market_data: dict):
        log.warning("EMERGENCY EXIT: Closing all positions")
        for sid, strategy in self.strategies.items():
            for pos in strategy.active_positions:
                try:
                    await strategy.execute(
                        {"action": "deleverage" if hasattr(strategy, "base_leverage") else "close",
                         "position_id": pos.id},
                        market_data,
                    )
                    self.state.add_event("emergency_close", sid, {"position_id": pos.id})
                except Exception as e:
                    log.error(f"Emergency close failed for {sid}/{pos.id}: {e}")

    def _compute_total_equity(self) -> float:
        total = 0
        for s in self.strategies.values():
            if not s.enabled:
                continue
            state = s.get_state()
            net = state.get("metrics", {}).get("net_value")
            if net is not None:
                total += net
            else:
                total += s.current_value
        return total

    def _record_strategy_performance(self, market_data: dict):
        conditions = {
            "volatility": market_data.get("volatility_1h", 0),
            "trend": market_data.get("sol_change_24h", 0),
            "funding": market_data.get("funding_apy", 0),
        }
        for sid, strategy in self.strategies.items():
            if strategy.enabled and strategy.capital_allocated > 0:
                pnl_pct = strategy.total_pnl_percent
                self.ai.selector.record_performance(sid, pnl_pct, conditions)

    def _save_db_snapshot(self, market_data: dict):
        if not DATABASE_URL:
            return
        try:
            from server.persistence import SnapshotStore
            scalper = self.strategies.get("volatility_scalper")
            regime = ""
            volatility = 0.0
            indicators = {}
            if scalper and hasattr(scalper, "signal_engine"):
                regime = scalper.signal_engine.regime_detector.regime.value
                volatility = scalper.signal_engine.regime_detector.confidence
                indicators = scalper.signal_engine.get_indicator_snapshot()
            allocations = {sid: s.target_allocation for sid, s in self.strategies.items()}
            SnapshotStore.save(
                self.prices.sol_price, self._compute_total_equity(),
                self._compute_total_equity() - self.capital,
                regime, volatility, indicators, allocations,
            )
        except Exception as e:
            log.debug(f"DB snapshot failed: {e}")

    def _check_dynamic_allocation(self):
        scalper = self.strategies.get("volatility_scalper")
        if not scalper or not hasattr(scalper, "signal_engine"):
            return

        regime = scalper.signal_engine.regime_detector.regime
        regime_confidence = scalper.signal_engine.regime_detector.confidence

        funding_arb = self.strategies.get("funding_arb")
        funding_apy = 0
        if funding_arb and hasattr(funding_arb, "_best_funding_apy"):
            funding_apy = funding_arb._best_funding_apy

        volatility_2h = self.prices.get_market_data().get("volatility_1h", 0)

        current_allocs = {
            sid: s.target_allocation for sid, s in self.strategies.items()
            if s.enabled or s.capital_allocated > 0
        }

        new_allocs = self.allocator.should_rebalance(
            regime, current_allocs,
            regime_confidence=regime_confidence,
            funding_apy=funding_apy,
            volatility_2h=volatility_2h,
        )
        if new_allocs:
            log.info(f"Dynamic reallocation: regime={regime.value} conf={regime_confidence:.2f} funding={funding_apy:.1f}% vol={volatility_2h:.4f}")
            self._apply_allocations(new_allocs)
            self.state.add_event("dynamic_reallocation", "allocator", {
                "regime": regime.value,
                "allocations": new_allocs,
            })

    def _apply_allocations(self, allocations: dict):
        for sid, strategy in self.strategies.items():
            pct = allocations.get(sid, 0)
            strategy.target_allocation = pct
            strategy.capital_allocated = self.capital * pct

    def _save_all_states(self):
        for sid, strategy in self.strategies.items():
            self.state.set_strategy(sid, strategy.get_state())

    def toggle_strategy(self, strategy_id: str, enabled: bool) -> bool:
        strategy = self.strategies.get(strategy_id)
        if not strategy:
            return False
        strategy.enabled = enabled
        self._save_all_states()
        self.state.add_event("enable" if enabled else "disable", strategy_id, {})
        return True

    def update_allocation(self, allocations: dict):
        self._apply_allocations(allocations)
        self._save_all_states()
        self.state.add_event("reallocation", "orchestrator", allocations)

    def get_status(self) -> dict:
        total_equity = self._compute_total_equity()
        total_pnl = total_equity - self.capital
        pnl_pct = (total_pnl / self.capital * 100) if self.capital > 0 else 0

        uptime_hours = (time.time() - self.state.portfolio.started_at) / 3600
        projected_dpy = 0.0
        projected_mpy = 0.0
        projected_apy = 0.0

        oldest_position_age = 0.0
        for s in self.strategies.values():
            for pos in s.active_positions:
                oldest_position_age = max(oldest_position_age, pos.age_hours)

        effective_hours = oldest_position_age if oldest_position_age > 0.1 else uptime_hours
        if effective_hours > 0.1 and self.capital > 0:
            hourly_return = total_pnl / self.capital / effective_hours
            projected_dpy = hourly_return * 24 * 100
            projected_mpy = hourly_return * 730 * 100
            projected_apy = hourly_return * 8760 * 100

        total_fees = sum(s.total_fees for s in self.strategies.values())

        return {
            "mode": self.mode,
            "capital": self.capital,
            "total_value": total_equity,
            "total_pnl": total_pnl,
            "total_pnl_percent": pnl_pct,
            "total_fees": total_fees,
            "projected_dpy": projected_dpy,
            "projected_mpy": projected_mpy,
            "projected_apy": projected_apy,
            "risk_level": self.state.portfolio.risk_level,
            "circuit_breaker_active": self.state.portfolio.circuit_breaker_active,
            "sol_price": self.prices.sol_price,
            "strategies": {
                sid: s.get_state() for sid, s in self.strategies.items()
            },
            "uptime_hours": uptime_hours,
            "last_update": self.state.portfolio.last_update,
            "guardian": self.guardian._last_assessment,
            "ai_reasoning": self.ai.get_reasoning_summary(),
        }
