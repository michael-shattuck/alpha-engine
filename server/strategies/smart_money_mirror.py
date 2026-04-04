import asyncio
import time
import uuid
import logging
from typing import Optional
from collections import deque

from server.strategies.base import BaseStrategy, StrategyPosition
from server.strategies.sse_consumer import SSEConsumer
from server.signals.engine import SignalEngine, SignalType, TradeSignal
from server.signals.learner import TradeLearner
from server.execution.venue_router import VenueRouter
from server.execution.drift import MARKET_INDEX
from server.config import DATABASE_URL
from server.persistence import TradeStore

log = logging.getLogger("smart_money_mirror")

TRACKED_ASSETS = [
    "SOL", "JUP", "JTO", "PYTH", "SUI", "SEI", "WIF",
    "PENGU", "FARTCOIN", "TRUMP", "POPCAT", "BONK", "MOODENG",
]

DEEP_LIQUIDITY = {"SOL", "BTC", "ETH", "XRP", "DOGE", "LINK", "BNB", "LTC", "ADA", "AVAX"}
MEDIUM_LIQUIDITY = {"JUP", "JTO", "SUI", "SEI", "PYTH", "RENDER", "RAY", "DRIFT", "INJ", "OP", "ARB", "TON", "HNT", "TIA", "HYPE"}
THIN_LIQUIDITY = {"WIF", "BONK", "PENGU", "POPCAT", "GOAT", "PNUT", "AI16Z", "TRUMP", "IO", "KMNO", "TNSR", "ME", "BERA"}


def get_slippage(symbol: str) -> float:
    if symbol in DEEP_LIQUIDITY:
        return 0.001
    if symbol in MEDIUM_LIQUIDITY:
        return 0.0015
    if symbol in THIN_LIQUIDITY:
        return 0.002
    return 0.003


LEVERAGE_TIERS = {
    "aggressive": {"leverage": 10.0, "min_flow": 0.6, "sl_pct": 0.015, "tp_pct": 0.030, "trail_activate": 0.08, "trail_distance": 0.05, "max_hold_sec": 7200},
    "confident":  {"leverage": 7.0,  "min_flow": 0.3, "sl_pct": 0.014, "tp_pct": 0.025, "trail_activate": 0.08, "trail_distance": 0.05, "max_hold_sec": 5400},
    "moderate":   {"leverage": 5.0,  "min_flow": 0.0, "sl_pct": 0.010, "tp_pct": 0.020, "trail_activate": 0.06, "trail_distance": 0.04, "max_hold_sec": 3600},
}

FLOW_WINDOW_SEC = 300
FLOW_DECAY_HALF_LIFE = 120


class FlowAggregator:
    def __init__(self):
        self._events: deque = deque(maxlen=5000)
        self._buy_sol_total: float = 0
        self._sell_sol_total: float = 0

    def record(self, action: str, sol_amount: float, wallet_tier: str):
        now = time.time()
        tier_weight = {"gods": 4, "diamond": 3, "gold": 2, "silver": 1}.get(wallet_tier, 0.5)
        weighted = sol_amount * tier_weight
        self._events.append((now, action, weighted))

    def score(self) -> float:
        now = time.time()
        cutoff = now - FLOW_WINDOW_SEC
        buy_pressure = 0.0
        sell_pressure = 0.0

        for ts, action, weighted in self._events:
            if ts < cutoff:
                continue
            age = now - ts
            decay = 0.5 ** (age / FLOW_DECAY_HALF_LIFE)
            if action == "BUY":
                buy_pressure += weighted * decay
            else:
                sell_pressure += weighted * decay

        total = buy_pressure + sell_pressure
        if total < 0.1:
            return 0.0
        return (buy_pressure - sell_pressure) / total

    def volume(self) -> float:
        now = time.time()
        cutoff = now - FLOW_WINDOW_SEC
        total = 0.0
        for ts, _, weighted in self._events:
            if ts >= cutoff:
                total += abs(weighted)
        return total

    def prune(self):
        cutoff = time.time() - FLOW_WINDOW_SEC * 2
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()


class SmartMoneyMirror(BaseStrategy):
    STRATEGY_ID = "smart_money_mirror"
    STRATEGY_NAME = "Smart Money Mirror"

    MAX_CONCURRENT_POSITIONS = 4
    POSITION_SIZE_PCT = 0.12
    MIN_TRADE_USD = 1.0
    DAILY_LOSS_LIMIT_PCT = 5.0
    COOLDOWN_AFTER_LOSS_SEC = 90
    COOLDOWN_AFTER_WIN_SEC = 20
    MIN_FLOW_VOLUME = 1.0

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self.engines: dict[str, SignalEngine] = {
            asset: SignalEngine(asset=asset) for asset in TRACKED_ASSETS
        }
        self.learner = TradeLearner()
        self.router: VenueRouter | None = None
        self.sse: SSEConsumer | None = None
        self.flow = FlowAggregator()
        self._wallet_cache: dict = {}
        self._asset_prices: dict[str, float] = {}
        self._active_trades: list[dict] = []
        self._trade_log: list[dict] = []
        self._daily_pnl: float = 0.0
        self._daily_trade_count: int = 0
        self._daily_wins: int = 0
        self._daily_losses: int = 0
        self._last_trade_time: float = 0.0
        self._daily_reset_time: float = 0.0
        self._last_wallet_refresh: float = 0.0
        self._last_price_fetch: float = 0.0
        self._tier_stats: dict[str, dict] = {
            k: {"trades": 0, "wins": 0, "pnl": 0.0} for k in LEVERAGE_TIERS
        }

    async def warmup(self, price_history: list[dict]):
        from server.config import SMART_MONEY_SSE_URL

        SignalEngine.load_ml_models()

        self.router = VenueRouter(paper_mode=(self.mode != "live"))
        await self.router.start()

        await self._fetch_asset_prices()

        self.sse = SSEConsumer(SMART_MONEY_SSE_URL, self._on_sse_event)
        self._wallet_cache = await self.sse.fetch_wallets()
        await self.sse.start()

        if DATABASE_URL:
            active = TradeStore.get_active()
            mirror_trades = [t for t in active if t.get("trade_type", "").startswith("mirror")]
            if mirror_trades:
                self._active_trades = mirror_trades
                log.info(f"Restored {len(mirror_trades)} mirror trades from DB")

        self._daily_reset_time = int(time.time() // 86400) * 86400

        import httpx
        from server.signals.candles import Candle, Timeframe, TIMEFRAME_SECONDS, MAX_CANDLES
        async with httpx.AsyncClient(timeout=60) as http:
            for asset, engine in self.engines.items():
                try:
                    tf_map = {"1m": Timeframe.M1, "5m": Timeframe.M5, "15m": Timeframe.M15, "1h": Timeframe.H1, "4h": Timeframe.H4, "1d": Timeframe.D1}
                    for interval, tf in tf_map.items():
                        r = await http.get(
                            f"https://lens.soon.app/api/assets/{asset}/history",
                            params={"interval": interval, "limit": "5000"},
                            headers={"x-api-key": "your-dev-key"},
                        )
                        if r.status_code != 200:
                            continue
                        raw = r.json().get("data", [])
                        if not raw:
                            continue
                        period = TIMEFRAME_SECONDS[tf]
                        max_c = MAX_CANDLES.get(tf, 500)
                        for c in raw[-max_c:]:
                            candle = Candle(
                                open=c["open"], high=c["high"], low=c["low"], close=c["close"],
                                volume=c.get("volume", 0), timestamp=c["timestamp"],
                            )
                            engine.candles.buffers[tf].append(candle)
                            engine.candles.last_candle_time[tf] = candle.timestamp
                    engine.is_warmed_up = True
                    log.info(f"Warmed up mirror engine for {asset}")
                except Exception as e:
                    log.warning(f"Mirror warmup {asset}: {e}")

        log.info(
            f"Smart Money Mirror warmup: {len(self._wallet_cache)} wallets, "
            f"SSE {'connected' if self.sse.connected else 'connecting'}, "
            f"{sum(1 for e in self.engines.values() if e.is_warmed_up)}/{len(self.engines)} engines warmed"
        )

    async def _on_sse_event(self, event: dict):
        event_type = event.get("_event_type", "trade")

        if event_type == "trade":
            action = event.get("action", "").upper()
            if not action:
                action = event.get("type", event.get("side", "")).upper()
            if action not in ("BUY", "SELL"):
                return
            sol_amount = event.get("size_sol", 0)
            if sol_amount <= 0:
                return
            wallet_prefix = event.get("wallet", "")[:12]
            wallet_info = self._wallet_cache.get(wallet_prefix, {})
            wallet_tier = wallet_info.get("tier", "unknown")
            self.flow.record(action, sol_amount, wallet_tier)

        elif event_type == "state":
            positions = event.get("positions", [])
            closed = event.get("closed_trades", [])
            for pos in positions:
                size = pos.get("size_usd", 0)
                if size > 0:
                    self.flow.record("BUY", size / 80, "unknown")
            for trade in closed:
                pnl = trade.get("pnl_sol", 0)
                self.flow.record("SELL", abs(pnl) if pnl else 0.01, "unknown")

    PYTH_FEEDS = {
        "SOL": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
        "JUP": "0x0a0408d619e9380abad35060f9192039ed5042fa6f82301d0e48bb52be830996",
        "JTO": "0xb43660a5f790c69354b0729a5ef9d50d68f1df92107540210b9cccba1f947cc2",
        "PYTH": "0x0bbf28e9a841a1cc788f6a361b17ca072d0ea3098a1e5df1c3922d06719579ff",
        "W": "0xeff7446475e218517566ea99e72a4abec2e1bd8498b43b7d8331e29dcb059389",
        "SUI": "0x23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744",
        "SEI": "0x53614f1cb0c031d4af66c04cb9c756234adad0e1cee85303795091499a4084eb",
        "WIF": "0x4ca4beeca86f0d164160323817a4e42b10010a724c2217c6ee41b54cd4cc61fc",
        "BONK": "0x72b021217ca3fe68922a19aaf990109cb9d84e9ad004b4d2025ad6f529314419",
        "PENGU": "0xbed3097008b9b5e3c93bec20be79cb43986b85a996475589351a21e67bae9b61",
        "FARTCOIN": "0x58cd29ef0e714c5affc44f269b2c1899a52da4169d7acc147b9da692e6953608",
        "TRUMP": "0x879551021853eec7a7dc827578e8e69da7e4fa8148339aa0d3d5296405be4b1a",
        "POPCAT": "0xb9312a7ee50e189ef045aa3c7842e099b061bd9bdc99ac645956c3b660dc8cce",
        "MOODENG": "0xffff73128917a90950cd0473fd2551d7cd274fd5a6cc45641881bbcc6ee73417",
    }

    async def _fetch_asset_prices(self):
        import httpx
        non_sol = {a: fid for a, fid in self.PYTH_FEEDS.items() if a != "SOL"}
        if not non_sol:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                params = [("ids[]", fid) for fid in non_sol.values()]
                r = await http.get("http://20.120.229.168:4160/api/latest_price_feeds", params=params)
                if r.status_code != 200:
                    return
                feeds = r.json()
                fid_to_asset = {fid.removeprefix("0x"): a for a, fid in non_sol.items()}
                for feed in feeds:
                    fid = feed.get("id", "")
                    asset = fid_to_asset.get(fid)
                    if not asset:
                        continue
                    pd = feed.get("price", {})
                    price = int(pd.get("price", 0)) * (10 ** int(pd.get("expo", 0)))
                    if price > 0:
                        self._asset_prices[asset] = price
        except Exception:
            pass

    async def update(self, market_data: dict):
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return
        now = time.time()

        self._asset_prices["SOL"] = sol_price
        if now - self._last_price_fetch > 10:
            await self._fetch_asset_prices()
            self._last_price_fetch = now

        for asset, engine in self.engines.items():
            price = self._asset_prices.get(asset, 0)
            if price > 0:
                engine.on_tick(price, now)

        if now - self._last_wallet_refresh > 300 and self.sse:
            self._wallet_cache = await self.sse.fetch_wallets()
            self._last_wallet_refresh = now

        self.flow.prune()

        for trade in list(self._active_trades):
            if trade["status"] != "active":
                continue

            asset = trade["asset"]
            price = self._asset_prices.get(asset, 0)
            if price <= 0:
                continue

            trade["current_price"] = price
            leverage = trade["leverage"]

            if trade["direction"] == "long":
                raw_pnl = (price - trade["entry_price"]) / trade["entry_price"] * leverage
                trade["peak_price"] = max(trade.get("peak_price", trade["entry_price"]), price)
            else:
                raw_pnl = (trade["entry_price"] - price) / trade["entry_price"] * leverage
                trade["peak_price"] = min(trade.get("peak_price", trade["entry_price"]), price)

            trade["mae"] = min(trade.get("mae", 0), raw_pnl)
            trade["mfe"] = max(trade.get("mfe", 0), raw_pnl)
            trade["pnl_pct"] = raw_pnl * 100
            trade["pnl_usd"] = trade["collateral_usd"] * raw_pnl
            trade["last_update"] = now

            tier_key = trade.get("leverage_tier", "moderate")
            tier_cfg = LEVERAGE_TIERS.get(tier_key, LEVERAGE_TIERS["moderate"])

            if raw_pnl <= -tier_cfg["sl_pct"]:
                await self._close_trade(trade, price, "stop_loss", market_data)
                continue

            if raw_pnl >= tier_cfg["tp_pct"]:
                await self._close_trade(trade, price, "take_profit", market_data)
                continue

            if raw_pnl >= tier_cfg["trail_activate"]:
                if trade["direction"] == "long":
                    trail_stop = trade["peak_price"] * (1 - tier_cfg["trail_distance"])
                    if price <= trail_stop:
                        await self._close_trade(trade, price, "trailing_stop", market_data)
                        continue
                else:
                    trail_stop = trade["peak_price"] * (1 + tier_cfg["trail_distance"])
                    if price >= trail_stop:
                        await self._close_trade(trade, price, "trailing_stop", market_data)
                        continue

            age = now - trade["opened_at"]
            if age >= tier_cfg["max_hold_sec"]:
                await self._close_trade(trade, price, "max_hold", market_data)
                continue

            engine = self.engines.get(asset)
            if engine:
                new_sl = engine.update_trailing_stop(trade, price)
                if new_sl is not None:
                    trade["stop_loss"] = new_sl

                exit_signal = engine.check_exits(trade, price)
                if exit_signal:
                    await self._close_trade(trade, price, exit_signal.reason, market_data)
                    continue

        self._check_daily_reset(now)
        self._sync_positions(sol_price)

        flow_score = self.flow.score()
        flow_vol = self.flow.volume()

        self.last_update = now
        self.metrics = {
            "flow_score": round(flow_score, 3),
            "flow_volume": round(flow_vol, 2),
            "sse_connected": self.sse.connected if self.sse else False,
            "active_trades": len([t for t in self._active_trades if t["status"] == "active"]),
            "daily_trades": self._daily_trade_count,
            "daily_pnl_usd": self._daily_pnl,
            "daily_pnl_pct": (self._daily_pnl / self.capital_allocated * 100) if self.capital_allocated > 0 else 0,
            "daily_wins": self._daily_wins,
            "daily_losses": self._daily_losses,
            "tier_stats": self._tier_stats,
            "engines_warmed": sum(1 for e in self.engines.values() if e.is_warmed_up),
            "asset_prices": {a: round(p, 6) for a, p in self._asset_prices.items() if p > 0},
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
        if self._last_trade_time > 0:
            cooldown = self.COOLDOWN_AFTER_LOSS_SEC if self._daily_losses > self._daily_wins else self.COOLDOWN_AFTER_WIN_SEC
            if now - self._last_trade_time < cooldown:
                return {"action": "wait", "reason": "cooldown"}

        flow_score = self.flow.score()
        flow_vol = self.flow.volume()

        if flow_vol < self.MIN_FLOW_VOLUME:
            return {"action": "hold", "reason": f"low_flow_volume_{flow_vol:.1f}"}

        leverage_tier = self._select_leverage_tier(flow_score)
        tier_cfg = LEVERAGE_TIERS[leverage_tier]

        best_signal = None
        best_confidence = 0.0

        active_longs = sum(1 for t in self._active_trades if t["status"] == "active" and t["direction"] == "long")
        active_shorts = sum(1 for t in self._active_trades if t["status"] == "active" and t["direction"] == "short")

        for asset, engine in self.engines.items():
            price = self._asset_prices.get(asset, 0)
            if price <= 0 or not engine.is_warmed_up:
                continue

            already_trading = any(t["asset"] == asset and t["status"] == "active" for t in self._active_trades)
            if already_trading:
                continue

            signal = engine.evaluate(price)
            if signal.type not in (SignalType.LONG, SignalType.SHORT):
                continue

            if signal.type == SignalType.LONG and flow_score < -0.2:
                continue
            if signal.type == SignalType.SHORT and flow_score > 0.2:
                continue

            if signal.type == SignalType.LONG and active_longs >= 3:
                continue
            if signal.type == SignalType.SHORT and active_shorts >= 3:
                continue

            allowed, adj_conf, learn_reason = self.learner.get_entry_filter(
                asset, signal.type.value, signal.confidence, signal.trade_type
            )
            if not allowed:
                continue

            flow_boost = 1.0 + abs(flow_score) * 0.3
            adj_conf *= flow_boost

            signal.confidence = adj_conf
            if adj_conf > best_confidence:
                best_signal = signal
                best_confidence = adj_conf

        if not best_signal:
            return {"action": "hold", "reason": "no_signal"}

        size = self._calculate_position_size(best_signal, tier_cfg["leverage"])
        if size < self.MIN_TRADE_USD:
            return {"action": "wait", "reason": "position_too_small"}

        action_str = "open_long" if best_signal.type == SignalType.LONG else "open_short"

        return {
            "action": action_str,
            "signal": best_signal,
            "leverage": tier_cfg["leverage"],
            "leverage_tier": leverage_tier,
            "deposit_usd": size,
            "flow_score": flow_score,
            "flow_volume": flow_vol,
            "reason": f"{best_signal.asset}: {best_signal.reason} flow={flow_score:+.2f} lev={tier_cfg['leverage']}x",
        }

    def _select_leverage_tier(self, flow_score: float) -> str:
        abs_flow = abs(flow_score)
        for tier_key in ["aggressive", "confident", "moderate"]:
            if abs_flow >= LEVERAGE_TIERS[tier_key]["min_flow"]:
                return tier_key
        return "moderate"

    def _calculate_position_size(self, signal: TradeSignal, leverage: float) -> float:
        base_capital = max(self.capital_allocated, 1.0)

        profile = self.learner.get_profile(signal.asset)
        if profile.trades >= 10 and profile.win_rate > 0.45:
            avg_win = abs(profile.avg_win_pct) / 100 if profile.avg_win_pct else 0.02
            avg_loss = abs(profile.avg_loss_pct) / 100 if profile.avg_loss_pct else 0.015
            wr = profile.win_rate
            if avg_loss > 0:
                kelly = wr - (1 - wr) / (avg_win / avg_loss)
                kelly = max(0.02, min(kelly * 0.5, 0.20))
            else:
                kelly = self.POSITION_SIZE_PCT
        else:
            kelly = self.POSITION_SIZE_PCT

        base = base_capital * kelly

        active_usd = sum(t["collateral_usd"] for t in self._active_trades if t["status"] == "active")
        available = base_capital - active_usd
        base = min(base, available)

        if self.capital_allocated > 0 and self._daily_pnl < 0:
            drawdown = abs(self._daily_pnl) / self.capital_allocated
            if drawdown > 0.02:
                base *= 0.5

        base *= min(signal.confidence / 0.8, 1.0)
        base *= self.learner.get_size_multiplier(signal.asset)

        return max(base, 0)

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        act = action["action"]
        if act not in ("open_long", "open_short"):
            return None

        signal: TradeSignal = action["signal"]
        leverage = action["leverage"]
        leverage_tier = action["leverage_tier"]
        tier_cfg = LEVERAGE_TIERS[leverage_tier]
        size = action["deposit_usd"]
        direction = "long" if act == "open_long" else "short"
        asset = signal.asset

        entry_price = self._asset_prices.get(asset, signal.entry_price)
        if entry_price <= 0:
            return None

        slippage = get_slippage(asset)
        if direction == "long":
            entry_price *= (1 + slippage)
        else:
            entry_price *= (1 - slippage)

        collateral = size
        notional = collateral * leverage

        if direction == "long":
            sl_price = entry_price * (1 - tier_cfg["sl_pct"] / leverage)
            tp_price = entry_price * (1 + tier_cfg["tp_pct"] / leverage)
        else:
            sl_price = entry_price * (1 + tier_cfg["sl_pct"] / leverage)
            tp_price = entry_price * (1 - tier_cfg["tp_pct"] / leverage)

        trade = {
            "id": str(uuid.uuid4())[:12],
            "direction": direction,
            "trade_type": f"mirror_{leverage_tier}",
            "asset": asset,
            "entry_price": entry_price,
            "current_price": entry_price,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "size_usd": notional,
            "leverage": leverage,
            "collateral_usd": collateral,
            "pnl_usd": 0,
            "pnl_pct": 0,
            "peak_price": entry_price,
            "mae": 0,
            "mfe": 0,
            "leverage_tier": leverage_tier,
            "flow_score_at_entry": action.get("flow_score", 0),
            "flow_volume_at_entry": action.get("flow_volume", 0),
            "regime_at_entry": signal.regime,
            "signal_confidence": signal.confidence,
            "signal_reason": signal.reason,
            "opened_at": time.time(),
            "last_update": time.time(),
            "status": "active",
        }

        if self.mode == "live" and self.router:
            try:
                result = await self.router.open_perp_position(asset, direction, notional, leverage)
                if result.get("oracle_price"):
                    trade["entry_price"] = result["oracle_price"]
                    trade["peak_price"] = result["oracle_price"]
                log.info(f"Mirror live {direction} {asset}: ${notional:.2f} {leverage_tier} -> {result.get('status')}")
            except Exception as e:
                log.error(f"Mirror live open failed: {e}")
                return None

        self._active_trades.append(trade)
        self._daily_trade_count += 1
        self._last_trade_time = time.time()

        if DATABASE_URL:
            TradeStore.save(trade)

        log.info(
            f"MIRROR {direction} {leverage_tier} {asset}: ${collateral:.2f}@{leverage}x "
            f"flow={action.get('flow_score', 0):+.2f} conf={signal.confidence:.2f} "
            f"entry=${entry_price:.6f} SL=${sl_price:.6f} TP=${tp_price:.6f}"
        )

        self._sync_positions(sol_price)
        return self.active_positions[-1] if self.active_positions else None

    async def _close_trade(self, trade: dict, exit_price: float, reason: str, market_data: dict):
        trade["status"] = "closing"
        asset = trade["asset"]

        if self.mode == "live" and self.router:
            try:
                await self.router.close_perp_position(asset)
            except Exception as e:
                log.error(f"Mirror close failed: {e}")

        trade["status"] = "closed"

        slippage = get_slippage(asset)
        if trade["direction"] == "long":
            actual_exit = exit_price * (1 - slippage)
            pnl_pct = (actual_exit - trade["entry_price"]) / trade["entry_price"] * trade["leverage"]
        else:
            actual_exit = exit_price * (1 + slippage)
            pnl_pct = (trade["entry_price"] - actual_exit) / trade["entry_price"] * trade["leverage"]

        fee_pct = 0.001 * trade["leverage"]
        pnl_pct -= fee_pct
        pnl_usd = trade["collateral_usd"] * pnl_pct

        trade["pnl_usd"] = pnl_usd
        trade["pnl_pct"] = pnl_pct * 100
        trade["exit_price"] = actual_exit
        trade["exit_reason"] = reason
        trade["closed_at"] = time.time()

        self._daily_pnl += pnl_usd
        if pnl_usd > 0:
            self._daily_wins += 1
        else:
            self._daily_losses += 1

        tier_key = trade.get("leverage_tier", "moderate")
        if tier_key in self._tier_stats:
            self._tier_stats[tier_key]["trades"] += 1
            self._tier_stats[tier_key]["pnl"] += pnl_usd
            if pnl_usd > 0:
                self._tier_stats[tier_key]["wins"] += 1

        self.learner.record_trade_close(trade)

        self._trade_log.append(dict(trade))
        if len(self._trade_log) > 200:
            self._trade_log = self._trade_log[-200:]

        if DATABASE_URL:
            TradeStore.save(trade)

        self._active_trades = [t for t in self._active_trades if t["status"] == "active"]

        log.info(
            f"MIRROR CLOSED {tier_key} {trade['direction']} {asset}: "
            f"PnL=${pnl_usd:.2f} ({pnl_pct*100:.1f}%) reason={reason} "
            f"MAE={trade.get('mae', 0):.1%} MFE={trade.get('mfe', 0):.1%} "
            f"flow@entry={trade.get('flow_score_at_entry', 0):+.2f}"
        )

    def _check_daily_reset(self, now: float):
        today_utc = int(now // 86400) * 86400
        if self._daily_reset_time < today_utc:
            if self._daily_trade_count > 0:
                log.info(
                    f"Mirror daily reset: {self._daily_trade_count}t "
                    f"W={self._daily_wins} L={self._daily_losses} PnL=${self._daily_pnl:.2f}"
                )
            self._daily_pnl = 0.0
            self._daily_trade_count = 0
            self._daily_wins = 0
            self._daily_losses = 0
            self._daily_reset_time = today_utc

    def _sync_positions(self, sol_price: float):
        from server.config import ORCA_WHIRLPOOL_SOL_USDC
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
                metadata={
                    "direction": trade["direction"],
                    "trade_type": trade["trade_type"],
                    "leverage": trade["leverage"],
                    "leverage_tier": trade.get("leverage_tier"),
                    "flow_score": trade.get("flow_score_at_entry"),
                    "asset": trade["asset"],
                },
            ))

    def get_state(self) -> dict:
        base = super().get_state()
        base["active_trades"] = [t for t in self._active_trades if t["status"] == "active"]
        base["trade_log"] = self._trade_log[-50:]
        base["tier_stats"] = self._tier_stats
        base["daily_stats"] = {
            "trades_today": self._daily_trade_count,
            "wins": self._daily_wins,
            "losses": self._daily_losses,
            "daily_pnl_usd": self._daily_pnl,
            "win_rate": self._daily_wins / max(self._daily_wins + self._daily_losses, 1),
        }
        base["flow"] = {
            "score": self.flow.score(),
            "volume": self.flow.volume(),
        }
        base["sse_connected"] = self.sse.connected if self.sse else False
        base["sse_stats"] = self.sse.stats if self.sse else {}
        base["wallet_cache_size"] = len(self._wallet_cache)
        return base

    def load_state(self, state: dict):
        super().load_state(state)
        self._trade_log = state.get("trade_log", [])
        self._tier_stats = state.get("tier_stats", self._tier_stats)
