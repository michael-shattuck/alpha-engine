import logging
import time
import json
from dataclasses import dataclass

from server.ml.features import load_candles
from server.signals import indicators as ind

log = logging.getLogger("backtest")


@dataclass
class BacktestConfig:
    rsi_long_threshold: float = 35
    rsi_short_threshold: float = 65
    rsi_long_max: float = 55
    rsi_short_min: float = 45
    bb_entry_pct: float = 0.02
    velocity_confirm: float = 0.05
    rsi_turning_required: bool = True
    trend_confirm_candles: int = 2
    tp_pct: float = 0.008
    sl_pct: float = 0.004
    leverage: float = 3.0
    fee_pct: float = 0.001
    min_score: int = 4
    max_hold_candles: int = 30


def score_long(rsi: float, rsi_prev: float, bb_pos: float, velocity: float,
               accel: float, trend_up: bool, cfg: BacktestConfig) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    if rsi < cfg.rsi_long_threshold:
        score += 2
        reasons.append(f"RSI={rsi:.0f}<{cfg.rsi_long_threshold}")
    elif rsi < 40:
        score += 1
        reasons.append(f"RSI={rsi:.0f}<40")

    if rsi > cfg.rsi_long_max:
        return 0, [f"RSI={rsi:.0f}>{cfg.rsi_long_max} blocked"]

    if bb_pos < 0.2:
        score += 2
        reasons.append(f"BB={bb_pos:.2f} near lower")
    elif bb_pos < 0.35:
        score += 1
        reasons.append(f"BB={bb_pos:.2f} low")

    if cfg.rsi_turning_required and rsi > rsi_prev:
        score += 1
        reasons.append("RSI turning up")
    elif cfg.rsi_turning_required:
        score -= 1

    if velocity > cfg.velocity_confirm:
        score += 1
        reasons.append(f"vel={velocity:.2f}%")
    elif velocity < -0.2:
        score -= 1

    if accel > 0:
        score += 1
        reasons.append("accel+")

    if trend_up:
        score += 1
        reasons.append("trend confirms")

    return score, reasons


def score_short(rsi: float, rsi_prev: float, bb_pos: float, velocity: float,
                accel: float, trend_down: bool, cfg: BacktestConfig) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    if rsi > cfg.rsi_short_threshold:
        score += 2
        reasons.append(f"RSI={rsi:.0f}>{cfg.rsi_short_threshold}")
    elif rsi > 60:
        score += 1
        reasons.append(f"RSI={rsi:.0f}>60")

    if rsi < cfg.rsi_short_min:
        return 0, [f"RSI={rsi:.0f}<{cfg.rsi_short_min} blocked"]

    if bb_pos > 0.8:
        score += 2
        reasons.append(f"BB={bb_pos:.2f} near upper")
    elif bb_pos > 0.65:
        score += 1
        reasons.append(f"BB={bb_pos:.2f} high")

    if cfg.rsi_turning_required and rsi < rsi_prev:
        score += 1
        reasons.append("RSI turning down")
    elif cfg.rsi_turning_required:
        score -= 1

    if velocity < -cfg.velocity_confirm:
        score += 1
        reasons.append(f"vel={velocity:.2f}%")
    elif velocity > 0.2:
        score -= 1

    if accel < 0:
        score += 1
        reasons.append("accel-")

    if trend_down:
        score += 1
        reasons.append("trend confirms")

    return score, reasons


def run_backtest(asset: str, cfg: BacktestConfig = BacktestConfig(), candles: list[dict] | None = None) -> dict:
    if candles is None:
        candles = load_candles(asset, limit=200000)
    if len(candles) < 100:
        return {"error": f"Only {len(candles)} candles for {asset}"}

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    trades = []
    active_trade = None
    lookback = 30

    for i in range(lookback, len(candles) - cfg.max_hold_candles):
        price = closes[i]
        window = closes[i - lookback:i]

        rsi_val = ind.rsi(window)
        rsi_prev = ind.rsi(window[:-1]) if len(window) > 15 else rsi_val
        bb_lower, bb_middle, bb_upper = ind.bollinger_bands(window, 20)
        bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper > bb_lower else 0.5
        velocity = ind.price_velocity(window, 3)
        accel = ind.price_acceleration(window, 3)
        trend_up = len(window) >= cfg.trend_confirm_candles + 1 and window[-1] > window[-cfg.trend_confirm_candles - 1]
        trend_down = len(window) >= cfg.trend_confirm_candles + 1 and window[-1] < window[-cfg.trend_confirm_candles - 1]

        if active_trade:
            t = active_trade
            hold = i - t["entry_idx"]

            if t["direction"] == "long":
                cur_pnl = (price - t["entry_price"]) / t["entry_price"]
                hit_tp = price >= t["tp"]
                hit_sl = price <= t["sl"]
            else:
                cur_pnl = (t["entry_price"] - price) / t["entry_price"]
                hit_tp = price <= t["tp"]
                hit_sl = price >= t["sl"]

            if hit_tp or hit_sl or hold >= cfg.max_hold_candles:
                leveraged_pnl = cur_pnl * cfg.leverage
                fee = cfg.fee_pct * cfg.leverage
                net_pnl = leveraged_pnl - fee
                reason = "tp" if hit_tp else "sl" if hit_sl else "timeout"
                trades.append({
                    "direction": t["direction"],
                    "entry": t["entry_price"],
                    "exit": price,
                    "pnl_pct": net_pnl * 100,
                    "hold": hold,
                    "reason": reason,
                    "score": t["score"],
                })
                active_trade = None
            continue

        long_score, long_reasons = score_long(rsi_val, rsi_prev, bb_pos, velocity, accel, trend_up, cfg)
        short_score, short_reasons = score_short(rsi_val, rsi_prev, bb_pos, velocity, accel, trend_down, cfg)

        if long_score >= cfg.min_score and long_score > short_score:
            active_trade = {
                "direction": "long",
                "entry_price": price,
                "entry_idx": i,
                "tp": price * (1 + cfg.tp_pct),
                "sl": price * (1 - cfg.sl_pct),
                "score": long_score,
            }
        elif short_score >= cfg.min_score and short_score > long_score:
            active_trade = {
                "direction": "short",
                "entry_price": price,
                "entry_idx": i,
                "tp": price * (1 - cfg.tp_pct),
                "sl": price * (1 + cfg.sl_pct),
                "score": short_score,
            }

    if not trades:
        return {"trades": 0, "error": "no trades"}

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total_pnl = sum(t["pnl_pct"] for t in trades)
    avg_hold = sum(t["hold"] for t in trades) / len(trades)
    days = len(candles) / 1440

    return {
        "asset": asset,
        "candles": len(candles),
        "days": round(days, 1),
        "trades": len(trades),
        "trades_per_day": round(len(trades) / max(days, 1), 1),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 3),
        "avg_win": round(sum(t["pnl_pct"] for t in wins) / len(wins), 3) if wins else 0,
        "avg_loss": round(sum(t["pnl_pct"] for t in losses) / len(losses), 3) if losses else 0,
        "avg_hold": round(avg_hold, 1),
        "daily_pnl": round(total_pnl / max(days, 1), 2),
        "monthly_pnl": round(total_pnl / max(days, 1) * 30, 1),
        "config": {
            "min_score": cfg.min_score,
            "tp_pct": cfg.tp_pct,
            "sl_pct": cfg.sl_pct,
            "rsi_long": cfg.rsi_long_threshold,
            "rsi_short": cfg.rsi_short_threshold,
        },
        "by_reason": {
            "tp": len([t for t in trades if t["reason"] == "tp"]),
            "sl": len([t for t in trades if t["reason"] == "sl"]),
            "timeout": len([t for t in trades if t["reason"] == "timeout"]),
        },
    }


def optimize(asset: str) -> dict:
    candles = load_candles(asset, limit=200000)
    if len(candles) < 100:
        return {"error": f"Only {len(candles)} candles"}

    best = None
    best_daily = -999

    configs = []
    for min_score in [3, 4, 5, 6]:
        for tp in [0.005, 0.007, 0.010, 0.015]:
            for sl in [0.003, 0.004, 0.005, 0.007]:
                for rsi_l in [30, 35, 40]:
                    for rsi_s in [60, 65, 70]:
                        configs.append(BacktestConfig(
                            min_score=min_score,
                            tp_pct=tp,
                            sl_pct=sl,
                            rsi_long_threshold=rsi_l,
                            rsi_short_threshold=rsi_s,
                        ))

    log.info(f"Testing {len(configs)} configurations on {asset} ({len(candles)} candles)...")

    for i, cfg in enumerate(configs):
        result = run_backtest(asset, cfg, candles=candles)
        if result.get("trades", 0) < 10:
            continue

        wr = result.get("win_rate", 0)
        daily = result.get("daily_pnl", 0)

        if daily > best_daily and wr >= 55:
            best_daily = daily
            best = result

        if (i + 1) % 100 == 0:
            log.info(f"  {i+1}/{len(configs)} tested, best daily={best_daily:.2f}%")

    return best or {"error": "no profitable config found"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    for asset in ["JUP", "JTO"]:
        print(f"\n{'='*60}")
        print(f"BACKTESTING {asset}")
        print(f"{'='*60}")

        result = run_backtest(asset)
        print(f"Default config: {result.get('trades',0)} trades, {result.get('win_rate',0)}% WR, {result.get('daily_pnl',0)}%/day")

        print(f"\nOptimizing...")
        best = optimize(asset)
        if "error" not in best:
            print(f"\nBEST CONFIG for {asset}:")
            print(f"  Trades: {best['trades']} ({best['trades_per_day']}/day)")
            print(f"  Win rate: {best['win_rate']}%")
            print(f"  Avg win: {best['avg_win']}% | Avg loss: {best['avg_loss']}%")
            print(f"  Daily PnL: {best['daily_pnl']}% | Monthly: {best['monthly_pnl']}%")
            print(f"  Config: {best['config']}")
            print(f"  Exits: TP={best['by_reason']['tp']} SL={best['by_reason']['sl']} Timeout={best['by_reason']['timeout']}")
        else:
            print(f"  {best['error']}")
