import time
import math
import logging
from dataclasses import dataclass, field

log = logging.getLogger("guardian")


@dataclass
class DrawdownTracker:
    peak_equity: float = 0.0
    current_equity: float = 0.0
    max_drawdown: float = 0.0
    drawdown_start: float = 0.0
    recovery_mode: bool = False
    recovery_leverage_cap: float = 1.5

    def update(self, equity: float) -> float:
        self.current_equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity
            if self.recovery_mode:
                log.info("Exited recovery mode -- new equity high")
                self.recovery_mode = False

        drawdown = (self.peak_equity - equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

        if drawdown > 8.0 and not self.recovery_mode:
            self.recovery_mode = True
            self.drawdown_start = time.time()
            log.warning(f"Entering recovery mode: drawdown={drawdown:.1f}%")

        return drawdown


@dataclass
class PositionScaler:
    warmup_hours: float = 6.0
    max_position_pct: float = 1.0
    min_position_pct: float = 0.2

    def scale_factor(self, uptime_hours: float, drawdown_pct: float, volatility: float) -> float:
        time_factor = min(uptime_hours / self.warmup_hours, 1.0)

        drawdown_factor = 1.0
        if drawdown_pct > 5:
            drawdown_factor = max(1.0 - (drawdown_pct - 5) / 10, 0.3)

        vol_factor = 1.0
        if volatility > 0.04:
            vol_factor = 0.5
        elif volatility > 0.02:
            vol_factor = 0.75

        return max(
            time_factor * drawdown_factor * vol_factor * self.max_position_pct,
            self.min_position_pct,
        )


@dataclass
class StopLoss:
    position_stop_pct: float = 12.0
    daily_stop_pct: float = 8.0
    trailing_stop_pct: float = 15.0

    _daily_high: float = 0.0
    _daily_reset: float = 0.0
    _position_peaks: dict = field(default_factory=dict)

    def check_position_stop(self, position_id: str, equity: float, deposit: float) -> bool:
        loss_pct = (1 - equity / deposit) * 100 if deposit > 0 else 0
        return loss_pct > self.position_stop_pct

    def check_trailing_stop(self, position_id: str, current_equity: float) -> bool:
        peak = self._position_peaks.get(position_id, current_equity)
        if current_equity > peak:
            self._position_peaks[position_id] = current_equity
            return False

        drop = (peak - current_equity) / peak * 100 if peak > 0 else 0
        return drop > self.trailing_stop_pct

    def check_daily_stop(self, total_equity: float) -> bool:
        now = time.time()
        if now - self._daily_reset > 86400:
            self._daily_high = total_equity
            self._daily_reset = now

        if total_equity > self._daily_high:
            self._daily_high = total_equity

        daily_loss = (self._daily_high - total_equity) / self._daily_high * 100 if self._daily_high > 0 else 0
        return daily_loss > self.daily_stop_pct

    def update_peak(self, position_id: str, equity: float):
        current = self._position_peaks.get(position_id, 0)
        if equity > current:
            self._position_peaks[position_id] = equity


class Guardian:
    def __init__(self):
        self.drawdown = DrawdownTracker()
        self.scaler = PositionScaler()
        self.stop_loss = StopLoss()
        self._hourly_pnl: list[dict] = []
        self._last_assessment: dict = {}
        self._strategy_peaks: dict[str, float] = {}
        self._strategy_disabled_until: dict[str, float] = {}

    def assess(self, portfolio: dict, market_data: dict) -> dict:
        equity = portfolio.get("total_equity", 0)
        capital = portfolio.get("capital", 0)
        strategies = portfolio.get("strategies", {})
        uptime = portfolio.get("uptime_hours", 0)
        volatility = market_data.get("volatility_1h", 0)

        dd_pct = self.drawdown.update(equity)

        self._hourly_pnl.append({"ts": time.time(), "equity": equity})
        self._hourly_pnl = [p for p in self._hourly_pnl if time.time() - p["ts"] < 3600]

        hourly_loss = 0
        if len(self._hourly_pnl) >= 2:
            oldest = self._hourly_pnl[0]["equity"]
            hourly_loss = (oldest - equity) / oldest * 100 if oldest > 0 else 0

        actions = []
        risk_level = "low"
        leverage_cap = None

        if self.drawdown.recovery_mode:
            leverage_cap = self.drawdown.recovery_leverage_cap
            actions.append({
                "type": "cap_leverage",
                "max_leverage": leverage_cap,
                "reason": f"recovery_mode (drawdown={dd_pct:.1f}%)",
            })
            risk_level = "medium"

        scale = self.scaler.scale_factor(uptime, dd_pct, volatility)
        if scale < 0.9:
            actions.append({
                "type": "scale_position",
                "factor": scale,
                "reason": f"position_scaling (time={uptime:.1f}h dd={dd_pct:.1f}% vol={volatility:.4f})",
            })

        daily_stop = self.stop_loss.check_daily_stop(equity)
        if daily_stop:
            actions.append({
                "type": "halt_all",
                "reason": f"daily_stop_loss ({self.stop_loss.daily_stop_pct}% breached)",
            })
            risk_level = "critical"

        if hourly_loss > 5:
            actions.append({
                "type": "circuit_breaker",
                "reason": f"hourly_loss={hourly_loss:.1f}%",
            })
            risk_level = "critical"

        for sid, state in strategies.items():
            metrics = state.get("metrics", {})
            net_value = metrics.get("net_value", 0)
            eq = metrics.get("equity", state.get("capital_allocated", 0))

            if eq > 0:
                for pos in state.get("positions", []):
                    pid = pos.get("id", "")
                    pos_equity = pos.get("current_value_usd", 0) + pos.get("fees_earned_usd", 0)
                    borrowed = pos.get("metadata", {}).get("borrowed_usd", 0)
                    pos_net = pos_equity - borrowed

                    self.stop_loss.update_peak(pid, pos_net)

                    if self.stop_loss.check_position_stop(pid, pos_net, eq):
                        actions.append({
                            "type": "close_position",
                            "strategy": sid,
                            "position_id": pid,
                            "reason": f"position_stop_loss ({self.stop_loss.position_stop_pct}%)",
                        })
                        risk_level = max(risk_level, "high", key=["low", "medium", "high", "critical"].index)

                    if self.stop_loss.check_trailing_stop(pid, pos_net):
                        actions.append({
                            "type": "close_position",
                            "strategy": sid,
                            "position_id": pid,
                            "reason": f"trailing_stop ({self.stop_loss.trailing_stop_pct}%)",
                        })
                        risk_level = max(risk_level, "high", key=["low", "medium", "high", "critical"].index)

        now = time.time()
        for sid, state in strategies.items():
            strat_value = state.get("current_value", state.get("capital_allocated", 0))
            if strat_value <= 0:
                continue

            peak = self._strategy_peaks.get(sid, strat_value)
            self._strategy_peaks[sid] = max(peak, strat_value)
            strat_dd = (self._strategy_peaks[sid] - strat_value) / self._strategy_peaks[sid] * 100 if self._strategy_peaks[sid] > 0 else 0

            disabled_until = self._strategy_disabled_until.get(sid, 0)
            if disabled_until > now:
                actions.append({
                    "type": "disable_strategy",
                    "strategy": sid,
                    "reason": f"drawdown_cooldown (until {int(disabled_until - now)}s)",
                })
                continue

            if strat_dd > 15:
                self._strategy_disabled_until[sid] = now + 14400
                actions.append({
                    "type": "disable_strategy",
                    "strategy": sid,
                    "reason": f"strategy_drawdown_{strat_dd:.1f}%_disable_4h",
                })
                risk_level = max(risk_level, "high", key=["low", "medium", "high", "critical"].index)
            elif strat_dd > 10:
                actions.append({
                    "type": "halve_allocation",
                    "strategy": sid,
                    "reason": f"strategy_drawdown_{strat_dd:.1f}%_halve",
                })
                risk_level = max(risk_level, "medium", key=["low", "medium", "high", "critical"].index)

        if dd_pct > 15:
            risk_level = "critical"
        elif dd_pct > 10:
            risk_level = max(risk_level, "high", key=["low", "medium", "high", "critical"].index)
        elif dd_pct > 5:
            risk_level = max(risk_level, "medium", key=["low", "medium", "high", "critical"].index)

        self._last_assessment = {
            "risk_level": risk_level,
            "drawdown_pct": dd_pct,
            "max_drawdown": self.drawdown.max_drawdown,
            "recovery_mode": self.drawdown.recovery_mode,
            "position_scale": scale,
            "leverage_cap": leverage_cap,
            "hourly_loss_pct": hourly_loss,
            "volatility": volatility,
            "actions": actions,
            "timestamp": time.time(),
        }

        return self._last_assessment

    def get_optimal_leverage(self, base_leverage: float, volatility: float) -> float:
        lev = base_leverage

        if volatility > 0.04:
            lev = min(lev, 1.5)
        elif volatility > 0.02:
            lev = min(lev, 2.0)

        if self.drawdown.recovery_mode:
            lev = min(lev, self.drawdown.recovery_leverage_cap)

        scale = self.scaler.scale_factor(
            uptime_hours=999,
            drawdown_pct=self.drawdown.max_drawdown,
            volatility=volatility,
        )
        lev *= scale

        return max(lev, 1.0)

    def get_optimal_allocation(self, market_data: dict) -> dict:
        vol = market_data.get("volatility_1h", 0)
        sol_change = market_data.get("sol_change_24h", 0)
        funding = market_data.get("funding_apy", 0)

        if self.drawdown.recovery_mode:
            return {
                "leveraged_lp": 0.60,
                "volatile_pairs": 0.10,
                "adaptive_range": 0.20,
                "funding_arb": 0.10,
            }

        if vol > 0.05:
            return {
                "leveraged_lp": 0.40,
                "volatile_pairs": 0.10,
                "adaptive_range": 0.30,
                "funding_arb": 0.20 if funding > 15 else 0.0,
            }

        if abs(sol_change) < 2:
            return {
                "leveraged_lp": 0.80,
                "volatile_pairs": 0.20,
            }

        return {
            "leveraged_lp": 0.70,
            "volatile_pairs": 0.15,
            "adaptive_range": 0.15,
        }
