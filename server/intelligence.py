import time
import math
import logging

log = logging.getLogger("intelligence")


class RebalanceIntelligence:
    def __init__(self):
        self._price_history: list[dict] = []
        self._rebalance_log: list[dict] = []

    def record_price(self, price: float):
        self._price_history.append({"ts": time.time(), "p": price})
        cutoff = time.time() - 86400
        self._price_history = [h for h in self._price_history if h["ts"] > cutoff]

    def should_preemptive_rebalance(
        self, current_price: float, lower: float, upper: float, entry_price: float
    ) -> tuple[bool, str]:
        if len(self._price_history) < 6:
            return False, ""

        recent = self._price_history[-6:]
        prices = [h["p"] for h in recent]

        velocity = (prices[-1] - prices[0]) / prices[0] * 100
        acceleration = 0
        if len(prices) >= 3:
            v1 = prices[len(prices) // 2] - prices[0]
            v2 = prices[-1] - prices[len(prices) // 2]
            acceleration = (v2 - v1) / max(abs(v1), 0.01)

        range_width = upper - lower
        distance_to_lower = (current_price - lower) / range_width
        distance_to_upper = (upper - current_price) / range_width

        if distance_to_lower < 0.15 and velocity < -0.3:
            return True, f"approaching_lower (dist={distance_to_lower:.1%}, vel={velocity:+.2f}%)"

        if distance_to_upper < 0.15 and velocity > 0.3:
            return True, f"approaching_upper (dist={distance_to_upper:.1%}, vel={velocity:+.2f}%)"

        if abs(velocity) > 1.0 and abs(acceleration) > 2.0:
            return True, f"momentum_surge (vel={velocity:+.2f}%, accel={acceleration:+.1f})"

        return False, ""

    def rebalance_profitable(
        self, pool_apy: float, range_pct: float, concentration: float, deposit: float, cost_pct: float = 0.0008
    ) -> tuple[bool, float]:
        cost = deposit * cost_pct
        hourly_fee_income = deposit * (pool_apy / 100 / 365 / 24) * concentration
        hours_to_recover = cost / hourly_fee_income if hourly_fee_income > 0 else float("inf")
        return hours_to_recover < 4, hours_to_recover

    def optimal_rebalance_timing(self, volatility: float) -> str:
        if volatility > 0.05:
            return "wait"
        if volatility > 0.03:
            return "cautious"
        return "proceed"

    def record_rebalance(self, price: float, cost: float, reason: str):
        self._rebalance_log.append({
            "ts": time.time(),
            "price": price,
            "cost": cost,
            "reason": reason,
        })
        self._rebalance_log = self._rebalance_log[-500:]

    def rebalance_frequency(self, hours: float = 24) -> float:
        cutoff = time.time() - hours * 3600
        recent = [r for r in self._rebalance_log if r["ts"] > cutoff]
        return len(recent) / max(hours / 24, 0.01)

    def total_rebalance_cost(self, hours: float = 24) -> float:
        cutoff = time.time() - hours * 3600
        return sum(r["cost"] for r in self._rebalance_log if r["ts"] > cutoff)


class StrategySelector:
    def __init__(self):
        self._performance_log: list[dict] = []

    def record_performance(self, strategy_id: str, pnl_pct: float, market_conditions: dict):
        self._performance_log.append({
            "ts": time.time(),
            "strategy": strategy_id,
            "pnl_pct": pnl_pct,
            "conditions": market_conditions,
        })
        self._performance_log = self._performance_log[-2000:]

    def score_strategy(self, strategy_id: str, current_conditions: dict) -> float:
        relevant = [
            p for p in self._performance_log
            if p["strategy"] == strategy_id
        ]

        if len(relevant) < 5:
            return 50.0

        weighted_scores = []
        for entry in relevant[-50:]:
            similarity = self._condition_similarity(entry["conditions"], current_conditions)
            weighted_scores.append(entry["pnl_pct"] * similarity)

        if not weighted_scores:
            return 50.0

        avg = sum(weighted_scores) / len(weighted_scores)
        return max(min(avg * 10 + 50, 100), 0)

    def rank_strategies(self, strategy_ids: list[str], market_data: dict) -> list[tuple[str, float]]:
        conditions = {
            "volatility": market_data.get("volatility_1h", 0),
            "trend": market_data.get("sol_change_24h", 0),
            "funding": market_data.get("funding_apy", 0),
        }
        scores = [(sid, self.score_strategy(sid, conditions)) for sid in strategy_ids]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def _condition_similarity(self, a: dict, b: dict) -> float:
        if not a or not b:
            return 0.5
        diffs = []
        for key in a:
            if key in b:
                va, vb = float(a[key] or 0), float(b[key] or 0)
                denom = max(abs(va), abs(vb), 0.01)
                diffs.append(1 - min(abs(va - vb) / denom, 1))
        return sum(diffs) / len(diffs) if diffs else 0.5


class AIOrchestrator:
    def __init__(self):
        self.rebalancer = RebalanceIntelligence()
        self.selector = StrategySelector()
        self._decisions: list[dict] = []

    def decide(self, portfolio: dict, market_data: dict, guardian_assessment: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        self.rebalancer.record_price(sol_price)

        decision = {
            "timestamp": time.time(),
            "actions": [],
            "allocation_change": None,
            "leverage_override": None,
            "reasoning": [],
        }

        if guardian_assessment.get("risk_level") == "critical":
            decision["actions"].append({"type": "emergency_halt", "reason": "guardian_critical"})
            decision["reasoning"].append("Guardian flagged critical risk -- halting all activity")
            self._decisions.append(decision)
            return decision

        for action in guardian_assessment.get("actions", []):
            if action["type"] == "cap_leverage":
                decision["leverage_override"] = action["max_leverage"]
                decision["reasoning"].append(f"Leverage capped to {action['max_leverage']}x: {action['reason']}")
            elif action["type"] == "scale_position":
                decision["reasoning"].append(f"Position scale: {action['factor']:.0%}")
            elif action["type"] in ("halt_all", "circuit_breaker"):
                decision["actions"].append({"type": "emergency_halt", "reason": action["reason"]})
                decision["reasoning"].append(f"Emergency halt: {action['reason']}")
            elif action["type"] == "close_position":
                decision["actions"].append(action)
                decision["reasoning"].append(f"Closing {action['position_id']}: {action['reason']}")

        strategies = portfolio.get("strategies", {})
        for sid, state in strategies.items():
            if not state.get("enabled"):
                continue
            for pos in state.get("positions", []):
                lower = pos.get("lower_price", 0)
                upper = pos.get("upper_price", 0)
                entry = pos.get("entry_price", 0)
                if lower > 0 and upper > 0 and entry > 0:
                    should, reason = self.rebalancer.should_preemptive_rebalance(
                        sol_price, lower, upper, entry
                    )
                    if should:
                        timing = self.rebalancer.optimal_rebalance_timing(
                            market_data.get("volatility_1h", 0)
                        )
                        if timing == "wait":
                            decision["reasoning"].append(
                                f"Preemptive rebalance suggested for {sid} ({reason}) but vol too high -- waiting"
                            )
                        else:
                            decision["actions"].append({
                                "type": "preemptive_rebalance",
                                "strategy": sid,
                                "position_id": pos.get("id"),
                                "reason": reason,
                            })
                            decision["reasoning"].append(f"Preemptive rebalance: {reason}")

        rebal_freq = self.rebalancer.rebalance_frequency(24)
        if rebal_freq > 8:
            decision["reasoning"].append(
                f"High rebalance frequency ({rebal_freq:.0f}/day) -- consider widening range"
            )
            current_vol = market_data.get("volatility_1h", 0)
            if current_vol < 0.02:
                decision["reasoning"].append("But volatility is low -- may be a choppy market")

        vol = market_data.get("volatility_1h", 0)
        funding = market_data.get("funding_apy", 0)

        dormant_should_activate = []
        if funding > 20:
            dormant_should_activate.append(("funding_arb", f"funding_apy={funding:.0f}%"))
        if vol > 0.04 and guardian_assessment.get("recovery_mode"):
            dormant_should_activate.append(("adaptive_range", f"high_vol={vol:.3f} + recovery_mode"))

        for sid, reason in dormant_should_activate:
            if sid not in strategies or not strategies.get(sid, {}).get("enabled"):
                decision["actions"].append({
                    "type": "activate_strategy",
                    "strategy": sid,
                    "reason": reason,
                })
                decision["reasoning"].append(f"Activate {sid}: {reason}")

        if len(self._decisions) >= 10:
            strategy_ids = list(strategies.keys())
            rankings = self.selector.rank_strategies(strategy_ids, market_data)
            top = rankings[0] if rankings else None
            if top and top[1] > 70:
                decision["reasoning"].append(
                    f"Top performing strategy in current conditions: {top[0]} (score={top[1]:.0f})"
                )

        self._decisions.append(decision)
        if len(self._decisions) > 500:
            self._decisions = self._decisions[-500:]

        return decision

    def get_recent_decisions(self, limit: int = 20) -> list[dict]:
        return self._decisions[-limit:]

    def get_reasoning_summary(self) -> list[str]:
        if not self._decisions:
            return []
        return self._decisions[-1].get("reasoning", [])
