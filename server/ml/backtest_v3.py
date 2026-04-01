import logging
import json
from dataclasses import dataclass

from server.ml.features import load_candles
from server.signals import indicators as ind

log = logging.getLogger("backtest_v3")


@dataclass
class Config:
    sl_pct: float = 0.004
    trailing_pct: float = 0.005
    max_hold_candles: int = 90
    long_threshold: float = 0.30
    short_threshold: float = -0.30
    leverage: float = 3.0
    fee_per_side: float = 0.00035

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


def score_tf(closes, highs, lows, price):
    if len(closes) < 15:
        return 0, "x"

    rsi = ind.rsi(closes)
    rsi_prev = ind.rsi(closes[:-1]) if len(closes) > 15 else rsi
    ema_9 = ind.ema(closes, 9)
    ema_21 = ind.ema(closes, 21)
    velocity = ind.price_velocity(closes, 3)
    bb_l, bb_m, bb_u = ind.bollinger_bands(closes)
    bb_pos = (price - bb_l) / (bb_u - bb_l) if bb_u > bb_l else 0.5

    s = 0.0
    tags = []

    if ema_9 > ema_21:
        s += 0.3
        tags.append("e+")
    elif ema_9 < ema_21:
        s -= 0.3
        tags.append("e-")

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

    return s, f"{''.join(tags)}r{rsi:.0f}v{velocity:.1f}"


def run_backtest(asset, cfg=Config(), candles_1m=None):
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

    for i in range(60, len(c1)):
        price = c1[i]["close"]
        i5 = i // 5
        i15 = i // 15
        i60 = i // 60

        if active:
            t = active
            bars = i - t["entry_idx"]

            if t["dir"] == "long":
                t["peak"] = max(t["peak"], price)
                hit_sl = price <= t["sl"]
                trail = t["peak"] * (1 - cfg.trailing_pct)
                hit_trail = t["peak"] > t["entry"] * 1.001 and price <= trail
            else:
                t["peak"] = min(t["peak"], price)
                hit_sl = price >= t["sl"]
                trail = t["peak"] * (1 + cfg.trailing_pct)
                hit_trail = t["peak"] < t["entry"] * 0.999 and price >= trail

            if hit_sl or hit_trail or bars >= cfg.max_hold_candles:
                pnl = ((price - t["entry"]) / t["entry"]) if t["dir"] == "long" else ((t["entry"] - price) / t["entry"])
                lev_pnl = pnl * cfg.leverage
                fee = cfg.fee_per_side * 2 * cfg.leverage
                net = lev_pnl - fee
                reason = "sl" if hit_sl else "trail" if hit_trail else "timeout"
                trades.append({
                    "dir": t["dir"], "pnl": net * 100, "hold": bars,
                    "reason": reason, "peak_move": abs(t["peak"] - t["entry"]) / t["entry"] * 100,
                })
                active = None
            continue

        if i5 < 15 or i15 < 15 or i60 < 15:
            continue

        w = cfg
        total = 0

        c1_closes = [c["close"] for c in c1[max(0, i - 30):i + 1]]
        c1_highs = [c["high"] for c in c1[max(0, i - 30):i + 1]]
        c1_lows = [c["low"] for c in c1[max(0, i - 30):i + 1]]
        s1, _ = score_tf(c1_closes, c1_highs, c1_lows, price)
        total += s1 * w.m1_weight

        c5_closes = [c["close"] for c in c5[max(0, i5 - 30):i5 + 1]]
        c5_highs = [c["high"] for c in c5[max(0, i5 - 30):i5 + 1]]
        c5_lows = [c["low"] for c in c5[max(0, i5 - 30):i5 + 1]]
        s5, _ = score_tf(c5_closes, c5_highs, c5_lows, price)
        total += s5 * w.m5_weight

        c15_closes = [c["close"] for c in c15[max(0, i15 - 30):i15 + 1]]
        c15_highs = [c["high"] for c in c15[max(0, i15 - 30):i15 + 1]]
        c15_lows = [c["low"] for c in c15[max(0, i15 - 30):i15 + 1]]
        s15, _ = score_tf(c15_closes, c15_highs, c15_lows, price)
        total += s15 * w.m15_weight

        c60_closes = [c["close"] for c in c60[max(0, i60 - 30):i60 + 1]]
        c60_highs = [c["high"] for c in c60[max(0, i60 - 30):i60 + 1]]
        c60_lows = [c["low"] for c in c60[max(0, i60 - 30):i60 + 1]]
        s60, _ = score_tf(c60_closes, c60_highs, c60_lows, price)
        total += s60 * w.h1_weight

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
                bucket[key] = {"count": 0, "wins": 0, "pnl": 0, "peak_moves": []}
            bucket[key]["count"] += 1
            bucket[key]["pnl"] += t["pnl"]
            bucket[key]["peak_moves"].append(t["peak_move"])
            if t["pnl"] > 0:
                bucket[key]["wins"] += 1

    for bucket in [by_reason, by_dir]:
        for v in bucket.values():
            v["avg_peak"] = round(sum(v["peak_moves"]) / len(v["peak_moves"]), 2) if v["peak_moves"] else 0
            del v["peak_moves"]
            v["pnl"] = round(v["pnl"], 2)

    return {
        "asset": asset,
        "days": round(days, 1),
        "trades": len(trades),
        "per_day": round(len(trades) / max(days, 1), 1),
        "wins": len(wins),
        "losses": len(losses),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "daily_pnl": round(total_pnl / max(days, 1), 3),
        "monthly": round(total_pnl / max(days, 1) * 30, 1),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "rr_ratio": round(abs(sum(t["pnl"] for t in wins) / len(wins)) / abs(sum(t["pnl"] for t in losses) / len(losses)), 2) if wins and losses else 0,
        "best_trade": round(max(t["pnl"] for t in trades), 2),
        "worst_trade": round(min(t["pnl"] for t in trades), 2),
        "by_reason": by_reason,
        "by_dir": by_dir,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    for asset in ["JUP", "JTO", "PYTH"]:
        print(f"\n{'='*60}")
        print(f"  {asset}")
        print(f"{'='*60}")

        configs = [
            ("default",       Config()),
            ("tight_sl",      Config(sl_pct=0.003, trailing_pct=0.004)),
            ("wide_trail",    Config(sl_pct=0.004, trailing_pct=0.008)),
            ("aggressive",    Config(sl_pct=0.003, trailing_pct=0.006, long_threshold=0.25, short_threshold=-0.25)),
            ("selective",     Config(sl_pct=0.004, trailing_pct=0.005, long_threshold=0.40, short_threshold=-0.40)),
            ("very_select",   Config(sl_pct=0.005, trailing_pct=0.006, long_threshold=0.50, short_threshold=-0.50)),
        ]

        candles = load_candles(asset, limit=200000)
        for name, cfg in configs:
            r = run_backtest(asset, cfg, candles_1m=candles)
            if "error" in r:
                print(f"  {name:15} {r['error']}")
                continue
            print(f"  {name:15} {r['trades']:>4}t WR={r['wr']:>5}% R:R={r['rr_ratio']} daily={r['daily_pnl']:>+7.3f}% avg_w={r['avg_win']:>+5.2f}% avg_l={r['avg_loss']:>+5.2f}% best={r['best_trade']}% worst={r['worst_trade']}%")
            print(f"                  exit: {json.dumps({k: f\"{v['count']}t ${v['pnl']}\" for k,v in r['by_reason'].items()})}  dir: {json.dumps({k: f\"{v['count']}t ${v['pnl']}\" for k,v in r['by_dir'].items()})}")
