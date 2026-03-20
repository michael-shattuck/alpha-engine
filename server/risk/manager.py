import time
from collections import deque

from server.config import RISK_LIMITS


class RiskManager:

    def __init__(self, risk_limits: dict = None):
        self.limits = risk_limits or RISK_LIMITS
        self.consecutive_losses = 0
        self.peak_value = 0.0
        self.last_circuit_breaker_time = 0.0
        self.risk_history: deque = deque(maxlen=100)
        self._loss_window: deque = deque(maxlen=1000)

    def evaluate(self, portfolio_state: dict, market_data: dict, signals: dict) -> dict:
        reasons = []
        actions = []

        drawdown_exceeded, current_drawdown = self.check_drawdown(portfolio_state)
        if drawdown_exceeded:
            reasons.append(f"Drawdown at {current_drawdown:.2f}% exceeds limit of {self.limits['max_drawdown_percent']}%")
            actions.append("scale_down")

        cb_triggered, cb_reason = self.check_circuit_breaker(portfolio_state, market_data)
        if cb_triggered:
            reasons.append(cb_reason)
            actions.append("halt_all")

        concentration_issues = self.check_concentration(portfolio_state)
        for issue in concentration_issues:
            reasons.append(f"Strategy '{issue['strategy']}' at {issue['current_allocation']:.1%} exceeds max {self.limits['max_single_strategy_allocation']:.1%}")
            actions.append("rebalance")

        sol_crash, sol_change = self.check_sol_crash(market_data)
        if sol_crash:
            reasons.append(f"SOL crashed {sol_change:.1f}% in 24h (threshold: {self.limits['sol_crash_threshold_percent']}%)")
            actions.append("emergency_exit")

        risk_score = signals.get("risk_score", 0.0)
        risk_level = self.get_risk_level(risk_score)

        if risk_level == "high" and "scale_down" not in actions:
            actions.append("scale_down")
            reasons.append(f"Risk score elevated at {risk_score:.1f}")

        if risk_level == "critical" and "halt_all" not in actions:
            actions.append("halt_all")
            reasons.append(f"Risk score critical at {risk_score:.1f}")

        drift = self._check_allocation_drift(portfolio_state)
        if drift and "rebalance" not in actions:
            actions.append("rebalance")
            reasons.append("Allocation drift exceeds threshold")

        assessment = {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "circuit_breaker": cb_triggered,
            "actions": list(set(actions)),
            "reasons": reasons,
            "drawdown_percent": current_drawdown,
            "consecutive_losses": self.consecutive_losses,
            "timestamp": time.time(),
        }

        self.risk_history.append(assessment)
        return assessment

    def check_drawdown(self, portfolio_state: dict) -> tuple[bool, float]:
        current_value = portfolio_state.get("total_value", 0.0)

        if current_value > self.peak_value:
            self.peak_value = current_value

        if self.peak_value <= 0:
            return False, 0.0

        drawdown = ((self.peak_value - current_value) / self.peak_value) * 100.0
        exceeded = drawdown >= self.limits["max_drawdown_percent"]
        return exceeded, drawdown

    def check_circuit_breaker(self, portfolio_state: dict, market_data: dict) -> tuple[bool, str]:
        now = time.time()
        window_seconds = self.limits["circuit_breaker_window_hours"] * 3600
        threshold = self.limits["circuit_breaker_loss_percent"]

        current_value = portfolio_state.get("total_value", 0.0)
        self._loss_window.append((now, current_value))

        while self._loss_window and (now - self._loss_window[0][0]) > window_seconds:
            self._loss_window.popleft()

        if not self._loss_window:
            return False, ""

        window_start_value = self._loss_window[0][1]
        if window_start_value <= 0:
            return False, ""

        loss_percent = ((window_start_value - current_value) / window_start_value) * 100.0

        if loss_percent >= threshold:
            self.last_circuit_breaker_time = now
            return True, f"Lost {loss_percent:.2f}% in {self.limits['circuit_breaker_window_hours']}h window (limit: {threshold}%)"

        return False, ""

    def check_concentration(self, portfolio_state: dict) -> list[dict]:
        issues = []
        strategies = portfolio_state.get("strategies", {})
        total_value = portfolio_state.get("total_value", 0.0)

        if total_value <= 0:
            return issues

        max_allocation = self.limits["max_single_strategy_allocation"]

        for strategy_id, strategy_state in strategies.items():
            strategy_value = strategy_state.get("current_value", 0.0)
            allocation = strategy_value / total_value

            if allocation > max_allocation:
                issues.append({
                    "strategy": strategy_id,
                    "current_allocation": allocation,
                    "max_allocation": max_allocation,
                    "action": "reduce",
                })

        return issues

    def check_sol_crash(self, market_data: dict) -> tuple[bool, float]:
        sol_change_24h = market_data.get("price_change_24h", 0.0)
        threshold = self.limits["sol_crash_threshold_percent"]
        crashed = sol_change_24h <= threshold
        return crashed, sol_change_24h

    def calculate_position_sizes(self, capital: float, signals: dict) -> dict:
        risk_score = signals.get("risk_score", 50.0)
        allocation = signals.get("recommended_allocation", {})

        if risk_score >= 75:
            scale_factor = 0.25
        elif risk_score >= 50:
            scale_factor = 0.50
        elif risk_score >= 25:
            scale_factor = 0.75
        else:
            scale_factor = 1.0

        deployable_capital = capital * scale_factor
        reserve_capital = capital - deployable_capital

        position_sizes = {}
        for strategy, weight in allocation.items():
            position_sizes[strategy] = round(deployable_capital * weight, 2)

        position_sizes["reserve"] = round(reserve_capital, 2)
        position_sizes["scale_factor"] = scale_factor
        position_sizes["deployable_capital"] = round(deployable_capital, 2)

        return position_sizes

    def get_risk_level(self, risk_score: float) -> str:
        if risk_score < 25:
            return "low"
        elif risk_score < 50:
            return "medium"
        elif risk_score < 75:
            return "high"
        return "critical"

    def record_trade_result(self, pnl: float):
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def _check_allocation_drift(self, portfolio_state: dict) -> bool:
        strategies = portfolio_state.get("strategies", {})
        total_value = portfolio_state.get("total_value", 0.0)

        if total_value <= 0:
            return False

        drift_threshold = self.limits["rebalance_drift_threshold"]

        for strategy_id, strategy_state in strategies.items():
            current_allocation = strategy_state.get("current_value", 0.0) / total_value
            target_allocation = strategy_state.get("target_allocation", 0.0)
            drift = abs(current_allocation - target_allocation)

            if drift > drift_threshold:
                return True

        return False
