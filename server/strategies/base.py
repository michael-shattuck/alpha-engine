import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class StrategyPosition:
    id: str
    pool: str
    entry_price: float
    lower_price: float = 0.0
    upper_price: float = 0.0
    deposit_usd: float = 0.0
    current_value_usd: float = 0.0
    fees_earned_usd: float = 0.0
    il_percent: float = 0.0
    sol_amount: float = 0.0
    usdc_amount: float = 0.0
    opened_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    status: str = "active"
    in_range: bool = True
    hours_in_range: float = 0.0
    hours_out_of_range: float = 0.0
    rebalance_count: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        return self.current_value_usd - self.deposit_usd + self.fees_earned_usd

    @property
    def pnl_percent(self) -> float:
        if self.deposit_usd <= 0:
            return 0
        return (self.pnl / self.deposit_usd) * 100

    @property
    def age_hours(self) -> float:
        return (time.time() - self.opened_at) / 3600


class BaseStrategy(ABC):
    STRATEGY_ID: str = ""
    STRATEGY_NAME: str = ""

    def __init__(self, mode: str = "paper"):
        self.mode = mode
        self.enabled = True
        self.capital_allocated = 0.0
        self.target_allocation = 0.0
        self.positions: list[StrategyPosition] = []
        self.status = "idle"
        self.error = ""
        self.metrics: dict = {}
        self.last_update = 0.0

    @abstractmethod
    async def evaluate(self, market_data: dict) -> dict:
        """Evaluate current conditions. Return recommended actions."""

    @abstractmethod
    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        """Execute an action (open, close, rebalance). Returns affected position."""

    @abstractmethod
    async def update(self, market_data: dict):
        """Update all positions with current market data."""

    def get_state(self) -> dict:
        return {
            "id": self.STRATEGY_ID,
            "name": self.STRATEGY_NAME,
            "enabled": self.enabled,
            "mode": self.mode,
            "capital_allocated": self.capital_allocated,
            "target_allocation": self.target_allocation,
            "current_value": self.current_value,
            "total_fees": self.total_fees,
            "total_pnl": self.total_pnl,
            "total_pnl_percent": self.total_pnl_percent,
            "positions": [asdict(p) for p in self.positions if p.status == "active"],
            "position_count": len([p for p in self.positions if p.status == "active"]),
            "status": self.status,
            "last_update": self.last_update,
            "error": self.error,
            "metrics": self.metrics,
        }

    def load_state(self, state: dict):
        self.enabled = state.get("enabled", True)
        self.mode = state.get("mode", self.mode)
        saved_capital = state.get("capital_allocated", 0.0)
        if saved_capital > self.capital_allocated:
            self.capital_allocated = saved_capital
        self.target_allocation = state.get("target_allocation", 0.0)
        self.status = state.get("status", "idle")
        self.error = state.get("error", "")
        self.metrics = state.get("metrics", {})
        self.last_update = state.get("last_update", 0.0)
        self.positions = [
            StrategyPosition(**p) for p in state.get("positions", [])
        ]

    @property
    def current_value(self) -> float:
        active = [p for p in self.positions if p.status == "active"]
        if not active:
            return self.capital_allocated
        return sum(p.current_value_usd + p.fees_earned_usd for p in active)

    @property
    def total_fees(self) -> float:
        return sum(p.fees_earned_usd for p in self.positions)

    @property
    def total_pnl(self) -> float:
        return sum(p.pnl for p in self.positions if p.status == "active")

    @property
    def total_pnl_percent(self) -> float:
        if self.capital_allocated <= 0:
            return 0
        return (self.total_pnl / self.capital_allocated) * 100

    @property
    def active_positions(self) -> list[StrategyPosition]:
        return [p for p in self.positions if p.status == "active"]

    def close_position(self, position_id: str):
        for p in self.positions:
            if p.id == position_id:
                p.status = "closed"
                p.last_update = time.time()
                return p
        return None
