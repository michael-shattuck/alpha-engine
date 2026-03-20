import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server.config import WEB_API_HOST, WEB_API_PORT, DEFAULT_MODE, DEFAULT_CAPITAL_ALLOCATION
from server.orchestrator import Orchestrator
from server.strategies.leveraged_lp import LeveragedLPStrategy
from server.strategies.volatile_pairs import VolatilePairsStrategy
from server.strategies.adaptive_range import AdaptiveRangeStrategy
from server.strategies.funding_arb import FundingArbStrategy

log = logging.getLogger("api")

orchestrator: Orchestrator = None
orchestrator_task: asyncio.Task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, orchestrator_task

    capital = float(app.state.capital) if hasattr(app.state, "capital") else 100.0
    mode = app.state.mode if hasattr(app.state, "mode") else DEFAULT_MODE

    orchestrator = Orchestrator(capital=capital, mode=mode)
    orchestrator.register_strategy(LeveragedLPStrategy(mode=mode))
    orchestrator.register_strategy(VolatilePairsStrategy(mode=mode))
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


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        file_path = FRONTEND_DIST / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")
