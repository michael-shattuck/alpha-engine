import logging
import json
import time as _time
from dataclasses import dataclass, field, asdict
from typing import Optional

from server.ml.features import load_candles
from server.signals import indicators as ind

log = logging.getLogger("backtest_v4")

DRIFT_FEE_PER_SIDE = 0.00035
SLIPPAGE_PCT = 0.0015
ROUNDING_FLAT = 0.002


@dataclass
class AssetConfig:
    sl_pct: float = 0.005
    trailing_pct: float = 0.006
    max_hold_minutes: int = 90
    long_threshold: float = 0.50
    short_threshold: float = -0.50
    leverage: float = 3.0
    h1_weight: float = 0.30
    m15_weight: float = 0.25
    m5_weight: float = 0.25
    m1_weight: float = 0.20


def aggregate(candles_1m, minutes):
    result = []
    for i in range(0, len(candles_1m) - minutes + 1, minutes):
        chunk = candles_1m[i:i + minutes]
        result.append({
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
        })
    return result


def score_tf(closes, price):
    if len(closes) < 15:
        return 0.0

    rsi = ind.rsi(closes)
    rsi_prev = ind.rsi(closes[:-1]) if len(closes) > 15 else rsi
    ema_9 = ind.ema(closes, 9)
    ema_21 = ind.ema(closes, 21)
    velocity = ind.price_velocity(closes, 3)
    bb_l, _, bb_u = ind.bollinger_bands(closes)
    bb_pos = (price - bb_l) / (bb_u - bb_l) if bb_u > bb_l else 0.5

    s = 0.0

    if ema_9 > ema_21:
        s += 0.3
    elif ema_9 < ema_21:
        s -= 0.3

    if 50 < rsi < 70 and rsi > rsi_prev:
        s += 0.2
    elif 30 < rsi < 50 and rsi < rsi_prev:
        s -= 0.2
    elif rsi >= 70:
        s -= 0.3
    elif rsi <= 30:
        s += 0.3

    if velocity > 0.1:
        s += 0.2
    elif velocity < -0.1:
        s -= 0.2

    if bb_pos < 0.15:
        s += 0.3
    elif bb_pos > 0.85:
        s -= 0.3

    if len(closes) >= 3:
        move = (closes[-1] - closes[-3]) / closes[-3]
        if move > 0.002:
            s += 0.15
        elif move < -0.002:
            s -= 0.15

    return s


def realistic_pnl(entry, exit_price, direction, leverage, notional_usd):
    if direction == "long":
        raw = (exit_price - entry) / entry
    else:
        raw = (entry - exit_price) / entry

    leveraged = raw * leverage
    fee = DRIFT_FEE_PER_SIDE * 2 * leverage
    slippage = SLIPPAGE_PCT * leverage
    rounding = ROUNDING_FLAT / max(notional_usd, 1) * leverage

    return leveraged - fee - slippage - rounding


def run_backtest(asset, cfg=AssetConfig(), candles_1m=None):
    if candles_1m is None:
        candles_1m = load_candles(asset, limit=200000)
    if len(candles_1m) < 500:
        return {"error": f"Only {len(candles_1m)} candles"}

    c1 = candles_1m
    c5 = aggregate(c1, 5)
    c15 = aggregate(c1, 15)
    c60 = aggregate(c1, 60)

    trades = []
    active = None
    notional_usd = 15.0

    for i in range(60, len(c1)):
        price = c1[i]["close"]
        i5 = i // 5
        i15 = i // 15
        i60 = i // 60

        if active:
            t = active
            bars = i - t["entry_idx"]

            if t["dir"] == "long":
                t["peak"] = max(t["peak"], c1[i]["high"])
                hit_sl = c1[i]["low"] <= t["sl"]
                trail = t["peak"] * (1 - cfg.trailing_pct)
                hit_trail = t["peak"] > t["entry"] * 1.001 and c1[i]["low"] <= trail
            else:
                t["peak"] = min(t["peak"], c1[i]["low"])
                hit_sl = c1[i]["high"] >= t["sl"]
                trail = t["peak"] * (1 + cfg.trailing_pct)
                hit_trail = t["peak"] < t["entry"] * 0.999 and c1[i]["high"] >= trail

            exit_price = None
            reason = None

            if hit_sl:
                exit_price = t["sl"]
                reason = "sl"
            elif hit_trail:
                exit_price = trail
                reason = "trail"
            elif bars >= cfg.max_hold_minutes:
                exit_price = price
                reason = "timeout"

            if exit_price:
                net = realistic_pnl(t["entry"], exit_price, t["dir"], cfg.leverage, notional_usd)
                peak_move = abs(t["peak"] - t["entry"]) / t["entry"]
                trades.append({
                    "dir": t["dir"],
                    "pnl": net * 100,
                    "hold": bars,
                    "reason": reason,
                    "peak_move": peak_move * 100,
                    "entry": t["entry"],
                    "exit": exit_price,
                })
                active = None
            continue

        if i5 < 15 or i15 < 15 or i60 < 15:
            continue

        total = 0
        c1_closes = [c["close"] for c in c1[max(0, i - 30):i + 1]]
        total += score_tf(c1_closes, price) * cfg.m1_weight

        c5_closes = [c["close"] for c in c5[max(0, i5 - 30):i5 + 1]]
        total += score_tf(c5_closes, price) * cfg.m5_weight

        c15_closes = [c["close"] for c in c15[max(0, i15 - 30):i15 + 1]]
        total += score_tf(c15_closes, price) * cfg.m15_weight

        c60_closes = [c["close"] for c in c60[max(0, i60 - 30):i60 + 1]]
        total += score_tf(c60_closes, price) * cfg.h1_weight

        if total > cfg.long_threshold:
            active = {"dir": "long", "entry": price, "entry_idx": i,
                      "sl": price * (1 - cfg.sl_pct), "peak": price}
        elif total < cfg.short_threshold:
            active = {"dir": "short", "entry": price, "entry_idx": i,
                      "sl": price * (1 + cfg.sl_pct), "peak": price}

    if not trades:
        return {"error": "no trades", "asset": asset}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    days = len(c1) / 1440

    by_reason = {}
    by_dir = {}
    for t in trades:
        for key, bucket in [(t["reason"], by_reason), (t["dir"], by_dir)]:
            if key not in bucket:
                bucket[key] = {"count": 0, "wins": 0, "pnl": 0}
            bucket[key]["count"] += 1
            bucket[key]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                bucket[key]["wins"] += 1

    for bucket in [by_reason, by_dir]:
        for v in bucket.values():
            v["pnl"] = round(v["pnl"], 2)
            v["wr"] = round(v["wins"] / max(v["count"], 1) * 100)

    return {
        "asset": asset,
        "days": round(days, 1),
        "trades": len(trades),
        "per_day": round(len(trades) / max(days, 1), 1),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "daily_pnl": round(total_pnl / max(days, 1), 3),
        "monthly": round(total_pnl / max(days, 1) * 30, 1),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "rr": round(abs(sum(t["pnl"] for t in wins) / max(len(wins), 1)) / abs(sum(t["pnl"] for t in losses) / max(len(losses), 1)), 2) if wins and losses else 0,
        "best": round(max(t["pnl"] for t in trades), 2),
        "worst": round(min(t["pnl"] for t in trades), 2),
        "by_reason": by_reason,
        "by_dir": by_dir,
        "config": asdict(cfg),
    }


def optimize_asset(asset, candles_1m=None):
    if candles_1m is None:
        candles_1m = load_candles(asset, limit=200000)
    if len(candles_1m) < 500:
        return {"error": f"Only {len(candles_1m)} candles"}

    best = None
    best_score = -999
    tested = 0

    for sl in [0.003, 0.004, 0.005, 0.007]:
        for trail in [0.004, 0.005, 0.006, 0.008, 0.010]:
            for hold in [45, 60, 90, 120]:
                for thresh in [0.35, 0.40, 0.50, 0.60]:
                    for h1w in [0.25, 0.30, 0.35]:
                        cfg = AssetConfig(
                            sl_pct=sl, trailing_pct=trail,
                            max_hold_minutes=hold,
                            long_threshold=thresh, short_threshold=-thresh,
                            h1_weight=h1w,
                            m15_weight=(1 - h1w) / 3,
                            m5_weight=(1 - h1w) / 3,
                            m1_weight=(1 - h1w) / 3,
                        )
                        r = run_backtest(asset, cfg, candles_1m=candles_1m)
                        tested += 1

                        if r.get("trades", 0) < 30:
                            continue

                        wr = r.get("wr", 0)
                        daily = r.get("daily_pnl", 0)
                        rr = r.get("rr", 0)
                        avg_loss = abs(r.get("avg_loss", 0))

                        score = daily * 0.5 + wr * 0.02 + rr * 0.5 - avg_loss * 0.3

                        if score > best_score and wr >= 40 and rr >= 1.5:
                            best_score = score
                            best = r

                        if tested % 500 == 0:
                            log.info(f"  {asset}: {tested} tested, best_score={best_score:.2f}")

    log.info(f"  {asset}: {tested} total configs tested")
    return best or {"error": "no profitable config", "tested": tested}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    assets = ["JUP", "JTO", "PYTH"]

    print("=" * 70)
    print("  DEFAULT CONFIG (with realistic friction)")
    print("=" * 70)
    for asset in assets:
        r = run_backtest(asset)
        if "error" in r:
            print(f"  {asset}: {r['error']}")
            continue
        print(f"  {asset}: {r['trades']}t WR={r['wr']}% R:R={r['rr']} daily={r['daily_pnl']:+.3f}% monthly={r['monthly']}%")
        print(f"    avg_w={r['avg_win']:+.2f}% avg_l={r['avg_loss']:+.2f}% best={r['best']}% worst={r['worst']}%")
        exit_str = " ".join(f"{k}={v['count']}t({v['wr']}%wr)" for k, v in r["by_reason"].items())
        dir_str = " ".join(f"{k}={v['count']}t({v['wr']}%wr)" for k, v in r["by_dir"].items())
        print(f"    exit: {exit_str}")
        print(f"    dir:  {dir_str}")

    print("\n" + "=" * 70)
    print("  OPTIMIZING PER ASSET")
    print("=" * 70)
    results = {}
    for asset in assets:
        print(f"\n  Optimizing {asset}...")
        candles = load_candles(asset, limit=200000)
        best = optimize_asset(asset, candles_1m=candles)
        results[asset] = best
        if "error" in best:
            print(f"    {best}")
            continue
        cfg = best["config"]
        print(f"    BEST: {best['trades']}t WR={best['wr']}% R:R={best['rr']} daily={best['daily_pnl']:+.3f}% monthly={best['monthly']}%")
        print(f"    avg_w={best['avg_win']:+.2f}% avg_l={best['avg_loss']:+.2f}%")
        print(f"    SL={cfg['sl_pct']} trail={cfg['trailing_pct']} hold={cfg['max_hold_minutes']}m thresh={cfg['long_threshold']}")
        print(f"    weights: h1={cfg['h1_weight']} m15={cfg['m15_weight']:.3f} m5={cfg['m5_weight']:.3f} m1={cfg['m1_weight']:.3f}")

    print("\n" + "=" * 70)
    print("  OPTIMAL CONFIGS SUMMARY")
    print("=" * 70)
    for asset, best in results.items():
        if "error" in best:
            continue
        cfg = best["config"]
        print(f"  \"{asset}\": AssetConfig(sl_pct={cfg['sl_pct']}, trailing_pct={cfg['trailing_pct']}, max_hold_minutes={cfg['max_hold_minutes']}, long_threshold={cfg['long_threshold']}, short_threshold={cfg['short_threshold']}, h1_weight={cfg['h1_weight']}, m15_weight={cfg['m15_weight']:.4f}, m5_weight={cfg['m5_weight']:.4f}, m1_weight={cfg['m1_weight']:.4f}),")
