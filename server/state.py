import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from server.config import STATE_DIR


@dataclass
class Position:
    id: str
    strategy: str
    pool: str
    entry_price: float
    lower_price: float
    upper_price: float
    deposit_usd: float
    current_value_usd: float
    fees_earned_usd: float
    sol_amount: float
    usdc_amount: float
    opened_at: float
    last_update: float
    status: str = "active"
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


@dataclass
class StrategyState:
    id: str
    name: str
    enabled: bool = True
    mode: str = "paper"
    capital_allocated: float = 0.0
    target_allocation: float = 0.0
    current_value: float = 0.0
    total_fees: float = 0.0
    total_pnl: float = 0.0
    positions: list = field(default_factory=list)
    status: str = "idle"
    last_update: float = 0.0
    error: str = ""
    metrics: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    timestamp: float
    total_value: float
    total_pnl: float
    total_pnl_percent: float
    strategy_values: dict = field(default_factory=dict)
    risk_level: str = "low"
    sol_price: float = 0.0


@dataclass
class PortfolioState:
    total_capital: float = 0.0
    total_value: float = 0.0
    total_pnl: float = 0.0
    mode: str = "paper"
    strategies: dict = field(default_factory=dict)
    risk_level: str = "low"
    circuit_breaker_active: bool = False
    started_at: float = field(default_factory=time.time)
    last_update: float = 0.0
    history: list = field(default_factory=list)
    events: list = field(default_factory=list)


STATE_FILE = STATE_DIR / "portfolio.json"
HISTORY_FILE = STATE_DIR / "history.json"
EVENTS_FILE = STATE_DIR / "events.json"


class StateManager:
    def __init__(self):
        self.portfolio = PortfolioState()
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.portfolio = PortfolioState(**{
                    k: v for k, v in data.items()
                    if k in PortfolioState.__dataclass_fields__
                })
            except (json.JSONDecodeError, TypeError):
                pass
        if HISTORY_FILE.exists():
            try:
                self.portfolio.history = json.loads(HISTORY_FILE.read_text())
            except (json.JSONDecodeError, TypeError):
                pass
        if EVENTS_FILE.exists():
            try:
                self.portfolio.events = json.loads(EVENTS_FILE.read_text())
            except (json.JSONDecodeError, TypeError):
                pass

    def save(self):
        STATE_DIR.mkdir(exist_ok=True)
        portfolio_data = asdict(self.portfolio)
        history = portfolio_data.pop("history", [])
        events = portfolio_data.pop("events", [])
        STATE_FILE.write_text(json.dumps(portfolio_data, indent=2))
        HISTORY_FILE.write_text(json.dumps(history[-10000:], indent=2))
        EVENTS_FILE.write_text(json.dumps(events[-5000:], indent=2))

    def add_snapshot(self, sol_price: float = 0.0):
        total_value = sum(
            s.get("current_value", 0) for s in self.portfolio.strategies.values()
        )
        self.portfolio.total_value = total_value
        self.portfolio.total_pnl = total_value - self.portfolio.total_capital

        pnl_pct = 0.0
        if self.portfolio.total_capital > 0:
            pnl_pct = (self.portfolio.total_pnl / self.portfolio.total_capital) * 100

        snap = {
            "timestamp": time.time(),
            "total_value": total_value,
            "total_pnl": self.portfolio.total_pnl,
            "total_pnl_percent": pnl_pct,
            "strategy_values": {
                k: v.get("current_value", 0)
                for k, v in self.portfolio.strategies.items()
            },
            "risk_level": self.portfolio.risk_level,
            "sol_price": sol_price,
        }
        self.portfolio.history.append(snap)
        self.portfolio.last_update = time.time()
        self.save()

    def add_event(self, event_type: str, strategy: str, data: dict):
        self.portfolio.events.append({
            "timestamp": time.time(),
            "type": event_type,
            "strategy": strategy,
            "data": data,
        })

    def get_strategy(self, strategy_id: str) -> Optional[dict]:
        return self.portfolio.strategies.get(strategy_id)

    def set_strategy(self, strategy_id: str, state: dict):
        self.portfolio.strategies[strategy_id] = state
        self.save()
