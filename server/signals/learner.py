import time
import logging
import json
from dataclasses import dataclass, field, asdict

log = logging.getLogger("learner")

DECAY_RATE = 0.95
REGRET_LOOKBACK = 900
MIN_TRADES_FOR_ADAPTATION = 3
MAX_PAIN = 3.0
MAX_REGRET = 3.0


@dataclass
class AssetProfile:
    asset: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0

    pain: float = 0.0
    regret: float = 0.0

    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_hold_seconds: float = 0.0
    avg_win_hold: float = 0.0

    long_wins: int = 0
    long_losses: int = 0
    short_wins: int = 0
    short_losses: int = 0

    trend_wins: int = 0
    trend_losses: int = 0
    mr_wins: int = 0
    mr_losses: int = 0

    tp_hits: int = 0
    sl_hits: int = 0
    time_exits: int = 0
    trailing_exits: int = 0

    near_tp_misses: int = 0
    regret_signals: int = 0

    confidence_adjustment: float = 0.0
    size_multiplier: float = 1.0
    tp_multiplier: float = 1.0
    sl_multiplier: float = 1.0
    short_penalty: float = 0.0

    last_updated: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / max(self.trades, 1)

    @property
    def long_wr(self) -> float:
        total = self.long_wins + self.long_losses
        return self.long_wins / max(total, 1)

    @property
    def short_wr(self) -> float:
        total = self.short_wins + self.short_losses
        return self.short_wins / max(total, 1)

    @property
    def trend_wr(self) -> float:
        total = self.trend_wins + self.trend_losses
        return self.trend_wins / max(total, 1)

    @property
    def mr_wr(self) -> float:
        total = self.mr_wins + self.mr_losses
        return self.mr_wins / max(total, 1)


class TradeLearner:
    def __init__(self):
        self.profiles: dict[str, AssetProfile] = {}
        self._pending_signals: dict[str, dict] = {}

    def get_profile(self, asset: str) -> AssetProfile:
        if asset not in self.profiles:
            self.profiles[asset] = AssetProfile(asset=asset)
        return self.profiles[asset]

    def record_trade_close(self, trade: dict):
        asset = trade.get("asset", "SOL")
        p = self.get_profile(asset)
        now = time.time()

        pnl_pct = trade.get("pnl_pct", 0)
        pnl_usd = trade.get("pnl_usd", 0)
        direction = trade.get("direction", "long")
        trade_type = trade.get("trade_type", "trend_follow")
        exit_reason = trade.get("exit_reason", "")
        hold_time = trade.get("closed_at", now) - trade.get("opened_at", now)
        won = pnl_usd > 0

        p.trades += 1

        if won:
            p.wins += 1
            p.consecutive_wins += 1
            p.consecutive_losses = 0
            p.avg_win_pct = (p.avg_win_pct * (p.wins - 1) + pnl_pct) / p.wins
            p.avg_win_hold = (p.avg_win_hold * (p.wins - 1) + hold_time) / p.wins
        else:
            p.losses += 1
            p.consecutive_losses += 1
            p.consecutive_wins = 0
            p.avg_loss_pct = (p.avg_loss_pct * (p.losses - 1) + abs(pnl_pct)) / p.losses

        p.avg_hold_seconds = (p.avg_hold_seconds * (p.trades - 1) + hold_time) / p.trades

        if direction == "long":
            if won:
                p.long_wins += 1
            else:
                p.long_losses += 1
        else:
            if won:
                p.short_wins += 1
            else:
                p.short_losses += 1

        if trade_type == "trend_follow":
            if won:
                p.trend_wins += 1
            else:
                p.trend_losses += 1
        elif trade_type == "mean_reversion":
            if won:
                p.mr_wins += 1
            else:
                p.mr_losses += 1

        if "take_profit" in exit_reason:
            p.tp_hits += 1
        elif "stop_loss" in exit_reason:
            p.sl_hits += 1
        elif "trailing" in exit_reason:
            p.trailing_exits += 1
        elif "time_exit" in exit_reason:
            p.time_exits += 1

        self._update_pain(p, trade)
        self._update_adaptations(p)
        p.last_updated = now

        log.info(
            f"Learner [{asset}]: trades={p.trades} WR={p.win_rate:.0%} "
            f"pain={p.pain:.2f} regret={p.regret:.2f} "
            f"size_mult={p.size_multiplier:.2f} conf_adj={p.confidence_adjustment:+.2f} "
            f"tp_mult={p.tp_multiplier:.2f} short_pen={p.short_penalty:.2f}"
        )

    def record_skipped_signal(self, asset: str, direction: str, entry_price: float, tp_price: float):
        self._pending_signals[f"{asset}_{direction}_{time.time():.0f}"] = {
            "asset": asset,
            "direction": direction,
            "entry_price": entry_price,
            "tp_price": tp_price,
            "timestamp": time.time(),
        }

    def check_regret(self, asset: str, current_price: float):
        now = time.time()
        expired = []
        for key, sig in self._pending_signals.items():
            if sig["asset"] != asset:
                continue
            age = now - sig["timestamp"]
            if age > REGRET_LOOKBACK:
                expired.append(key)
                continue

            entry = sig["entry_price"]
            tp = sig["tp_price"]

            if sig["direction"] == "long" and current_price >= tp:
                p = self.get_profile(asset)
                p.regret_signals += 1
                p.regret = min(p.regret + 0.5, MAX_REGRET)
                self._update_adaptations(p)
                expired.append(key)
                log.info(f"Learner [{asset}]: REGRET long skipped at {entry:.6f}, would have hit TP at {current_price:.6f}")
            elif sig["direction"] == "short" and current_price <= tp:
                p = self.get_profile(asset)
                p.regret_signals += 1
                p.regret = min(p.regret + 0.5, MAX_REGRET)
                self._update_adaptations(p)
                expired.append(key)
                log.info(f"Learner [{asset}]: REGRET short skipped at {entry:.6f}, would have hit TP at {current_price:.6f}")

        for key in expired:
            self._pending_signals.pop(key, None)

    def _update_pain(self, p: AssetProfile, trade: dict):
        pnl_usd = trade.get("pnl_usd", 0)
        exit_reason = trade.get("exit_reason", "")

        p.pain *= DECAY_RATE

        if pnl_usd < 0:
            severity = abs(trade.get("pnl_pct", 0)) / 3.0
            p.pain = min(p.pain + severity, MAX_PAIN)

            if "stop_loss" in exit_reason:
                p.pain = min(p.pain + 0.3, MAX_PAIN)
            if p.consecutive_losses >= 3:
                p.pain = min(p.pain + 0.5, MAX_PAIN)
        else:
            p.pain = max(p.pain - 0.3, 0)
            p.regret *= DECAY_RATE

        entry = trade.get("entry_price", 0)
        tp = trade.get("take_profit", 0)
        exit_price = trade.get("exit_price", trade.get("current_price", 0))
        if "time_exit" in exit_reason and entry > 0 and tp > 0:
            if trade["direction"] == "long":
                progress = (exit_price - entry) / (tp - entry) if tp != entry else 0
            else:
                progress = (entry - exit_price) / (entry - tp) if tp != entry else 0
            if progress > 0.7:
                p.near_tp_misses += 1

    def _update_adaptations(self, p: AssetProfile):
        if p.trades < MIN_TRADES_FOR_ADAPTATION:
            return

        p.confidence_adjustment = (p.pain * 0.05) - (p.regret * 0.03)
        p.confidence_adjustment = max(-0.15, min(p.confidence_adjustment, 0.15))

        if p.consecutive_losses >= 3:
            p.size_multiplier = 0.5
        elif p.consecutive_losses >= 2:
            p.size_multiplier = 0.7
        elif p.consecutive_wins >= 3:
            p.size_multiplier = min(1.3, 1.0 + p.consecutive_wins * 0.1)
        else:
            p.size_multiplier = max(0.7, min(1.2, 1.0 - (p.pain * 0.1) + (p.regret * 0.05)))

        if p.near_tp_misses >= 3 and p.tp_hits < p.near_tp_misses:
            p.tp_multiplier = 0.85
        elif p.tp_hits > p.time_exits and p.tp_hits >= 3:
            p.tp_multiplier = 1.15
        else:
            p.tp_multiplier = 1.0

        if p.sl_hits >= 3 and p.sl_hits > p.tp_hits:
            p.sl_multiplier = 0.85
        else:
            p.sl_multiplier = 1.0

        short_total = p.short_wins + p.short_losses
        if short_total >= 3 and p.short_wr < 0.3:
            p.short_penalty = 0.3
        elif short_total >= 5 and p.short_wr < 0.4:
            p.short_penalty = 0.2
        else:
            p.short_penalty = 0.0

    def get_entry_filter(self, asset: str, direction: str, confidence: float, trade_type: str) -> tuple[bool, float, str]:
        p = self.get_profile(asset)
        if p.trades < MIN_TRADES_FOR_ADAPTATION:
            return True, confidence, "no history"

        adjusted_confidence = confidence - p.confidence_adjustment

        if direction == "short" and p.short_penalty > 0:
            adjusted_confidence -= p.short_penalty

        if trade_type == "trend_follow" and p.trend_losses > p.trend_wins and (p.trend_wins + p.trend_losses) >= 3:
            adjusted_confidence -= 0.05
        if trade_type == "mean_reversion" and p.mr_losses > p.mr_wins and (p.mr_wins + p.mr_losses) >= 3:
            adjusted_confidence -= 0.05

        from server.signals.engine import SignalEngine
        min_conf = SignalEngine.MIN_CONFIDENCE
        if adjusted_confidence < min_conf:
            reason = f"filtered: conf {confidence:.2f}->{adjusted_confidence:.2f} (pain={p.pain:.1f} streak={p.consecutive_losses}L)"
            return False, adjusted_confidence, reason

        return True, adjusted_confidence, f"pain={p.pain:.1f} regret={p.regret:.1f}"

    def get_size_multiplier(self, asset: str) -> float:
        return self.get_profile(asset).size_multiplier

    def get_tp_sl_multipliers(self, asset: str) -> tuple[float, float]:
        p = self.get_profile(asset)
        return p.tp_multiplier, p.sl_multiplier

    def get_state(self) -> dict:
        return {
            "profiles": {a: asdict(p) for a, p in self.profiles.items()},
            "pending_signals": dict(self._pending_signals),
        }

    def load_state(self, state: dict):
        for asset, pdata in state.get("profiles", {}).items():
            self.profiles[asset] = AssetProfile(**{k: v for k, v in pdata.items() if k in AssetProfile.__dataclass_fields__})
        self._pending_signals = state.get("pending_signals", {})
        for asset, p in self.profiles.items():
            log.info(f"Loaded [{asset}]: trades={p.trades} WR={p.win_rate:.0%} pain={p.pain:.2f} regret={p.regret:.2f}")
