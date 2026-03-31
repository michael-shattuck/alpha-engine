import logging

log = logging.getLogger("optimizer")

BORROW_RATE_APY = 12.0

WHIRLPOOL_REGISTRY = {
    "orca_sol_usdc": {
        "address": "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
        "token_a": "SOL",
        "token_b": "USDC",
        "base_range": 0.03,
        "min_range": 0.02,
        "max_range": 0.15,
        "il_factor": 1.0,
    },
}


def score_pool(pool_apy: float, volatility: float, leverage: float, range_pct: float) -> dict:
    concentration = min(0.10 / range_pct, 6.0) if range_pct > 0 else 1
    gross_apy = pool_apy * concentration * leverage
    borrow_cost = BORROW_RATE_APY * (leverage - 1)

    daily_vol = volatility * (480 ** 0.5)
    if daily_vol > 0 and range_pct > 0:
        rebalances_per_day = max(daily_vol / range_pct * 0.3, 0)
    else:
        rebalances_per_day = 0.1

    swap_slippage = 0.10
    tx_fees = 0.02
    il_at_exit = range_pct * 0.15 * leverage
    cost_per_rebalance_pct = swap_slippage * 2 + tx_fees + il_at_exit
    rebalance_cost_annual = rebalances_per_day * cost_per_rebalance_pct * 365

    net_apy = gross_apy - borrow_cost - rebalance_cost_annual
    monthly = net_apy / 12

    return {
        "gross_apy": gross_apy,
        "borrow_cost": borrow_cost,
        "rebalance_cost": rebalance_cost_annual,
        "rebalances_per_day": rebalances_per_day,
        "net_apy": net_apy,
        "monthly": monthly,
        "concentration": concentration,
    }


def optimize_for_floor(pool_apy: float, volatility: float, max_leverage: float,
                       floor_monthly: float = 30.0) -> dict:
    best = None
    best_monthly = -999

    for lev_10x in range(10, int(max_leverage * 10) + 1):
        lev = lev_10x / 10.0
        for rng_100x in range(200, 1501, 50):
            rng = rng_100x / 10000.0

            result = score_pool(pool_apy, volatility, lev, rng)

            if result["monthly"] >= floor_monthly and result["monthly"] > best_monthly:
                best_monthly = result["monthly"]
                best = {
                    "leverage": lev,
                    "range_pct": rng,
                    **result,
                }

    if not best:
        max_result = {"monthly": -999}
        for lev_10x in range(10, int(max_leverage * 10) + 1):
            lev = lev_10x / 10.0
            for rng_100x in range(200, 1501, 50):
                rng = rng_100x / 10000.0
                result = score_pool(pool_apy, volatility, lev, rng)
                if result["monthly"] > max_result["monthly"]:
                    max_result = {"leverage": lev, "range_pct": rng, **result}
        best = max_result
        log.warning(
            f"Floor {floor_monthly}% unreachable at {pool_apy:.0f}% APY. "
            f"Best: {best['monthly']:.1f}%/mo at {best['leverage']}x/{best['range_pct']*100:.1f}%"
        )

    return best


def rank_pools(pool_apys: dict, volatility: float, max_leverage: float,
               floor_monthly: float = 30.0) -> list[dict]:
    ranked = []
    for pool_key, apy in pool_apys.items():
        if apy < 10:
            continue
        registry = WHIRLPOOL_REGISTRY.get(pool_key)
        if not registry:
            continue

        opt = optimize_for_floor(apy, volatility, max_leverage, floor_monthly)
        opt["pool"] = pool_key
        opt["pool_apy"] = apy
        opt["address"] = registry["address"]
        ranked.append(opt)

    ranked.sort(key=lambda x: x["monthly"], reverse=True)
    return ranked
