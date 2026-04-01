import logging
import time
from dataclasses import dataclass, field

from server.ml.features import load_candles
from server.signals import indicators as ind

log = logging.getLogger("backtest_v2")


@dataclass
class StrategyConfig:
    trend_tp: float = 0.01
    trend_sl: float = 0.007
    trend_max_hold: int = 45
    trend_trailing: float = 0.015

    mr_tp: float = 0.007
    mr_sl: float = 0.003
    mr_max_hold: int = 15
    mr_trailing: float = 0.015

    mr_bb_entry: float = 0.20
    mr_rsi_long_max: float = 45
    mr_rsi_short_min: float = 55

    trend_velocity_min: float = -0.1
    trend_rsi_long_min: float = 40
    trend_rsi_long_max: float = 65
    trend_rsi_short_min: float = 35
    trend_rsi_short_max: float = 60

    adx_trend_threshold: float = 20
    adx_short_min: float = 25

    leverage: float = 3.0
    fee_per_side: float = 0.00035

    sol_trend_filter: bool = True


def aggregate_to_timeframe(candles_1m: list[dict], minutes: int) -> list[dict]:
    result = []
    for i in range(0, len(candles_1m) - minutes + 1, minutes):
        chunk = candles_1m[i:i + minutes]
        if not chunk:
            continue
        result.append({
            "timestamp": chunk[0]["timestamp"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(c.get("volume", 0) for c in chunk),
        })
    return result


def detect_regime(adx: float, plus_di: float, minus_di: float, bbw: float,
                  ema_9: float, ema_21: float, cfg: StrategyConfig) -> str:
    if adx >= cfg.adx_trend_threshold:
        if plus_di > minus_di:
            if ema_9 > 0 and ema_21 > 0 and ema_9 < ema_21:
                return "volatile_ranging"
            return "trending_up"
        else:
            if ema_9 > 0 and ema_21 > 0 and ema_9 > ema_21:
                return "volatile_ranging"
            return "trending_down"
    if bbw < 0.02:
        return "dead"
    if bbw < 0.04:
        return "ranging"
    return "volatile_ranging"


def run_backtest(asset: str, cfg: StrategyConfig = StrategyConfig(),
                 candles_1m: list[dict] | None = None,
                 sol_candles_1m: list[dict] | None = None) -> dict:
    if candles_1m is None:
        candles_1m = load_candles(asset, limit=200000)
    if len(candles_1m) < 500:
        return {"error": f"Only {len(candles_1m)} candles"}

    candles_15m = aggregate_to_timeframe(candles_1m, 15)
    candles_1h = aggregate_to_timeframe(candles_1m, 60)

    sol_15m = None
    if sol_candles_1m and cfg.sol_trend_filter:
        sol_15m = aggregate_to_timeframe(sol_candles_1m, 15)

    trades = []
    active = None
    min_bars = 30

    for i in range(min_bars, len(candles_15m)):
        price = candles_15m[i]["close"]
        closes = [c["close"] for c in candles_15m[max(0, i - min_bars):i + 1]]
        if len(closes) < 22:
            continue

        h1_idx = i * 15 // 60
        if h1_idx < 14 or h1_idx >= len(candles_1h):
            continue
        closes_1h = [c["close"] for c in candles_1h[max(0, h1_idx - 30):h1_idx + 1]]
        highs_1h = [c["high"] for c in candles_1h[max(0, h1_idx - 30):h1_idx + 1]]
        lows_1h = [c["low"] for c in candles_1h[max(0, h1_idx - 30):h1_idx + 1]]

        if len(closes_1h) < 14:
            continue

        adx_val, plus_di, minus_di = ind.adx_with_di(highs_1h, lows_1h, closes_1h)
        bbw = ind.bollinger_band_width(closes, 20)
        ema_9 = ind.ema(closes, 9)
        ema_21 = ind.ema(closes, 21)
        rsi = ind.rsi(closes)
        rsi_prev = ind.rsi(closes[:-1]) if len(closes) > 16 else rsi
        velocity = ind.price_velocity(closes, 3)
        bb_lower, bb_middle, bb_upper = ind.bollinger_bands(closes)

        regime = detect_regime(adx_val, plus_di, minus_di, bbw, ema_9, ema_21, cfg)

        sol_falling = False
        if sol_15m and i < len(sol_15m):
            sol_closes = [c["close"] for c in sol_15m[max(0, i - 10):i + 1]]
            if len(sol_closes) >= 5:
                sol_ema_9 = ind.ema(sol_closes, min(9, len(sol_closes)))
                sol_ema_21 = ind.ema(sol_closes, min(21, len(sol_closes))) if len(sol_closes) >= 21 else sol_ema_9
                sol_vel = ind.price_velocity(sol_closes, 3)
                sol_falling = sol_ema_9 < sol_ema_21 and sol_vel < -0.1

        if active:
            t = active
            bars_held = i - t["entry_idx"]
            max_hold = cfg.trend_max_hold // 15 if t["trade_type"] == "trend_follow" else cfg.mr_max_hold // 15 if t["trade_type"] == "mean_reversion" else 6
            trailing_pct = cfg.trend_trailing if t["trade_type"] == "trend_follow" else cfg.mr_trailing

            if t["direction"] == "long":
                cur_pnl = (price - t["entry_price"]) / t["entry_price"]
                t["peak"] = max(t.get("peak", t["entry_price"]), price)
                hit_tp = price >= t["tp"]
                hit_sl = price <= t["sl"]
                trail_stop = t["peak"] * (1 - trailing_pct)
                hit_trail = t["peak"] > t["entry_price"] and price <= trail_stop
            else:
                cur_pnl = (t["entry_price"] - price) / t["entry_price"]
                t["peak"] = min(t.get("peak", t["entry_price"]), price)
                hit_tp = price <= t["tp"]
                hit_sl = price >= t["sl"]
                trail_stop = t["peak"] * (1 + trailing_pct)
                hit_trail = t["peak"] < t["entry_price"] and price >= trail_stop

            if hit_tp or hit_sl or hit_trail or bars_held >= max_hold:
                leveraged = cur_pnl * cfg.leverage
                fee = cfg.fee_per_side * 2 * cfg.leverage
                net = leveraged - fee
                reason = "tp" if hit_tp else "sl" if hit_sl else "trail" if hit_trail else "timeout"
                trades.append({
                    "direction": t["direction"],
                    "trade_type": t["trade_type"],
                    "regime": t["regime"],
                    "entry": t["entry_price"],
                    "exit": price,
                    "pnl_pct": net * 100,
                    "hold_bars": bars_held,
                    "reason": reason,
                })
                active = None
            continue

        signal_type = None
        signal_trade_type = None
        signal_confidence = 0

        if regime == "dead":
            continue

        if regime in ("ranging", "volatile_ranging"):
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper > bb_lower else 0.5
            extreme_long = bb_pos < 0.05 and rsi < 30
            extreme_short = bb_pos > 0.95 and rsi > 70

            long_mr = (bb_pos < cfg.mr_bb_entry and rsi < cfg.mr_rsi_long_max) and (rsi > rsi_prev or extreme_long)
            short_mr = (bb_pos > (1 - cfg.mr_bb_entry) and rsi > cfg.mr_rsi_short_min) and (rsi < rsi_prev or extreme_short)

            if long_mr and not (cfg.sol_trend_filter and sol_falling):
                if regime == "trending_down":
                    pass
                else:
                    signal_type = "long"
                    signal_trade_type = "mean_reversion"
                    tp_pct = cfg.mr_tp
                    sl_pct = cfg.mr_sl

            if short_mr and (signal_type is None or rsi > 60):
                signal_type = "short"
                signal_trade_type = "mean_reversion"
                tp_pct = cfg.mr_tp
                sl_pct = cfg.mr_sl

        else:
            long_trend = ema_9 > ema_21 and rsi > rsi_prev and cfg.trend_rsi_long_min < rsi < cfg.trend_rsi_long_max and velocity > cfg.trend_velocity_min
            short_trend = (ema_9 < ema_21 and rsi < rsi_prev and cfg.trend_rsi_short_min < rsi < cfg.trend_rsi_short_max
                           and velocity < -cfg.trend_velocity_min and adx_val > cfg.adx_short_min
                           and regime == "trending_down")

            if long_trend and not (cfg.sol_trend_filter and sol_falling):
                signal_type = "long"
                signal_trade_type = "trend_follow"
                tp_pct = cfg.trend_tp
                sl_pct = cfg.trend_sl

            elif short_trend:
                signal_type = "short"
                signal_trade_type = "trend_follow"
                tp_pct = cfg.trend_tp
                sl_pct = cfg.trend_sl

        if signal_type:
            if signal_type == "long":
                active = {
                    "direction": "long",
                    "trade_type": signal_trade_type,
                    "regime": regime,
                    "entry_price": price,
                    "entry_idx": i,
                    "tp": price * (1 + tp_pct),
                    "sl": price * (1 - sl_pct),
                    "peak": price,
                }
            else:
                active = {
                    "direction": "short",
                    "trade_type": signal_trade_type,
                    "regime": regime,
                    "entry_price": price,
                    "entry_idx": i,
                    "tp": price * (1 - tp_pct),
                    "sl": price * (1 + sl_pct),
                    "peak": price,
                }

    if not trades:
        return {"error": "no trades", "asset": asset}

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total_pnl = sum(t["pnl_pct"] for t in trades)
    days = len(candles_1m) / 1440

    by_type = {}
    by_dir = {}
    by_regime = {}
    by_reason = {}
    for t in trades:
        for key, bucket, val in [
            (t["trade_type"], by_type, t["pnl_pct"]),
            (t["direction"], by_dir, t["pnl_pct"]),
            (t["regime"], by_regime, t["pnl_pct"]),
            (t["reason"], by_reason, t["pnl_pct"]),
        ]:
            if key not in bucket:
                bucket[key] = {"count": 0, "wins": 0, "pnl": 0}
            bucket[key]["count"] += 1
            bucket[key]["pnl"] += val
            if val > 0:
                bucket[key]["wins"] += 1

    return {
        "asset": asset,
        "days": round(days, 1),
        "trades": len(trades),
        "trades_per_day": round(len(trades) / max(days, 1), 1),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "daily_pnl": round(total_pnl / max(days, 1), 3),
        "monthly_pnl": round(total_pnl / max(days, 1) * 30, 1),
        "avg_win": round(sum(t["pnl_pct"] for t in wins) / len(wins), 3) if wins else 0,
        "avg_loss": round(sum(t["pnl_pct"] for t in losses) / len(losses), 3) if losses else 0,
        "by_type": {k: {"count": v["count"], "wr": round(v["wins"] / v["count"] * 100), "pnl": round(v["pnl"], 2)} for k, v in by_type.items()},
        "by_direction": {k: {"count": v["count"], "wr": round(v["wins"] / v["count"] * 100), "pnl": round(v["pnl"], 2)} for k, v in by_dir.items()},
        "by_regime": {k: {"count": v["count"], "wr": round(v["wins"] / v["count"] * 100), "pnl": round(v["pnl"], 2)} for k, v in by_regime.items()},
        "by_reason": {k: {"count": v["count"], "pnl": round(v["pnl"], 2)} for k, v in by_reason.items()},
    }


def optimize(asset: str, sol_asset: str = None) -> dict:
    candles = load_candles(asset, limit=200000)
    sol_candles = load_candles(sol_asset, limit=200000) if sol_asset else None

    if len(candles) < 500:
        return {"error": f"Only {len(candles)} candles"}

    best = None
    best_daily = -999
    tested = 0

    for adx_thresh in [18, 20, 22, 25]:
        for mr_tp in [0.005, 0.007, 0.010]:
            for mr_sl in [0.003, 0.005, 0.007]:
                for trend_tp in [0.008, 0.010, 0.012]:
                    for trend_sl in [0.005, 0.007, 0.010]:
                        for mr_bb in [0.15, 0.20, 0.25]:
                            for sol_filter in [True, False]:
                                cfg = StrategyConfig(
                                    adx_trend_threshold=adx_thresh,
                                    mr_tp=mr_tp,
                                    mr_sl=mr_sl,
                                    trend_tp=trend_tp,
                                    trend_sl=trend_sl,
                                    mr_bb_entry=mr_bb,
                                    sol_trend_filter=sol_filter,
                                )
                                result = run_backtest(asset, cfg, candles_1m=candles, sol_candles_1m=sol_candles)
                                tested += 1

                                if result.get("trades", 0) < 20:
                                    continue
                                wr = result.get("win_rate", 0)
                                daily = result.get("daily_pnl", 0)

                                if daily > best_daily and wr >= 55:
                                    best_daily = daily
                                    best = result
                                    best["config"] = {
                                        "adx_thresh": adx_thresh,
                                        "mr_tp": mr_tp,
                                        "mr_sl": mr_sl,
                                        "trend_tp": trend_tp,
                                        "trend_sl": trend_sl,
                                        "mr_bb": mr_bb,
                                        "sol_filter": sol_filter,
                                    }

                                if tested % 500 == 0:
                                    log.info(f"  {tested} configs tested, best daily={best_daily:.3f}%")

    log.info(f"Tested {tested} configs total")
    return best or {"error": "no profitable config found", "tested": tested}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    for asset in ["JUP", "JTO", "PYTH"]:
        print(f"\n{'='*60}")
        print(f"  {asset} -- DEFAULT CONFIG")
        print(f"{'='*60}")

        result = run_backtest(asset)
        if "error" in result:
            print(f"  {result['error']}")
            continue

        print(f"  {result['trades']} trades over {result['days']}d ({result['trades_per_day']}/day)")
        print(f"  WR: {result['win_rate']}% | Daily: {result['daily_pnl']}% | Monthly: {result['monthly_pnl']}%")
        print(f"  Avg win: {result['avg_win']}% | Avg loss: {result['avg_loss']}%")
        print(f"  By type: {json.dumps(result['by_type'])}")
        print(f"  By dir:  {json.dumps(result['by_direction'])}")
        print(f"  By exit: {json.dumps(result['by_reason'])}")

        print(f"\n  Optimizing {asset}...")
        best = optimize(asset)
        if "error" not in best:
            print(f"\n  BEST for {asset}:")
            print(f"  {best['trades']} trades, WR={best['win_rate']}%, Daily={best['daily_pnl']}%, Monthly={best['monthly_pnl']}%")
            print(f"  Config: {json.dumps(best['config'])}")
            print(f"  By type: {json.dumps(best['by_type'])}")
            print(f"  By dir:  {json.dumps(best['by_direction'])}")
            print(f"  By exit: {json.dumps(best['by_reason'])}")
        else:
            print(f"  {best}")
