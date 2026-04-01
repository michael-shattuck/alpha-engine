import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server.config import WEB_API_HOST, WEB_API_PORT, DEFAULT_MODE, DEFAULT_CAPITAL_ALLOCATION, HELIUS_RPC_URL
from server.orchestrator import Orchestrator
from server.strategies.leveraged_lp import LeveragedLPStrategy
from server.strategies.volatile_pairs import VolatilePairsStrategy
from server.strategies.adaptive_range import AdaptiveRangeStrategy
from server.strategies.funding_arb import FundingArbStrategy
from server.strategies.volatility_scalper import VolatilityScalper
from server.alerts import alerts

log = logging.getLogger("api")

orchestrator: Orchestrator = None
orchestrator_task: asyncio.Task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, orchestrator_task

    capital = float(app.state.capital) if hasattr(app.state, "capital") else 100.0
    mode = app.state.mode if hasattr(app.state, "mode") else DEFAULT_MODE

    orchestrator = Orchestrator(capital=capital, mode=mode)
    orchestrator.register_strategy(LeveragedLPStrategy(mode=mode), dormant=True)
    orchestrator.register_strategy(VolatilityScalper(mode=mode))
    orchestrator.register_strategy(VolatilePairsStrategy(mode=mode), dormant=True)
    orchestrator.register_strategy(AdaptiveRangeStrategy(mode=mode), dormant=True)
    orchestrator.register_strategy(FundingArbStrategy(mode=mode), dormant=True)

    orchestrator_task = asyncio.create_task(orchestrator.start())
    log.info(f"Orchestrator started: mode={mode}, capital=${capital:.2f}")

    yield

    await orchestrator.stop()
    orchestrator_task.cancel()
    try:
        await orchestrator_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Alpha Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@app.get("/api/status")
async def get_status():
    return orchestrator.get_status()


@app.get("/api/strategies")
async def get_strategies():
    return {
        sid: s.get_state() for sid, s in orchestrator.strategies.items()
    }


@app.get("/api/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    strategy = orchestrator.strategies.get(strategy_id)
    if not strategy:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    return strategy.get_state()


class ToggleRequest(BaseModel):
    enabled: bool


@app.post("/api/strategies/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str, req: ToggleRequest):
    success = orchestrator.toggle_strategy(strategy_id, req.enabled)
    if not success:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    return {"ok": True, "strategy": strategy_id, "enabled": req.enabled}


class AllocationRequest(BaseModel):
    allocations: dict[str, float]


@app.post("/api/config/allocation")
async def update_allocation(req: AllocationRequest):
    orchestrator.update_allocation(req.allocations)
    return {"ok": True, "allocations": req.allocations}


@app.get("/api/history")
async def get_history(limit: int = 1000):
    from server.config import DATABASE_URL
    if DATABASE_URL:
        from server.persistence import TradeStore
        return TradeStore.get_recent(limit=limit)
    history = orchestrator.state.portfolio.history
    return history[-limit:]


@app.get("/api/events")
async def get_events(limit: int = 200):
    events = orchestrator.state.portfolio.events
    return events[-limit:]


@app.get("/api/market")
async def get_market():
    return orchestrator.prices.get_market_data()


@app.get("/api/pools")
async def get_pools():
    return orchestrator.prices.get_best_pools(min_apy=20.0, limit=20)


class ForceRangeRequest(BaseModel):
    range_pct: float | None


@app.post("/api/config/force-range")
async def force_range(req: ForceRangeRequest):
    for s in orchestrator.strategies.values():
        if hasattr(s, '_force_range'):
            s._force_range = req.range_pct
    return {"ok": True, "force_range": req.range_pct}


@app.get("/api/intelligence")
async def get_intelligence():
    return {
        "reasoning": orchestrator.ai.get_reasoning_summary(),
        "recent_decisions": orchestrator.ai.get_recent_decisions(10),
        "guardian": orchestrator.guardian._last_assessment,
        "rebalance_frequency_24h": orchestrator.ai.rebalancer.rebalance_frequency(24),
        "rebalance_cost_24h": orchestrator.ai.rebalancer.total_rebalance_cost(24),
    }


class LeverageRequest(BaseModel):
    leverage: float


@app.post("/api/config/leverage")
async def set_leverage(req: LeverageRequest):
    lev = max(1.0, min(req.leverage, 5.0))
    for s in orchestrator.strategies.values():
        if hasattr(s, "base_leverage"):
            s.base_leverage = lev
    orchestrator.state.add_event("leverage_change", "api", {"leverage": lev})
    return {"ok": True, "leverage": lev}


@app.post("/api/emergency-exit")
async def emergency_exit():
    market_data = orchestrator.prices.get_market_data()
    await orchestrator._emergency_exit(market_data)
    orchestrator.state.add_event("emergency_exit", "api", {"manual": True})
    return {"ok": True, "message": "All positions closed"}


@app.post("/api/emergency-exit/{strategy_id}")
async def emergency_exit_strategy(strategy_id: str):
    strategy = orchestrator.strategies.get(strategy_id)
    if not strategy:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    market_data = orchestrator.prices.get_market_data()
    for pos in list(strategy.active_positions):
        action = "deleverage" if hasattr(strategy, "base_leverage") else "close"
        await strategy.execute({"action": action, "position_id": pos.id}, market_data)
    strategy.enabled = False
    orchestrator.state.add_event("emergency_exit", strategy_id, {"manual": True})
    return {"ok": True, "strategy": strategy_id}


@app.get("/api/exit-cost")
async def estimate_exit_cost():
    total_deployed = 0
    total_borrowed = 0
    for s in orchestrator.strategies.values():
        state = s.get_state()
        m = state.get("metrics", {})
        total_deployed += state.get("current_value", 0)
        total_borrowed += m.get("borrowed", 0)

    swap_slippage = total_deployed * 0.001
    tx_fees = 0.01
    total_cost = swap_slippage + tx_fees

    return {
        "total_deployed": total_deployed,
        "total_borrowed": total_borrowed,
        "estimated_swap_slippage": swap_slippage,
        "estimated_tx_fees": tx_fees,
        "estimated_total_cost": total_cost,
        "cost_percent": (total_cost / total_deployed * 100) if total_deployed > 0 else 0,
    }


@app.get("/api/wallet")
async def get_wallet():
    lp = orchestrator.strategies.get("leveraged_lp")
    sol_balance = 0.0
    usdc_balance = 0.0
    marginfi_state = {"deposited_sol": 0, "borrowed_usdc": 0, "has_position": False}

    if lp and hasattr(lp, "orca") and lp.orca and lp.orca.rpc:
        try:
            from solders.pubkey import Pubkey
            bal = await lp.orca.rpc.get_balance(lp.orca.keypair.pubkey())
            sol_balance = bal.value / 1e9

            usdc_mint = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
            token_prog = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            ata_prog = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
            usdc_ata, _ = Pubkey.find_program_address(
                [bytes(lp.orca.keypair.pubkey()), bytes(token_prog), bytes(usdc_mint)], ata_prog
            )
            import httpx
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.post(HELIUS_RPC_URL, json={
                    "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountBalance",
                    "params": [str(usdc_ata)]
                })
                result = r.json().get("result", {}).get("value", {})
                usdc_balance = float(result.get("uiAmount", 0) or 0)
        except Exception:
            pass

    if lp and hasattr(lp, "lender") and lp.lender:
        marginfi_state = lp.lender.get_state()

    return {
        "sol_balance": sol_balance,
        "usdc_balance": usdc_balance,
        "sol_price": orchestrator.prices.sol_price,
        "total_usd": sol_balance * orchestrator.prices.sol_price + usdc_balance,
        "marginfi": marginfi_state,
    }


@app.get("/api/scalper")
async def get_scalper():
    scalper = orchestrator.strategies.get("volatility_scalper")
    if not scalper:
        return {}
    state = scalper.get_state()
    metrics = state.get("metrics", {})

    assets = []
    for asset, engine in scalper.engines.items():
        price = scalper._asset_prices.get(asset, 0)
        assessment = engine.regime_detector._last_assessment
        snap = engine.get_indicator_snapshot() if engine.is_warmed_up else {}
        active_trade = next((t for t in scalper._active_trades if t.get("asset") == asset and t["status"] == "active"), None)
        signal = engine.evaluate(price) if engine.is_warmed_up and price > 0 else None

        profile = scalper.learner.get_profile(asset)
        assets.append({
            "symbol": asset,
            "price": price,
            "regime": assessment.regime.value if assessment else "unknown",
            "regime_confidence": assessment.confidence if assessment else 0,
            "rsi_5m": snap.get("rsi_5m", 50),
            "adx": snap.get("adx_1h", 0),
            "bbw": snap.get("bb_width_1h", 0),
            "velocity": snap.get("velocity_5m", 0),
            "signal": signal.type.value if signal else "none",
            "signal_confidence": signal.confidence if signal else 0,
            "signal_reason": signal.reason if signal else "",
            "active_trade": {
                "direction": active_trade["direction"],
                "entry_price": active_trade["entry_price"],
                "pnl_pct": active_trade["pnl_pct"],
                "stop_loss": active_trade["stop_loss"],
                "take_profit": active_trade["take_profit"],
            } if active_trade else None,
            "learner": {
                "trades": profile.trades,
                "win_rate": profile.win_rate,
                "pain": profile.pain,
                "regret": profile.regret,
                "size_mult": profile.size_multiplier,
                "conf_adj": profile.confidence_adjustment,
                "tp_mult": profile.tp_multiplier,
                "short_penalty": profile.short_penalty,
                "consecutive_losses": profile.consecutive_losses,
                "consecutive_wins": profile.consecutive_wins,
            } if profile.trades > 0 else None,
        })

    drift_account = None
    if scalper.drift and scalper.drift.client:
        try:
            user = scalper.drift.client.get_user()
            collateral = user.get_total_collateral() / 1e6
            upnl = user.get_unrealized_pnl() / 1e6
            drift_account = {
                "collateral": collateral,
                "unrealized_pnl": upnl,
                "net_value": collateral + upnl,
                "starting_capital": scalper.capital_allocated,
                "total_pnl": (collateral + upnl) - scalper.capital_allocated,
            }
        except Exception:
            pass

    return {
        "assets": assets,
        "active_trades": state.get("active_trades", []),
        "daily_stats": state.get("daily_stats", {}),
        "drift_account": drift_account,
        "indicators": state.get("indicators", {}),
        "signal_performance": state.get("signal_performance", {}),
        "regime": metrics.get("regime", "unknown"),
        "regime_confidence": metrics.get("regime_confidence", 0),
        "asset_regimes": metrics.get("asset_regimes", {}),
    }


@app.get("/api/allocator")
async def get_allocator():
    if not hasattr(orchestrator, "allocator") or not orchestrator.allocator:
        return {"status": "not_configured"}
    return orchestrator.allocator.get_state()


@app.get("/api/portfolio")
async def get_portfolio():
    sol_price = orchestrator.prices.sol_price

    # Wallet balances
    sol_balance = 0.0
    usdc_balance = 0.0
    try:
        import base58, httpx
        from solders.pubkey import Pubkey
        from solders.keypair import Keypair
        from server.config import WALLET_PRIVATE_KEY
        kp = Keypair.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))
        w = kp.pubkey()
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(HELIUS_RPC_URL, json={
                "jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [str(w)]
            })
            sol_balance = r.json().get("result", {}).get("value", 0) / 1e9

            usdc_mint = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
            token_prog = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            ata_prog = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
            usdc_ata, _ = Pubkey.find_program_address([bytes(w), bytes(token_prog), bytes(usdc_mint)], ata_prog)
            r2 = await http.post(HELIUS_RPC_URL, json={
                "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountBalance",
                "params": [str(usdc_ata)]
            })
            result = r2.json().get("result", {}).get("value", {})
            usdc_balance = float(result.get("uiAmount", 0) or 0)
    except Exception:
        pass

    wallet_usd = sol_balance * sol_price + usdc_balance

    # Drift account
    drift_collateral = 0.0
    drift_free = 0.0
    drift_pnl = 0.0
    drift_positions = []
    try:
        from driftpy.drift_client import DriftClient
        from solana.rpc.async_api import AsyncClient as DriftRpc
        from solana.rpc.commitment import Confirmed
        import base58
        from solders.keypair import Keypair as DriftKp
        from server.config import WALLET_PRIVATE_KEY
        conn = DriftRpc(HELIUS_RPC_URL, commitment=Confirmed)
        kp = DriftKp.from_bytes(base58.b58decode(WALLET_PRIVATE_KEY))
        dc = DriftClient(conn, kp)
        await dc.subscribe()
        user = dc.get_user()
        if user:
            drift_collateral = user.get_total_collateral() / 1e6
            drift_free = user.get_free_collateral() / 1e6
            drift_pnl = user.get_unrealized_pnl(True) / 1e6
            for i in range(len(user.get_user_account().perp_positions)):
                pos = user.get_user_account().perp_positions[i]
                if pos.base_asset_amount != 0:
                    base = abs(pos.base_asset_amount) / 1e9
                    entry_notional = abs(pos.quote_entry_amount) / 1e6
                    entry_price = entry_notional / base if base > 0 else 0
                    try:
                        oracle = dc.get_oracle_price_data_for_perp_market(pos.market_index)
                        oracle_price = oracle.price / 1e6
                        if pos.base_asset_amount > 0:
                            pos_pnl = (oracle_price - entry_price) * base
                        else:
                            pos_pnl = (entry_price - oracle_price) * base
                    except Exception:
                        pos_pnl = 0
                    drift_positions.append({
                        "market_index": pos.market_index,
                        "direction": "long" if pos.base_asset_amount > 0 else "short",
                        "size_tokens": base,
                        "entry_price": entry_price,
                        "notional": entry_notional,
                        "pnl": pos_pnl,
                    })
        await dc.unsubscribe()
        await conn.close()
    except Exception:
        pass

    total_usd = wallet_usd + drift_collateral

    return {
        "sol_price": sol_price,
        "wallet": {
            "sol_balance": sol_balance,
            "sol_usd": sol_balance * sol_price,
            "usdc_balance": usdc_balance,
            "total_usd": wallet_usd,
        },
        "drift": {
            "collateral": drift_collateral,
            "free_collateral": drift_free,
            "unrealized_pnl": drift_pnl,
            "positions": drift_positions,
        },
        "total_usd": total_usd,
    }


@app.get("/api/lifecycle")
async def get_lifecycle():
    lp = orchestrator.strategies.get("leveraged_lp")
    if lp and hasattr(lp, "lifecycle") and lp.lifecycle:
        return lp.lifecycle.get_state()
    return {"phase": "idle", "error": ""}


@app.get("/api/optimizer")
async def get_optimizer():
    lp = orchestrator.strategies.get("leveraged_lp")
    if not lp:
        return {}
    pool_apy = lp.metrics.get("pool_apy", 50.0)
    vol = lp._volatility()
    trend = lp._trend()
    from server.strategies.optimizer import optimize_for_floor, rank_pools
    opt = optimize_for_floor(pool_apy, vol, lp.base_leverage, lp.RETURN_FLOOR_MONTHLY)
    pools = rank_pools(orchestrator.prices.pool_apys, vol, lp.base_leverage, lp.RETURN_FLOOR_MONTHLY)
    actual_fee_apy = 0
    if lp.fee_tracker and lp.active_positions:
        actual_fee_apy = lp.fee_tracker.get_actual_apy(lp.active_positions[0].deposit_usd)
    return {
        "current_pool_apy": pool_apy,
        "volatility": vol,
        "trend_1h": trend,
        "optimized": opt,
        "actual_fee_apy": actual_fee_apy,
        "return_floor": lp.RETURN_FLOOR_MONTHLY,
        "ranked_pools": pools[:5],
    }


@app.get("/api/alerts")
async def get_alerts(limit: int = 100):
    return alerts.get_history(limit)


@app.get("/api/health")
async def get_health():
    now = __import__("time").time()
    last_price = orchestrator.prices.last_price_update
    price_stale = (now - last_price) > 120 if last_price > 0 else True
    return {
        "status": "ok" if not price_stale else "degraded",
        "price_stale": price_stale,
        "last_price_update": last_price,
        "seconds_since_price": now - last_price if last_price > 0 else -1,
        "uptime_hours": (now - orchestrator.state.portfolio.started_at) / 3600,
        "circuit_breaker": orchestrator.state.portfolio.circuit_breaker_active,
        "strategies_with_errors": [
            sid for sid, s in orchestrator.strategies.items() if s.error
        ],
    }


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        file_path = FRONTEND_DIST / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
