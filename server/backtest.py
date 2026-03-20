import asyncio
import argparse
import math
import time
import httpx
from dataclasses import dataclass, field


COINGECKO_API = "https://api.coingecko.com/api/v3"
DEFILLAMA_API = "https://yields.llama.fi"
ORCA_SOL_USDC_POOL_ID = "a5c85bc8-eb41-45c0-a520-d18d7529c0d8"


@dataclass
class BacktestPosition:
    entry_price: float
    lower_price: float
    upper_price: float
    deposit_usd: float
    current_value_usd: float
    fees_earned_usd: float = 0.0
    rebalance_count: int = 0
    hours_in_range: float = 0.0
    hours_out_of_range: float = 0.0


@dataclass
class BacktestResult:
    period_days: int = 0
    start_price: float = 0.0
    end_price: float = 0.0
    price_change_pct: float = 0.0
    capital: float = 0.0
    final_value: float = 0.0
    total_fees: float = 0.0
    total_il: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    annualized_pct: float = 0.0
    monthly_avg_pct: float = 0.0
    rebalance_count: int = 0
    time_in_range_pct: float = 0.0
    avg_apy_used: float = 0.0
    worst_drawdown_pct: float = 0.0
    daily_pnls: list = field(default_factory=list)
    hourly_values: list = field(default_factory=list)


async def fetch_sol_prices(days: int) -> list[tuple[float, float]]:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(
            f"{COINGECKO_API}/coins/solana/market_chart",
            params={"vs_currency": "usd", "days": days}
        )
        resp.raise_for_status()
        return [(p[0] / 1000, p[1]) for p in resp.json()["prices"]]


async def fetch_pool_apys() -> dict[str, float]:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(f"{DEFILLAMA_API}/chart/{ORCA_SOL_USDC_POOL_ID}")
        resp.raise_for_status()
        data = resp.json().get("data", [])
        result = {}
        for point in data:
            date_str = point["timestamp"][:10]
            apy = float(point.get("apy", 0) or 0)
            result[date_str] = apy
        return result


def get_apy_for_timestamp(apy_data: dict[str, float], ts: float) -> float:
    from datetime import datetime, timezone
    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    if date_str in apy_data:
        return apy_data[date_str]
    dates = sorted(apy_data.keys())
    for d in reversed(dates):
        if d <= date_str:
            return apy_data[d]
    return 65.0


def run_backtest(
    prices: list[tuple[float, float]],
    apy_data: dict[str, float],
    capital: float,
    range_pct: float,
    concentration_mult: float,
    strategy: str = "tight_range",
) -> BacktestResult:
    result = BacktestResult()
    result.capital = capital
    result.start_price = prices[0][1]
    result.end_price = prices[-1][1]
    result.price_change_pct = (result.end_price - result.start_price) / result.start_price * 100
    result.period_days = int((prices[-1][0] - prices[0][0]) / 86400)

    position = BacktestPosition(
        entry_price=prices[0][1],
        lower_price=prices[0][1] * (1 - range_pct),
        upper_price=prices[0][1] * (1 + range_pct),
        deposit_usd=capital,
        current_value_usd=capital,
    )

    peak_value = capital
    worst_drawdown = 0.0
    total_apys = []
    daily_values = {}

    for i in range(1, len(prices)):
        ts, price = prices[i]
        prev_ts = prices[i - 1][0]
        hours_elapsed = (ts - prev_ts) / 3600

        if hours_elapsed <= 0:
            continue

        in_range = position.lower_price <= price <= position.upper_price

        if in_range:
            position.hours_in_range += hours_elapsed

            price_ratio = price / position.entry_price
            standard_il = 2 * math.sqrt(price_ratio) / (1 + price_ratio) - 1
            range_width = (position.upper_price - position.lower_price) / position.entry_price
            concentration_factor = min(2.0 / range_width, 10.0) if range_width > 0 else 1.0
            il_raw = standard_il * concentration_factor
            position.current_value_usd = position.deposit_usd * (1 + il_raw)

            apy = get_apy_for_timestamp(apy_data, ts)
            total_apys.append(apy)
            hourly_rate = apy / 100 / 365 / 24 * concentration_mult
            fees = hourly_rate * hours_elapsed * position.deposit_usd
            position.fees_earned_usd += fees
        else:
            position.hours_out_of_range += hours_elapsed

            if price < position.lower_price:
                sol_at_exit = position.deposit_usd / position.lower_price
                position.current_value_usd = sol_at_exit * price
            else:
                position.current_value_usd = position.deposit_usd

            if strategy == "tight_range":
                old_value = position.current_value_usd + position.fees_earned_usd
                rebalance_cost = old_value * 0.001
                new_deposit = old_value - rebalance_cost

                position = BacktestPosition(
                    entry_price=price,
                    lower_price=price * (1 - range_pct),
                    upper_price=price * (1 + range_pct),
                    deposit_usd=new_deposit,
                    current_value_usd=new_deposit,
                    fees_earned_usd=0,
                    rebalance_count=position.rebalance_count + 1,
                    hours_in_range=position.hours_in_range,
                    hours_out_of_range=position.hours_out_of_range,
                )
                result.total_fees += position.fees_earned_usd
                result.rebalance_count += 1

        total_value = position.current_value_usd + position.fees_earned_usd
        result.hourly_values.append({"ts": ts, "value": total_value, "price": price})

        if total_value > peak_value:
            peak_value = total_value
        drawdown = (peak_value - total_value) / peak_value * 100
        if drawdown > worst_drawdown:
            worst_drawdown = drawdown

        from datetime import datetime, timezone
        day_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        daily_values[day_key] = total_value

    final_value = position.current_value_usd + position.fees_earned_usd
    result.final_value = final_value
    result.total_fees = position.fees_earned_usd
    result.total_il = position.current_value_usd - position.deposit_usd
    result.total_pnl = final_value - capital
    result.total_pnl_pct = (result.total_pnl / capital) * 100
    result.worst_drawdown_pct = worst_drawdown
    result.rebalance_count = position.rebalance_count
    result.avg_apy_used = sum(total_apys) / len(total_apys) if total_apys else 0

    total_hours = position.hours_in_range + position.hours_out_of_range
    result.time_in_range_pct = (position.hours_in_range / total_hours * 100) if total_hours > 0 else 0

    if result.period_days > 0:
        result.annualized_pct = result.total_pnl_pct / result.period_days * 365
        result.monthly_avg_pct = result.total_pnl_pct / result.period_days * 30

    prev_val = capital
    days_sorted = sorted(daily_values.keys())
    for d in days_sorted:
        v = daily_values[d]
        daily_pnl = (v - prev_val) / prev_val * 100
        result.daily_pnls.append({"date": d, "pnl_pct": daily_pnl, "value": v})
        prev_val = v

    return result


def print_result(result: BacktestResult, label: str):
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print(f"  Period:         {result.period_days} days")
    print(f"  SOL:            ${result.start_price:.2f} -> ${result.end_price:.2f} ({result.price_change_pct:+.1f}%)")
    print(f"  Capital:        ${result.capital:.2f}")
    print(f"  Final Value:    ${result.final_value:.2f}")
    print(f"  Total P&L:      ${result.total_pnl:.2f} ({result.total_pnl_pct:+.2f}%)")
    print(f"  Monthly Avg:    {result.monthly_avg_pct:+.2f}%/mo")
    print(f"  Annualized:     {result.annualized_pct:+.1f}%")
    print(f"  Fees Earned:    ${result.total_fees:.2f}")
    print(f"  IL Loss:        ${result.total_il:.2f}")
    print(f"  Rebalances:     {result.rebalance_count}")
    print(f"  Time In Range:  {result.time_in_range_pct:.1f}%")
    print(f"  Avg Pool APY:   {result.avg_apy_used:.1f}%")
    print(f"  Max Drawdown:   {result.worst_drawdown_pct:.2f}%")

    if result.daily_pnls:
        positive_days = sum(1 for d in result.daily_pnls if d["pnl_pct"] > 0)
        negative_days = sum(1 for d in result.daily_pnls if d["pnl_pct"] <= 0)
        best_day = max(result.daily_pnls, key=lambda d: d["pnl_pct"])
        worst_day = min(result.daily_pnls, key=lambda d: d["pnl_pct"])
        print(f"  Win/Loss Days:  {positive_days}/{negative_days}")
        print(f"  Best Day:       {best_day['date']} ({best_day['pnl_pct']:+.2f}%)")
        print(f"  Worst Day:      {worst_day['date']} ({worst_day['pnl_pct']:+.2f}%)")

    if len(result.daily_pnls) >= 7:
        weekly_chunks = []
        for i in range(0, len(result.daily_pnls), 7):
            week = result.daily_pnls[i:i+7]
            week_pnl = sum(d["pnl_pct"] for d in week)
            weekly_chunks.append(week_pnl)
        print(f"\n  Weekly Returns:")
        for i, w in enumerate(weekly_chunks):
            bar = "+" * int(max(w, 0) * 5) or "-" * int(abs(min(w, 0)) * 5)
            print(f"    Week {i+1:2d}: {w:+6.2f}% {bar}")

    print(f"{'=' * 70}")


async def main():
    parser = argparse.ArgumentParser(description="Alpha Engine Backtester")
    parser.add_argument("--days", type=int, default=30, help="Days to backtest (max 90)")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--range", type=float, default=0.025, help="LP range (0.025 = +/-2.5%%)")
    parser.add_argument("--compare", action="store_true", help="Compare multiple range widths")
    args = parser.parse_args()

    days = min(args.days, 90)
    print(f"Fetching {days} days of historical data...")

    prices, apy_data = await asyncio.gather(
        fetch_sol_prices(days),
        fetch_pool_apys(),
    )

    print(f"SOL prices: {len(prices)} hourly points")
    print(f"Pool APYs: {len(apy_data)} daily points")

    if args.compare:
        configs = [
            ("Hold SOL (no LP)", 1.0, 0.0, "hold"),
            ("Wide Range +/-10%", 0.10, 1.0, "tight_range"),
            ("Medium Range +/-5%", 0.05, 2.0, "tight_range"),
            ("Tight Range +/-2.5%", 0.025, 4.0, "tight_range"),
            ("Ultra Tight +/-1%", 0.01, 8.0, "tight_range"),
        ]
    else:
        avg_lp_range = 0.10
        mult = min(avg_lp_range / args.range, 8.0)
        configs = [
            (f"Tight Range +/-{args.range*100:.1f}%", args.range, mult, "tight_range"),
        ]

    for label, range_pct, mult, strategy in configs:
        if strategy == "hold":
            result = BacktestResult()
            result.period_days = int((prices[-1][0] - prices[0][0]) / 86400)
            result.start_price = prices[0][1]
            result.end_price = prices[-1][1]
            result.price_change_pct = (result.end_price - result.start_price) / result.start_price * 100
            result.capital = args.capital
            result.final_value = args.capital * (1 + result.price_change_pct / 100)
            result.total_pnl = result.final_value - args.capital
            result.total_pnl_pct = result.price_change_pct
            result.monthly_avg_pct = result.total_pnl_pct / result.period_days * 30 if result.period_days > 0 else 0
            result.annualized_pct = result.total_pnl_pct / result.period_days * 365 if result.period_days > 0 else 0
            print_result(result, label)
        else:
            result = run_backtest(prices, apy_data, args.capital, range_pct, mult, strategy)
            print_result(result, label)

    if not args.compare:
        result = run_backtest(prices, apy_data, args.capital, args.range, min(0.10 / args.range, 8.0), "tight_range")
        print(f"\nIf you started with ${args.capital:,.0f} {days} days ago:")
        print(f"  Just holding SOL: ${args.capital * (1 + result.price_change_pct/100):,.2f} ({result.price_change_pct:+.1f}%)")
        print(f"  LP strategy:      ${result.final_value:,.2f} ({result.total_pnl_pct:+.2f}%)")
        diff = result.total_pnl_pct - result.price_change_pct
        print(f"  Alpha over hold:  {diff:+.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
