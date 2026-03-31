# Volatility Scalper Strategy -- Implementation Specification

## Overview

A new strategy for the alpha engine that actively trades intraday volatility on SOL and other assets using leveraged long/short positions. Unlike the existing `LeveragedLP` strategy (passive yield farming), this strategy makes directional bets on short-timeframe price swings.

**Target**: 3-5% daily returns via leveraged scalping of 1-3% intraday price swings.

**Phases**:
1. SOL/USDC leveraged scalping via MarginFi (long) + reverse-borrow (short) + Orca swaps
2. Drift Protocol perps integration (BTC, ETH, SOL -- native leverage + shorting)
3. Solana altcoins (JUP, PYTH, JTO, W) via Orca/Raydium spot + MarginFi leverage
4. Non-crypto assets via Drift (XAU, EUR/USD) for uncorrelated returns

This spec covers Phase 1 fully and the architecture to support Phases 2-4.

---

## The Math

SOL's daily range typically spans 3-8% (high to low). On volatile days, 10%+.

With 3x leverage (MarginFi):
- Capture 30% of a 5% daily range = 1.5% * 3x = **4.5%/day**
- Capture 50% of a 5% daily range = 2.5% * 3x = **7.5%/day**
- On a quiet 3% day at 30% capture = 0.9% * 3x = **2.7%/day**
- Realistic average: **3-5%/day**

The force multiplier: **shorting both legs of every swing**. A 3% oscillation = short the top (1.5% capture) + long the bottom (1.5% capture) = 3% at 1x, **9% at 3x**. Even at 30% capture efficiency that's 2.7% on a single swing.

During active trading sessions (US + Asia, ~12h/day), 1%/hour is achievable. Not 24/7.

---

## Combined System: LP + Scalper

The LP and scalper are negatively correlated on regime. When one struggles, the other thrives.

### Dynamic Capital Allocation

Capital flows between strategies based on market regime. Rebalance only when regime has been stable for 30+ minutes (1-3 rebalances/day max, ~0.2% cost each).

| Regime | LP Allocation | Scalper Allocation |
|---|---|---|
| Dead flat (no vol) | 90% | 10% (standby) |
| Normal ranging | 50% | 50% |
| High vol / trending | 30% | 70% |
| Crash | 20% | 80% (shorts) |

### Projected Returns by Regime

| Regime | LP Contribution | Scalper Contribution | Combined Daily | Combined Monthly |
|---|---|---|---|---|
| Dead flat | 90% at 2.6%/d = 2.3%/d | idle | **2.3%/d** | **70%** |
| Normal ranging | 50% at 2.0%/d = 1.0%/d | 50% at 3-4%/d = 1.5-2.0%/d | **2.5-3.0%/d** | **75-90%** |
| High vol crypto | 30% at 1.0%/d = 0.3%/d | 70% at 4-6%/d = 2.8-4.2%/d | **3.1-4.5%/d** | **93-135%** |
| Quiet + macro vol | 60% at 1.5%/d = 0.9%/d | 40% at 1.5-2.5%/d = 0.6-1.0%/d | **1.5-1.9%/d** | **45-57%** |
| Crash (shorts) | 20% at -0.5%/d = -0.1%/d | 80% at 3-5%/d = 2.4-4.0%/d | **2.3-3.9%/d** | **69-117%** |

LP math at 4x leverage, 2% range, 50% pool APY:
- Concentration: 5x (0.10/0.02)
- Effective: 50% * 5 * 4 = 1000%
- Minus borrow: 12% * 3 = 36%
- Net: 964% / 365 = **2.6%/day**

**Floor: ~70%/month** (dead flat, all capital in LP). This is the worst case -- no volatility at all, which rarely lasts more than a day.

### Multi-Asset Diversification (Phases 2-4)

For days when crypto is completely flat:

| Asset Class | Correlation to SOL | Volatility | Available on Pyth | Execution |
|---|---|---|---|---|
| SOL | 1.0 | High (3-8%/day) | Yes | MarginFi + Orca |
| ETH | ~0.75 | Medium (2-5%/day) | Yes | Drift perps |
| BTC | ~0.80 | Medium (2-4%/day) | Yes | Drift perps |
| Gold (XAU) | ~0.1 | Low-Med (0.5-2%/day) | Yes | Drift perps |
| EUR/USD | ~-0.3 | Low (0.3-1%/day) | Yes | Drift perps |
| Altcoins (JUP, JTO, BONK) | 0.5-0.7 | Very high (5-15%/day) | Yes | Orca/Raydium |

With diversification, even "quiet crypto + macro vol" days have tradeable assets. The floor rises further because there's always something moving somewhere.

---

## Architecture

### New Files

```
server/
  strategies/
    volatility_scalper.py        # Main strategy (BaseStrategy subclass)
    allocator.py                 # Dynamic allocation between LP and scalper
  execution/
    drift.py                     # Phase 2: Drift Protocol perp execution
  signals/
    __init__.py
    engine.py                    # SignalEngine: generates entry/exit signals
    indicators.py                # Technical indicators (RSI, BB, VWAP, EMA, ADX)
    regime.py                    # Market regime detector (trending/ranging/volatile_ranging/dead)
    candles.py                   # OHLCV candle aggregator from tick data
```

### Modified Files

```
server/config.py                 # New constants for scalper params
server/web_api.py                # Register new strategy in lifespan()
server/orchestrator.py           # Dynamic allocation hooks
```

### Unchanged (reused as-is)

```
server/strategies/base.py        # BaseStrategy interface -- subclass it
server/execution/prices.py       # PriceService -- already provides 10s SOL updates
server/execution/orca.py         # OrcaExecutor.swap() -- used for spot entry/exit
server/execution/marginfi.py     # MarginFiLender -- used for leverage
server/execution/lifecycle.py    # State machine -- adapted for scalper trade flows
server/risk/guardian.py          # Guardian -- portfolio-level risk still applies
server/intelligence.py           # AIOrchestrator -- can flag emergency actions
server/state.py                  # StateManager -- persists strategy state
```

---

## Phase 1: SOL/USDC Leveraged Scalping

### How It Works

1. The **SignalEngine** continuously processes 10-second price ticks from PriceService, aggregates them into 1m/5m/15m/1h OHLCV candles, and computes technical indicators.

2. The **RegimeDetector** classifies the current market into one of four regimes:
   - **TRENDING_UP**: Clear upward momentum. Take only long momentum entries.
   - **TRENDING_DOWN**: Clear downward momentum. Take only short momentum entries.
   - **RANGING**: Low directional vol, price oscillating in a band. Mean-revert both directions.
   - **VOLATILE_RANGING**: High volatility but no direction. Aggressive mean reversion -- this is where the scalper prints money. Price swings back and forth across bands repeatedly.
   - **DEAD**: No volatility at all (BBW < 0.02, ATR declining). No trades -- capital should flow to LP via dynamic allocation.

3. Based on regime + signals, the strategy enters **long** or **short** positions with 3x leverage:
   - **Ranging / volatile ranging**: Mean-revert. Buy RSI oversold / lower Bollinger band. Sell RSI overbought / upper Bollinger band. Capture both legs of every swing.
   - **Trending market**: Ride momentum. Long on uptrend pullbacks/breakouts, short on downtrend rallies/breakdowns. Trailing stop.
   - **Dead market**: No trades. Capital moves to LP.

4. Each trade has a defined stop-loss and take-profit set at entry time.

5. Multiple trades per day (target 3-8 round trips depending on volatility). During active sessions, up to 1 trade per hour.

### Long Flow (using existing MarginFi + Orca)

Opening a leveraged long:
1. Deposit USDC to MarginFi as collateral
2. Borrow additional USDC (for 3x: borrow 2x your equity)
3. Swap all USDC -> SOL via Orca (`a_to_b=False`)
4. Position is now: long SOL, owe USDC

Closing a long:
1. Swap SOL -> USDC via Orca (`a_to_b=True`)
2. Repay USDC borrow to MarginFi
3. Withdraw remaining USDC collateral (profit or loss)

### Short Flow (reverse of long -- NEW)

Opening a leveraged short:
1. Deposit USDC to MarginFi as collateral
2. Borrow SOL from MarginFi (using SOL bank, not USDC bank)
3. Swap borrowed SOL -> USDC via Orca (`a_to_b=True`)
4. Position is now: holding USDC, owe SOL. Profit if SOL price drops.

Closing a short:
1. Swap USDC -> SOL via Orca (`a_to_b=False`) to get enough SOL to repay
2. Repay SOL borrow to MarginFi
3. Withdraw remaining USDC collateral (profit or loss)

**MarginFi already supports borrowing SOL** -- the SOL bank (`CCKtUs6Cgwo4aaQUmBPmyoApH2gUDErxNZCAntD6LYGh`) is already configured in `marginfi.py`. The existing `deposit_and_borrow` and `repay_and_withdraw` methods handle USDC borrowing; the short flow needs equivalent methods that borrow SOL instead.

---

## Component Specifications

### 1. `server/signals/candles.py` -- CandleAggregator

Converts raw price ticks (10-second intervals from PriceService) into OHLCV candles at multiple timeframes.

```python
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"


@dataclass
class Candle:
    timestamp: float       # candle open time (unix)
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0    # not available from Pyth, will be 0 unless we get it elsewhere
    closed: bool = False    # True when candle period has elapsed


class CandleAggregator:
    TIMEFRAME_SECONDS = {
        Timeframe.M1: 60,
        Timeframe.M5: 300,
        Timeframe.M15: 900,
        Timeframe.H1: 3600,
        Timeframe.H4: 14400,
    }

    MAX_CANDLES = {
        Timeframe.M1: 1440,     # 24 hours
        Timeframe.M5: 576,      # 48 hours
        Timeframe.M15: 384,     # 4 days
        Timeframe.H1: 168,      # 7 days
        Timeframe.H4: 180,      # 30 days
    }

    def __init__(self):
        self._candles: dict[Timeframe, list[Candle]] = defaultdict(list)
        self._current: dict[Timeframe, Candle | None] = {tf: None for tf in Timeframe}

    def on_tick(self, price: float, timestamp: float):
        """Feed a price tick. Updates all timeframe candles."""

    def get_candles(self, timeframe: Timeframe, count: int = 100) -> list[Candle]:
        """Return the last `count` closed candles for a timeframe."""

    def get_current(self, timeframe: Timeframe) -> Candle | None:
        """Return the current in-progress candle for a timeframe."""

    def get_closes(self, timeframe: Timeframe, count: int = 100) -> list[float]:
        """Convenience: return just the close prices for indicator calculation."""

    def get_highs(self, timeframe: Timeframe, count: int = 100) -> list[float]:
        """Convenience: return just the high prices."""

    def get_lows(self, timeframe: Timeframe, count: int = 100) -> list[float]:
        """Convenience: return just the low prices."""

    def get_state(self) -> dict:
        """For persistence across restarts."""

    def load_state(self, state: dict):
        """Restore from persistence."""
```

**Key behaviors:**
- Candle boundaries are aligned to clock time (e.g., 5m candles start at :00, :05, :10...)
- `on_tick` is called ~6 times per candle on the 1m timeframe (every 10s). High/low/close update every tick.
- All timeframes update simultaneously from the same tick stream.
- Candles must survive strategy restarts via get_state()/load_state().

---

### 2. `server/signals/indicators.py` -- Technical Indicators

Pure functions that compute indicators from candle data. No state, no side effects.

```python
def rsi(closes: list[float], period: int = 14) -> float:
    """Relative Strength Index. Returns 0-100, 50.0 if insufficient data."""

def rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """RSI for each point (for divergence detection)."""

def bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2.0) -> tuple[float, float, float]:
    """Returns (lower, middle, upper). (0, 0, 0) if insufficient data."""

def bollinger_band_width(closes: list[float], period: int = 20, std_dev: float = 2.0) -> float:
    """BBW = (upper - lower) / middle. Detects volatility squeeze."""

def ema(values: list[float], period: int) -> float:
    """Exponential Moving Average. Returns 0.0 if insufficient data."""

def ema_series(values: list[float], period: int) -> list[float]:
    """EMA for each point."""

def vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> float:
    """Volume-Weighted Average Price. Falls back to equal-weighted typical price."""

def vwap_from_candles(candles: list) -> float:
    """VWAP computed directly from Candle objects."""

def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average Directional Index. 0-100. <20=ranging, 20-40=trending, >40=strong trend."""

def adx_with_di(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> tuple[float, float, float]:
    """Returns (adx, plus_di, minus_di)."""

def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range. Measures volatility in price units."""

def price_velocity(closes: list[float], period: int = 5) -> float:
    """Rate of price change over `period` bars. Returns percentage."""

def price_acceleration(closes: list[float], period: int = 5) -> float:
    """Second derivative of price. Detects momentum exhaustion."""
```

**Design notes:**
- All functions take lists oldest-first and return the most recent value.
- All functions handle insufficient data gracefully (return neutral values, not exceptions).
- No external dependencies. Pure math on lists of floats.
- The `_series` variants return full history for divergence detection.

---

### 3. `server/signals/regime.py` -- RegimeDetector

Classifies the current market state. **Critical correction from original spec:** "choppy" (high vol, no direction) is now **VOLATILE_RANGING** and is tradeable via aggressive mean reversion. Only **DEAD** (no vol at all) is a no-trade zone -- and even then, capital moves to LP where dead flat is peak earning.

```python
class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"              # Low vol, oscillating -- mean reversion
    VOLATILE_RANGING = "volatile_ranging"  # High vol, no direction -- aggressive mean reversion (was "choppy")
    DEAD = "dead"                    # No vol at all -- no scalper trades, capital to LP
```

**Decision logic:**

1. Compute ADX on 1h candles:
   - ADX >= 25: trending -> direction from +DI/-DI
   - ADX < 25: not trending -> check volatility

2. For trending: TRENDING_UP if +DI > -DI, TRENDING_DOWN if -DI > +DI. Confirm with 15m EMA(9) vs EMA(21).

3. For non-trending:
   - BBW < 0.02 AND ATR declining: **DEAD** (no trades, capital to LP)
   - BBW < 0.04: **RANGING** (normal mean reversion)
   - BBW >= 0.04: **VOLATILE_RANGING** (aggressive mean reversion -- wider bands, more signals, bigger moves)

4. ATR acceleration filter: If ATR is expanding rapidly (>1.5x 4h ago) AND ADX < 20, this confirms VOLATILE_RANGING not DEAD.

**The key insight: VOLATILE_RANGING is the scalper's best regime.** Price swings repeatedly across the Bollinger bands. Each crossing is a trade. At 3x leverage, each 1.5% swing = 4.5% return. Multiple swings per day.

---

### 4. `server/signals/engine.py` -- SignalEngine

The brain. Combines candles, indicators, and regime to produce signals.

**Entry signals by regime:**

| Regime | Signal Type | Entry Condition | TP | SL | R:R |
|---|---|---|---|---|---|
| RANGING | Mean revert long | RSI<30 AND/OR price near lower BB | 1% (3% lev) | 0.5% (1.5% lev) | 2:1 |
| RANGING | Mean revert short | RSI>70 AND/OR price near upper BB | 1% (3% lev) | 0.5% (1.5% lev) | 2:1 |
| VOLATILE_RANGING | Aggressive MR long | RSI<35 AND price below lower BB | 1.5% (4.5% lev) | 0.7% (2.1% lev) | 2.1:1 |
| VOLATILE_RANGING | Aggressive MR short | RSI>65 AND price above upper BB | 1.5% (4.5% lev) | 0.7% (2.1% lev) | 2.1:1 |
| TRENDING_UP | Pullback long | RSI dips below 45, crosses back above near EMA(21) | 2% (6% lev) | 0.8% (2.4% lev) | 2.5:1 |
| TRENDING_UP | Breakout long | Price breaks 1h high, velocity > 0.5% | 2% + trail | 0.8% (2.4% lev) | 2.5:1 |
| TRENDING_DOWN | Rally short | RSI spikes above 55, crosses back below near EMA(21) | 2% (6% lev) | 0.8% (2.4% lev) | 2.5:1 |
| TRENDING_DOWN | Breakdown short | Price breaks 1h low, velocity < -0.5% | 2% + trail | 0.8% (2.4% lev) | 2.5:1 |
| DEAD | None | -- | -- | -- | -- |

**Exit signals (checked every tick, before entry signals):**
- Stop-loss hit
- Take-profit hit
- Trailing stop (momentum trades only, ratchets in profitable direction)
- Regime change (trending -> dead = close all; ranging -> trending = close counter-trend positions)
- Time-based (mean reversion: 30min max hold; momentum: 2h max hold)

**Signal cooldown:**
- After any close: 60s before new entry
- After stop-loss: 120s (market moved against us)

**Confidence scoring (0.0-1.0):**
- RSI + BB agree: 0.9
- RSI only: 0.65
- BB only: 0.6
- VWAP confirmation bonus: +0.1
- Regime confidence multiplier applied on top
- Minimum threshold: 0.6

---

### 5. `server/strategies/volatility_scalper.py` -- VolatilityScalper

BaseStrategy subclass. Owns signal engine, manages positions, delegates execution.

**Key parameters:**
- MAX_CONCURRENT_POSITIONS = 2
- MAX_TRADES_PER_DAY = 20
- MAX_LEVERAGE = 3.0 (configurable to 5.0)
- POSITION_SIZE_PCT = 0.30 (max 30% per trade)
- COOLDOWN_AFTER_LOSS = 120s
- COOLDOWN_AFTER_WIN = 30s

**Position sizing:**
- Base: capital_allocated * POSITION_SIZE_PCT
- Scale down if daily drawdown > 2%
- Scale down based on regime confidence
- Never exceed available capital minus existing positions

---

### 6. `server/strategies/allocator.py` -- DynamicAllocator

Manages capital flow between LP and scalper based on regime.

```python
class DynamicAllocator:
    REBALANCE_MIN_INTERVAL = 1800    # 30 minutes between rebalances
    REBALANCE_COST_PCT = 0.002       # ~0.2% per rebalance

    ALLOCATIONS = {
        "dead":              {"leveraged_lp": 0.90, "volatility_scalper": 0.10},
        "ranging":           {"leveraged_lp": 0.50, "volatility_scalper": 0.50},
        "volatile_ranging":  {"leveraged_lp": 0.30, "volatility_scalper": 0.70},
        "trending_up":       {"leveraged_lp": 0.30, "volatility_scalper": 0.70},
        "trending_down":     {"leveraged_lp": 0.20, "volatility_scalper": 0.80},
    }

    def should_rebalance(self, current_regime: str, current_allocations: dict) -> dict | None:
        """
        Returns new allocations if regime has been stable for 30+ minutes
        and current allocations differ from target by more than 10%.
        Returns None if no rebalance needed.
        """
```

The orchestrator calls this every loop iteration. When it returns new allocations, the orchestrator:
1. Closes the LP position (via lifecycle)
2. Adjusts capital_allocated on both strategies
3. LP reopens on next cycle with new allocation
4. Scalper immediately has access to its new allocation

---

### 7. MarginFi Short-Selling Extension

Add to `MarginFiLender`:

```python
async def deposit_usdc_and_borrow_sol(self, wallet, usdc_amount, sol_borrow_amount) -> dict:
    """Deposit USDC collateral, borrow SOL. For short positions."""

async def repay_sol_and_withdraw_usdc(self, wallet) -> dict:
    """Repay SOL borrow, withdraw USDC collateral."""

def get_max_sol_borrow(self, collateral_usdc, sol_price, ltv=0.65) -> float:
    """Max SOL borrowable given USDC collateral."""
```

Also add tracking: `self.deposited_usdc`, `self.borrowed_sol`.

---

### 8. Configuration

```python
# Volatility Scalper
SCALPER_DEFAULT_LEVERAGE = 3.0
SCALPER_MAX_LEVERAGE = 5.0
SCALPER_MAX_CONCURRENT_POSITIONS = 2
SCALPER_MAX_TRADES_PER_DAY = 20
SCALPER_POSITION_SIZE_PCT = 0.30
SCALPER_COOLDOWN_AFTER_LOSS = 120
SCALPER_COOLDOWN_AFTER_WIN = 30
SCALPER_MIN_SIGNAL_CONFIDENCE = 0.6
SCALPER_DAILY_LOSS_LIMIT_PCT = 5.0

# Mean reversion
SCALPER_MR_TAKE_PROFIT_PCT = 0.01
SCALPER_MR_STOP_LOSS_PCT = 0.005
SCALPER_MR_MAX_HOLD_MINUTES = 30

# Aggressive mean reversion (volatile_ranging)
SCALPER_AMR_TAKE_PROFIT_PCT = 0.015
SCALPER_AMR_STOP_LOSS_PCT = 0.007
SCALPER_AMR_MAX_HOLD_MINUTES = 20

# Momentum
SCALPER_MOM_TAKE_PROFIT_PCT = 0.02
SCALPER_MOM_STOP_LOSS_PCT = 0.008
SCALPER_MOM_TRAILING_STOP_PCT = 0.01
SCALPER_MOM_MAX_HOLD_MINUTES = 120

# Dynamic allocation
DEFAULT_CAPITAL_ALLOCATION = {
    "leveraged_lp": 0.50,
    "volatility_scalper": 0.50,
}
```

---

## Execution Order

1. **`signals/indicators.py`** -- Pure math. Test each function with known inputs/outputs.
2. **`signals/candles.py`** -- Feed mock ticks, verify candle formation and boundary alignment.
3. **`signals/regime.py`** -- Feed historical candles, verify regime classification.
4. **`signals/engine.py`** -- Integration. Feed real price history, verify signals.
5. **`strategies/volatility_scalper.py`** -- Wire signals to strategy. Paper mode.
6. **`execution/marginfi.py` extensions** -- Short-selling methods.
7. **`strategies/allocator.py`** -- Dynamic allocation between LP and scalper.
8. **`config.py` + `web_api.py`** -- Registration and config.
9. **Paper mode validation** -- 24-48 hours alongside LP. Review signal quality, win rate, PnL.
10. **Live mode** -- Start with 10-20% allocation, scale up after validation.

---

## Risk Controls

| Control | Value | Purpose |
|---|---|---|
| Max concurrent positions | 2 | Limit exposure |
| Max trades per day | 20 | Prevent overtrading |
| Per-trade stop-loss | 0.5-0.8% (1.5-2.4% leveraged) | Cap per-trade loss |
| Daily loss limit | 5% of allocated capital | Stop trading for the day |
| Cooldown after loss | 120 seconds | Prevent revenge trading |
| Max leverage | 3x (configurable to 5x) | Limit leverage risk |
| No trading in DEAD regime | N/A | Capital flows to LP instead |
| Time-based exit | 20-30min (MR) / 2h (momentum) | Don't hold stale trades |
| Signal confidence minimum | 0.6 | Ignore weak signals |
| Position size cap | 30% of allocated capital | Diversify risk |
| Regime stability check | 30 min before allocation rebalance | Prevent churn on regime flips |

---

## Phase 2: Drift Protocol Integration

**Why Drift is the real unlock for scalping:**
- Single-transaction open/close (vs 3+ txs for MarginFi+Orca)
- Execution cost drops from ~0.4% to ~0.05% per round trip
- Native leverage up to 10x
- Native shorting (perps are symmetric)
- Cross-margin (capital efficient)
- BTC, ETH, SOL + non-crypto markets
- Funding rate income on the right side

```python
class DriftExecutor:
    # Drift program: dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH
    async def open_perp_position(self, market, direction, size_usd, leverage) -> dict
    async def close_perp_position(self, market) -> dict
    async def get_position(self, market) -> dict | None
    async def get_funding_rate(self, market) -> float
```

The signal engine and regime detector work identically for Drift markets -- only the execution layer changes.

---

## Phase 3: Solana Altcoins

JUP, PYTH, JTO, W, BONK -- native on Orca/Raydium. These swing 5-15% on random days even when SOL is flat.

Requires per-asset CandleAggregator + SignalEngine instances and an AssetAllocator that ranks by signal strength.

---

## Phase 4: Non-Crypto Assets

Via Drift perps: XAU/USD, EUR/USD, oil. Move on entirely different catalysts. When crypto is dead on a Sunday, gold may be moving on Asia open Monday.

Pyth already provides real-time feeds for all these at the self-hosted Hermes endpoint.

---

## Dashboard Additions

The scalper exposes via `get_state()`:

```python
{
    "active_trades": [...],
    "daily_stats": {
        "trades_today": int,
        "wins": int,
        "losses": int,
        "daily_pnl_usd": float,
        "daily_pnl_pct": float,
        "win_rate": float,
    },
    "indicators": {
        "rsi_5m": float,
        "bb_lower": float, "bb_upper": float,
        "regime": str, "regime_confidence": float,
        "adx": float, "plus_di": float, "minus_di": float,
        "atr": float, "vwap": float,
        "velocity_5m": float, "acceleration_5m": float,
    },
    "signal_performance": {
        "total_signals": int,
        "overall_win_rate": float,
        "profit_factor": float,
        "by_regime": {...},
    },
    "allocation": {
        "current_regime": str,
        "lp_pct": float,
        "scalper_pct": float,
        "last_rebalance": float,
    },
}
```
