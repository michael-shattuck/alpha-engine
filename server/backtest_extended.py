import asyncio
import argparse
import math
import time
import httpx
from datetime import datetime, timezone


COINGECKO_API = "https://api.coingecko.com/api/v3"
DEFILLAMA_API = "https://yields.llama.fi"
ORCA_POOL_ID = "a5c85bc8-eb41-45c0-a520-d18d7529c0d8"


async def fetch_hourly_prices(days: int = 365) -> list[tuple[float, float]]:
    async with httpx.AsyncClient(timeout=30) as http:
        all_prices = {}
        now = int(time.time())
        chunk_days = 85
        chunks_needed = (days // chunk_days) + 1

        end = now
        for i in range(chunks_needed):
            start = end - chunk_days * 86400
            try:
                resp = await http.get(
                    f"{COINGECKO_API}/coins/solana/market_chart/range",
                    params={"vs_currency": "usd", "from": start, "to": end}
                )
                if resp.status_code == 200:
                    for p in resp.json().get("prices", []):
                        all_prices[int(p[0] / 1000)] = p[1]
                elif resp.status_code == 429:
                    await asyncio.sleep(12)
                    resp = await http.get(
                        f"{COINGECKO_API}/coins/solana/market_chart/range",
                        params={"vs_currency": "usd", "from": start, "to": end}
                    )
                    if resp.status_code == 200:
                        for p in resp.json().get("prices", []):
                            all_prices[int(p[0] / 1000)] = p[1]
            except Exception:
                pass

            end = start
            await asyncio.sleep(2)

        return sorted(all_prices.items())


async def fetch_pool_apys() -> dict[str, float]:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(f"{DEFILLAMA_API}/chart/{ORCA_POOL_ID}")
        result = {}
        for point in resp.json().get("data", []):
            result[point["timestamp"][:10]] = float(point.get("apy", 0) or 0)
        return result


def get_apy(apy_data: dict, ts: float) -> float:
    d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    if d in apy_data:
        return apy_data[d]
    for k in sorted(apy_data.keys(), reverse=True):
        if k <= d:
            return apy_data[k]
    return 65.0


def compute_volatility(price_history: list[float]) -> float:
    if len(price_history) < 3:
        return 0
    returns = [
        (price_history[i] - price_history[i - 1]) / price_history[i - 1]
        for i in range(1, len(price_history))
    ]
    return (sum(r ** 2 for r in returns) / len(returns)) ** 0.5


def dynamic_range(vol: float) -> float:
    if vol < 0.005:
        return 0.02
    if vol < 0.015:
        return 0.03
    if vol < 0.03:
        return 0.05
    if vol < 0.06:
        return 0.08
    return 0.12


def dynamic_leverage(vol: float, base_leverage: float) -> float:
    if vol > 0.04:
        return min(base_leverage, 1.5)
    if vol > 0.02:
        return min(base_leverage, 2.0)
    return base_leverage


def run_backtest(
    prices: list[tuple[float, float]],
    apy_data: dict[str, float],
    capital: float,
    leverage: float = 2.5,
    borrow_rate_apy: float = 12.0,
    rebalance_cost_pct: float = 0.0008,
    compound_threshold_pct: float = 0.002,
) -> dict:
    equity = capital
    borrowed = equity * (leverage - 1)
    deposit = equity * leverage
    entry_price = prices[0][1]
    current_range = 0.05
    lower = entry_price * (1 - current_range)
    upper = entry_price * (1 + current_range)
    fees = 0.0
    rebalances = 0
    compounds = 0
    peak = equity
    worst_dd = 0.0
    price_buf = [prices[0][1]]

    monthly_snapshots = {}
    hourly_values = []

    for i in range(1, len(prices)):
        ts, price = prices[i]
        prev_ts = prices[i - 1][0]
        hours = (ts - prev_ts) / 3600
        if hours <= 0:
            continue

        price_buf.append(price)
        if len(price_buf) > 24:
            price_buf = price_buf[-24:]

        vol = compute_volatility(price_buf)
        opt_range = dynamic_range(vol)
        cur_lev = dynamic_leverage(vol, leverage)

        in_range = lower <= price <= upper

        if in_range:
            apy = get_apy(apy_data, ts)
            concentration = min(0.10 / current_range, 8.0)
            hourly_rate = apy / 100 / 365 / 24 * concentration
            period_fees = hourly_rate * hours * deposit
            fees += period_fees

            ratio = price / entry_price
            std_il = 2 * math.sqrt(ratio) / (1 + ratio) - 1
            rw = (upper - lower) / entry_price
            cf = min(2.0 / rw, 10.0) if rw > 0 else 1
            current_value = deposit * (1 + std_il * cf)
        else:
            if price < lower:
                sol_at_exit = deposit / lower
                current_value = sol_at_exit * price
            else:
                current_value = deposit

            net_value = current_value + fees - borrowed
            cost = net_value * rebalance_cost_pct
            equity = max(net_value - cost, 1)
            fees = 0
            compounds += 1
            borrowed = equity * (cur_lev - 1)
            deposit = equity * cur_lev
            entry_price = price
            current_range = opt_range
            lower = price * (1 - current_range)
            upper = price * (1 + current_range)
            rebalances += 1
            current_value = deposit

        borrow_cost = borrowed * (borrow_rate_apy / 100 / 365 / 24) * hours
        fees -= borrow_cost

        if in_range and fees > equity * compound_threshold_pct:
            net = current_value + fees - borrowed
            if net > equity * 1.001:
                equity = net
                borrowed = equity * (cur_lev - 1)
                deposit = equity * cur_lev
                entry_price = price
                current_range = opt_range
                lower = price * (1 - current_range)
                upper = price * (1 + current_range)
                fees = 0
                compounds += 1
                current_value = deposit

        net_equity = current_value + fees - borrowed
        if net_equity > peak:
            peak = net_equity
        dd = (peak - net_equity) / peak * 100 if peak > 0 else 0
        if dd > worst_dd:
            worst_dd = dd

        if net_equity < equity * 0.1:
            equity = max(net_equity, 0)
            borrowed = 0
            deposit = equity
            entry_price = price
            current_range = 0.10
            lower = price * (1 - current_range)
            upper = price * (1 + current_range)
            fees = 0
            current_value = equity

        month_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        monthly_snapshots[month_key] = net_equity
        hourly_values.append({"ts": ts, "equity": net_equity, "price": price})

    final = current_value + fees - borrowed
    days = (prices[-1][0] - prices[0][0]) / 86400
    pnl = final - capital
    pnl_pct = pnl / capital * 100

    return {
        "capital": capital,
        "final": final,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "monthly_avg": pnl_pct / days * 30 if days > 0 else 0,
        "annualized": pnl_pct / days * 365 if days > 0 else 0,
        "days": days,
        "rebalances": rebalances,
        "compounds": compounds,
        "worst_dd": worst_dd,
        "start_price": prices[0][1],
        "end_price": prices[-1][1],
        "price_change": (prices[-1][1] - prices[0][1]) / prices[0][1] * 100,
        "monthly_snapshots": monthly_snapshots,
        "hourly_values": hourly_values,
    }


def print_result(r: dict, label: str):
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print(f"  Period:        {r['days']:.0f} days")
    print(f"  SOL:           ${r['start_price']:.2f} -> ${r['end_price']:.2f} ({r['price_change']:+.1f}%)")
    print(f"  Capital:       ${r['capital']:,.2f}")
    print(f"  Final Equity:  ${r['final']:,.2f}")
    print(f"  P&L:           ${r['pnl']:,.2f} ({r['pnl_pct']:+.2f}%)")
    print(f"  Monthly Avg:   {r['monthly_avg']:+.1f}%/mo")
    print(f"  Annualized:    {r['annualized']:+.0f}%")
    print(f"  Rebalances:    {r['rebalances']}")
    print(f"  Compounds:     {r['compounds']}")
    print(f"  Max Drawdown:  {r['worst_dd']:.1f}%")

    print(f"\n  Monthly Returns:")
    prev = r["capital"]
    for month, eq in sorted(r["monthly_snapshots"].items()):
        pct = (eq - prev) / prev * 100 if prev > 0 else 0
        bar = "+" * int(max(pct, 0) * 1.5) or "-" * int(abs(min(pct, 0)) * 1.5)
        print(f"    {month}: {pct:+7.1f}% | equity: ${eq:>10,.0f} | {bar}")
        prev = eq

    winning = [
        (eq - prev) / prev * 100
        for prev, (_, eq) in zip(
            [r["capital"]] + [v for _, v in sorted(r["monthly_snapshots"].items())[:-1]],
            sorted(r["monthly_snapshots"].items()),
        )
    ]
    pos = sum(1 for w in winning if w > 0)
    neg = sum(1 for w in winning if w <= 0)
    print(f"\n  Win/Loss Months: {pos}/{neg}")
    if winning:
        print(f"  Best Month:  {max(winning):+.1f}%")
        print(f"  Worst Month: {min(winning):+.1f}%")
        print(f"  Median:      {sorted(winning)[len(winning)//2]:+.1f}%")
    print(f"{'=' * 70}")


async def main():
    parser = argparse.ArgumentParser(description="Extended Backtest")
    parser.add_argument("--days", type=int, default=365, help="Days of history")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--leverage", type=float, default=2.5)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()

    print(f"Fetching {args.days} days of hourly data...")
    prices, apy_data = await asyncio.gather(
        fetch_hourly_prices(args.days),
        fetch_pool_apys(),
    )
    print(f"Prices: {len(prices)} hourly points")
    print(f"APYs: {len(apy_data)} daily points")

    if not prices:
        print("No price data fetched")
        return

    first = datetime.fromtimestamp(prices[0][0], tz=timezone.utc)
    last = datetime.fromtimestamp(prices[-1][0], tz=timezone.utc)
    print(f"Range: {first.strftime('%Y-%m-%d')} to {last.strftime('%Y-%m-%d')}")

    if args.compare:
        for label, lev in [
            ("No leverage", 1.0),
            ("2x leverage", 2.0),
            ("2.5x leverage", 2.5),
            ("3x leverage", 3.0),
        ]:
            r = run_backtest(prices, apy_data, args.capital, leverage=lev)
            print_result(r, f"{label} + Dynamic Range + Compound")
    else:
        r = run_backtest(prices, apy_data, args.capital, leverage=args.leverage)
        print_result(r, f"{args.leverage}x Leveraged + Dynamic Range + Compound")

        hold_return = (prices[-1][1] - prices[0][1]) / prices[0][1] * 100
        hold_final = args.capital * (1 + hold_return / 100)
        print(f"\n  vs Hold SOL:    ${hold_final:,.2f} ({hold_return:+.1f}%)")
        print(f"  Alpha:          {r['pnl_pct'] - hold_return:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
