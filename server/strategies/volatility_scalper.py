import time
import uuid
import logging
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.signals.engine import SignalEngine, SignalType, TradeSignal
from server.signals.regime import MarketRegime
from server.execution.orca import OrcaExecutor
from server.execution.marginfi import MarginFiLender
from server.execution.drift import DriftExecutor
from server.config import ORCA_WHIRLPOOL_SOL_USDC, DATABASE_URL, HELIUS_RPC_URL
from server.persistence import TradeStore, SignalStore

log = logging.getLogger("volatility_scalper")

TRACKED_ASSETS = ["SOL", "JUP", "JTO", "PYTH", "W", "SUI", "SEI"]


class VolatilityScalper(BaseStrategy):
    STRATEGY_ID = "volatility_scalper"
    STRATEGY_NAME = "Volatility Scalper"

    MAX_CONCURRENT_POSITIONS = 7
    MAX_LEVERAGE = 3.0
    MIN_TRADE_USD = 10.0
    POSITION_SIZE_PCT = 0.14
    COOLDOWN_AFTER_LOSS_SEC = 120
    COOLDOWN_AFTER_WIN_SEC = 30
    DAILY_LOSS_LIMIT_PCT = 5.0

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self.engines: dict[str, SignalEngine] = {asset: SignalEngine(asset=asset) for asset in TRACKED_ASSETS}
        self.signal_engine = self.engines["SOL"]
        self.orca: OrcaExecutor | None = None
        self.lender: MarginFiLender | None = None
        self.drift: DriftExecutor | None = None
        self._asset_prices: dict[str, float] = {}
        self._active_trades: list[dict] = []
        self._trade_log: list[dict] = []
        self._daily_pnl: float = 0.0
        self._daily_trade_count: int = 0
        self._daily_wins: int = 0
        self._daily_losses: int = 0
        self._last_trade_time: float = 0.0
        self._daily_reset_time: float = 0.0

    async def init_executors(self):
        if self.mode == "live" and not self.drift:
            self.drift = DriftExecutor(paper_mode=False)
            await self.drift.start()
        if self.mode == "live" and not self.orca:
            self.orca = OrcaExecutor(paper_mode=False)
            await self.orca.start()
        if self.mode == "live" and not self.lender:
            self.lender = MarginFiLender(paper_mode=False)
            await self.lender.start()

    async def warmup(self, price_history: list[dict]):
        import httpx
        from server.signals.candles import Candle, Timeframe, TIMEFRAME_SECONDS, MAX_CANDLES

        await self._fetch_asset_prices()
        for asset in TRACKED_ASSETS:
            if self._asset_prices.get(asset, 0) <= 0:
                log.warning(f"No price for {asset} after initial fetch")

        if DATABASE_URL:
            active = TradeStore.get_active()
            if active:
                self._active_trades = active
                log.info(f"Restored {len(active)} active trades from DB")
            else:
                log.info("No active trades in DB")

        async with httpx.AsyncClient(timeout=60) as http:
            for asset, engine in self.engines.items():
                try:
                    tf_map = {"1m": Timeframe.M1, "5m": Timeframe.M5, "15m": Timeframe.M15, "1h": Timeframe.H1}
                    for interval, tf in tf_map.items():
                        r = await http.get(
                            f"https://lens.soon.app/api/assets/{asset}/history",
                            params={"interval": interval, "limit": "5000"},
                            headers={"x-api-key": "your-dev-key"},
                        )
                        if r.status_code != 200:
                            continue
                        raw = r.json().get("data", [])
                        candle_list = [
                            Candle(
                                timestamp=c["timestamp"] / 1000, timeframe=tf,
                                open=c["open"], high=c["high"], low=c["low"],
                                close=c["close"], volume=c.get("volume", 0), closed=True,
                            ) for c in raw
                        ]
                        max_c = MAX_CANDLES[tf]
                        engine.candles._candles[tf] = candle_list[-max_c:]
                        if candle_list:
                            last = candle_list[-1]
                            engine.candles._current[tf] = Candle(
                                timestamp=last.timestamp + TIMEFRAME_SECONDS[tf], timeframe=tf,
                                open=last.close, high=last.close, low=last.close, close=last.close,
                            )
                    engine._warmed_up = True
                    n5m = engine.candles.candle_count(Timeframe.M5)
                    log.info(f"Warmup {asset}: {n5m} 5m candles")
                except Exception as e:
                    log.warning(f"Warmup {asset} failed: {e}")

        if not self.engines["SOL"].is_warmed_up and price_history:
            await self.engines["SOL"].warmup(price_history)

    PYTH_FEEDS = {
        "SOL": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
        "JUP": "0x0a0408d619e9380abad35060f9192039ed5042fa6f82301d0e48bb52be830996",
        "JTO": "0xb43660a5f790c69354b0729a5ef9d50d68f1df92107540210b9cccba1f947cc2",
        "PYTH": "0x0bbf28e9a841a1cc788f6a361b17ca072d0ea3098a1e5df1c3922d06719579ff",
        "W": "0xeff7446475e218517566ea99e72a4abec2e1bd8498b43b7d8331e29dcb059389",
        "SUI": "0x23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744",
        "SEI": "0x53614f1cb0c031d4af66c04cb9c756234adad0e1cee85303795091499a4084eb",
    }

    async def _fetch_asset_prices(self):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                for asset, fid in self.PYTH_FEEDS.items():
                    if asset == "SOL":
                        continue
                    try:
                        r = await http.get(
                            "http://20.120.229.168:4160/api/latest_price_feeds",
                            params={"ids[]": fid},
                        )
                        if r.status_code == 200:
                            feeds = r.json()
                            if feeds:
                                pd = feeds[0].get("price", {})
                                price = int(pd.get("price", 0)) * (10 ** int(pd.get("expo", 0)))
                                if price > 0:
                                    self._asset_prices[asset] = price
                    except Exception:
                        pass
        except Exception:
            pass

    async def update(self, market_data: dict):
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return
        now = time.time()

        self._asset_prices["SOL"] = sol_price
        if now - getattr(self, '_last_price_fetch', 0) > 10:
            await self._fetch_asset_prices()
            self._last_price_fetch = now

        for asset, engine in self.engines.items():
            price = self._asset_prices.get(asset, 0)
            if price > 0:
                engine.on_tick(price, now)

        self.signal_engine.on_tick(sol_price, now)

        self._check_daily_reset(now)

        for trade in list(self._active_trades):
            if trade["status"] != "active":
                continue

            asset = trade.get("asset", "SOL")
            price = self._asset_prices.get(asset, 0)
            if price <= 0:
                continue
            engine = self.engines.get(asset, self.signal_engine)

            trade["current_price"] = price
            if trade["direction"] == "long":
                pnl_pct = (price - trade["entry_price"]) / trade["entry_price"] * trade["leverage"]
                trade["peak_price"] = max(trade.get("peak_price", trade["entry_price"]), price)
            else:
                pnl_pct = (trade["entry_price"] - price) / trade["entry_price"] * trade["leverage"]
                trade["peak_price"] = min(trade.get("peak_price", trade["entry_price"]), price)

            trade["pnl_pct"] = pnl_pct * 100
            trade["pnl_usd"] = trade["collateral_usd"] * pnl_pct
            trade["last_update"] = now

            new_sl = engine.update_trailing_stop(trade, price)
            if new_sl is not None:
                trade["stop_loss"] = new_sl

            exit_signal = engine.check_exits(trade, price)
            if exit_signal:
                await self._close_trade(trade, price, exit_signal.reason, market_data)

        self._sync_positions(sol_price)

        pool_apy = market_data.get("pool_apys", {}).get("orca_sol_usdc", 50.0)
        regime = self.signal_engine.regime_detector.regime
        stats = self.signal_engine.get_performance_stats()
        indicators = self.signal_engine.get_indicator_snapshot()

        asset_regimes = {}
        for asset, engine in self.engines.items():
            if engine.is_warmed_up:
                a = engine.regime_detector.assess(engine.candles)
                asset_regimes[asset] = {
                    "regime": a.regime.value,
                    "confidence": a.confidence,
                    "price": self._asset_prices.get(asset, 0),
                }

        self.last_update = now
        self.metrics = {
            "regime": regime.value,
            "regime_confidence": self.signal_engine.regime_detector.confidence,
            "asset_regimes": asset_regimes,
            "active_trades": len([t for t in self._active_trades if t["status"] == "active"]),
            "daily_trades": self._daily_trade_count,
            "daily_pnl_usd": self._daily_pnl,
            "daily_pnl_pct": (self._daily_pnl / self.capital_allocated * 100) if self.capital_allocated > 0 else 0,
            "daily_wins": self._daily_wins,
            "daily_losses": self._daily_losses,
            "daily_win_rate": self._daily_wins / max(self._daily_wins + self._daily_losses, 1),
            "all_time_win_rate": stats.get("win_rate", 0),
            "profit_factor": stats.get("profit_factor", 0),
            "pool_apy": pool_apy,
            "indicators": indicators,
        }

    async def evaluate(self, market_data: dict) -> dict:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return {"action": "wait", "reason": "no_price"}

        active_count = len([t for t in self._active_trades if t["status"] == "active"])

        if active_count >= self.MAX_CONCURRENT_POSITIONS:
            return {"action": "hold", "reason": "max_positions"}

        if self.capital_allocated > 0 and self._daily_pnl < 0:
            if abs(self._daily_pnl / self.capital_allocated) > self.DAILY_LOSS_LIMIT_PCT / 100:
                return {"action": "wait", "reason": "daily_loss_limit"}

        now = time.time()
        last_trade = self._last_trade_time
        if last_trade > 0:
            cooldown = self.COOLDOWN_AFTER_LOSS_SEC if self._daily_losses > self._daily_wins else self.COOLDOWN_AFTER_WIN_SEC
            if now - last_trade < cooldown:
                return {"action": "wait", "reason": "cooldown"}

        best_signal = None
        best_confidence = 0
        for asset, engine in self.engines.items():
            price = self._asset_prices.get(asset, 0)
            if price <= 0 or not engine.is_warmed_up:
                continue
            already_trading = any(t["asset"] == asset and t["status"] == "active" for t in self._active_trades)
            if already_trading:
                continue
            signal = engine.evaluate(price)
            if signal.type in (SignalType.LONG, SignalType.SHORT) and signal.confidence > best_confidence:
                best_signal = signal
                best_confidence = signal.confidence

        if not best_signal:
            return {"action": "hold", "reason": "no_signal_across_assets"}

        signal = best_signal
        log.info(f"Best signal: {signal.asset} {signal.type.value} conf={signal.confidence:.2f} reason={signal.reason[:40]}")

        size = self._calculate_position_size(signal)
        log.info(f"Position size: ${size:.2f} (min: ${self.MIN_TRADE_USD})")
        if size < self.MIN_TRADE_USD:
            log.warning(f"Position too small: ${size:.2f} < ${self.MIN_TRADE_USD}")
            return {"action": "wait", "reason": "position_too_small"}

        action = "open_long" if signal.type == SignalType.LONG else "open_short"
        return {
            "action": action,
            "signal": signal,
            "deposit_usd": size,
            "leverage": self.MAX_LEVERAGE,
            "reason": f"{signal.asset}: {signal.reason}",
        }

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        act = action["action"]

        if act in ("open_long", "open_short"):
            signal: TradeSignal = action["signal"]
            size = action["deposit_usd"]
            leverage = action["leverage"]
            direction = "long" if act == "open_long" else "short"

            asset_price = self._asset_prices.get(signal.asset, 0)
            if asset_price <= 0:
                log.warning(f"No price for {signal.asset}, skipping trade")
                return None

            trade = {
                "id": str(uuid.uuid4())[:12],
                "direction": direction,
                "trade_type": signal.trade_type,
                "asset": signal.asset,
                "entry_price": signal.entry_price,
                "current_price": asset_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "size_usd": size * leverage,
                "leverage": leverage,
                "collateral_usd": size,
                "pnl_usd": 0,
                "pnl_pct": 0,
                "peak_price": asset_price,
                "regime_at_entry": signal.regime,
                "signal_confidence": signal.confidence,
                "opened_at": time.time(),
                "last_update": time.time(),
                "status": "active",
            }

            if self.mode == "live":
                try:
                    await self.init_executors()
                    if direction == "long":
                        await self._open_live_long(trade, sol_price)
                    else:
                        await self._open_live_short(trade, sol_price)
                except Exception as e:
                    log.error(f"Live {direction} open failed: {e}")
                    self.error = str(e)
                    return None

            self._active_trades.append(trade)
            self._daily_trade_count += 1
            self._last_trade_time = time.time()

            if DATABASE_URL:
                TradeStore.save(trade)
                SignalStore.save(
                    signal.type.value, signal.asset, signal.confidence,
                    signal.entry_price, signal.stop_loss, signal.take_profit,
                    signal.regime, signal.trade_type, signal.reason, signal.indicators,
                )

            log.info(
                f"OPENED {direction} {signal.trade_type}: "
                f"entry=${sol_price:.2f} SL=${signal.stop_loss:.2f} TP=${signal.take_profit:.2f} "
                f"size=${size:.2f} lev={leverage}x conf={signal.confidence:.2f} "
                f"regime={signal.regime} reason={signal.reason}"
            )

            self._sync_positions(sol_price)
            return self.active_positions[-1] if self.active_positions else None

        return None

    async def _open_live_long(self, trade: dict, sol_price: float):
        asset = trade["asset"]
        size_usd = trade["size_usd"]
        result = await self.drift.open_perp_position(asset, "long", size_usd, trade["leverage"])
        log.info(f"Drift long {asset}: ${size_usd:.2f} -> {result}")

    async def _open_live_short(self, trade: dict, sol_price: float):
        asset = trade["asset"]
        size_usd = trade["size_usd"]
        result = await self.drift.open_perp_position(asset, "short", size_usd, trade["leverage"])
        log.info(f"Drift short {asset}: ${size_usd:.2f} -> {result}")

    async def _close_live_long(self, trade: dict, sol_price: float):
        asset = trade["asset"]
        result = await self.drift.close_perp_position(asset)
        log.info(f"Drift close long {asset}: {result}")

    async def _close_live_short(self, trade: dict, sol_price: float):
        asset = trade["asset"]
        result = await self.drift.close_perp_position(asset)
        log.info(f"Drift close short {asset}: {result}")

    async def _close_trade(self, trade: dict, exit_price: float, reason: str, market_data: dict):
        trade["status"] = "closing"

        if self.mode == "live":
            try:
                await self.init_executors()
                if trade["direction"] == "long":
                    await self._close_live_long(trade, exit_price)
                else:
                    await self._close_live_short(trade, exit_price)
            except Exception as e:
                log.error(f"Live close {trade['direction']} failed: {e}")
                self.error = str(e)

        trade["status"] = "closed"

        if trade["direction"] == "long":
            pnl_pct = (exit_price - trade["entry_price"]) / trade["entry_price"] * trade["leverage"]
        else:
            pnl_pct = (trade["entry_price"] - exit_price) / trade["entry_price"] * trade["leverage"]

        pnl_usd = trade["collateral_usd"] * pnl_pct
        trade["pnl_usd"] = pnl_usd
        trade["pnl_pct"] = pnl_pct * 100

        was_win = pnl_usd > 0
        self._daily_pnl += pnl_usd
        if was_win:
            self._daily_wins += 1
        else:
            self._daily_losses += 1

        self.signal_engine.record_close(not was_win)

        trade["exit_price"] = exit_price
        trade["exit_reason"] = reason
        trade["closed_at"] = time.time()
        self._trade_log.append(dict(trade))
        if len(self._trade_log) > 200:
            self._trade_log = self._trade_log[-200:]

        if DATABASE_URL:
            TradeStore.save(trade)

        self._active_trades = [t for t in self._active_trades if t["status"] == "active"]

        log.info(
            f"CLOSED {trade['direction']} {trade['trade_type']}: "
            f"entry=${trade['entry_price']:.2f} exit=${exit_price:.2f} "
            f"PnL=${pnl_usd:.2f} ({pnl_pct*100:.1f}%) reason={reason}"
        )

    def _calculate_position_size(self, signal: TradeSignal) -> float:
        base = self.capital_allocated * self.POSITION_SIZE_PCT

        active_usd = sum(t["collateral_usd"] for t in self._active_trades if t["status"] == "active")
        available = self.capital_allocated - active_usd
        base = min(base, available)

        if self.capital_allocated > 0 and self._daily_pnl < 0:
            drawdown = abs(self._daily_pnl) / self.capital_allocated
            if drawdown > 0.02:
                base *= 0.5

        base *= min(signal.confidence / 0.8, 1.0)

        return max(base, 0)

    def _check_daily_reset(self, now: float):
        import calendar
        today_utc = int(now // 86400) * 86400
        if self._daily_reset_time < today_utc:
            if self._daily_trade_count > 0:
                log.info(
                    f"Daily reset: trades={self._daily_trade_count} "
                    f"W/L={self._daily_wins}/{self._daily_losses} "
                    f"PnL=${self._daily_pnl:.2f}"
                )
            self._daily_pnl = 0.0
            self._daily_trade_count = 0
            self._daily_wins = 0
            self._daily_losses = 0
            self._daily_reset_time = today_utc

    def _sync_positions(self, sol_price: float):
        self.positions = []
        for trade in self._active_trades:
            if trade["status"] != "active":
                continue
            self.positions.append(StrategyPosition(
                id=trade["id"],
                pool=ORCA_WHIRLPOOL_SOL_USDC,
                entry_price=trade["entry_price"],
                deposit_usd=trade["collateral_usd"],
                current_value_usd=trade["collateral_usd"] + trade["pnl_usd"],
                fees_earned_usd=0,
                sol_amount=trade["size_usd"] / sol_price if trade["direction"] == "long" else 0,
                usdc_amount=trade["size_usd"] if trade["direction"] == "short" else 0,
                metadata={
                    "direction": trade["direction"],
                    "trade_type": trade["trade_type"],
                    "leverage": trade["leverage"],
                    "stop_loss": trade["stop_loss"],
                    "take_profit": trade["take_profit"],
                    "pnl_pct": trade["pnl_pct"],
                    "regime": trade["regime_at_entry"],
                    "confidence": trade["signal_confidence"],
                },
            ))
        if self.positions:
            self.status = "active"
        elif self._daily_trade_count > 0:
            self.status = "watching"
        else:
            self.status = "idle"

    def get_state(self) -> dict:
        base = super().get_state()
        base["active_trades"] = [t for t in self._active_trades if t["status"] == "active"]
        base["trade_log"] = self._trade_log[-50:]
        base["daily_stats"] = {
            "trades_today": self._daily_trade_count,
            "wins": self._daily_wins,
            "losses": self._daily_losses,
            "daily_pnl_usd": self._daily_pnl,
            "daily_pnl_pct": (self._daily_pnl / self.capital_allocated * 100) if self.capital_allocated > 0 else 0,
            "win_rate": self._daily_wins / max(self._daily_wins + self._daily_losses, 1),
        }
        base["signal_performance"] = self.signal_engine.get_performance_stats()
        base["indicators"] = self.signal_engine.get_indicator_snapshot()
        return base

    def load_state(self, state: dict):
        super().load_state(state)
        self._trade_log = state.get("trade_log", [])
        ds = state.get("daily_stats", {})
        self._daily_trade_count = ds.get("trades_today", 0)
        self._daily_wins = ds.get("wins", 0)
        self._daily_losses = ds.get("losses", 0)
        self._daily_pnl = ds.get("daily_pnl_usd", 0)
        if "signal_engine" in state:
            self.signal_engine.load_state(state["signal_engine"])
