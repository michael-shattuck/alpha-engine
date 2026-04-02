import asyncio
import time
import uuid
import logging
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.strategies.sse_consumer import SSEConsumer
from server.execution.drift import DriftExecutor, MARKET_INDEX, SETTLEMENT_MARKETS
from server.config import DATABASE_URL
from server.persistence import TradeStore

log = logging.getLogger("smart_money_mirror")

DRIFT_MINT_TO_SYMBOL = {
    "So11111111111111111111111111111111111111112": "SOL",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
    "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL": "JTO",
    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3": "PYTH",
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": "WIF",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv": "PENGU",
    "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump": "FARTCOIN",
    "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN": "TRUMP",
    "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": "POPCAT",
    "A98UDy7z8MfmWnTQt6cKjje7UfqV3pTLf4yEbuwL2HrH": "MOODENG",
    "5q2EfdKrV4oSaUGBWCMWjXbYNuSPEFMTig4kgFquTCkB": "SEI",
    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof": "RENDER",
    "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ": "W",
    "TNSRxcUxoT9xBG3de7PiJyTDYu7kskLqcpddxnEJAS6": "TNSR",
    "DriFtupJYLTosbwoN8koMbEYSx54aFAVLddWsbksjwg7": "DRIFT",
    "CLoUDKc4Ane7HeQcPpE3YHnznRxhMimJ4MyaUqyHFzAu": "CLOUD",
    "FUAfBo2jgks6gB4Z4LfZkqSZgzNucisEHqnNebaRxM1P": "MELANIA",
    "MEFNBXixkEbait3xn9bkm8WsJzXtVsaJEn4c8Sam21u": "ME",
    "3S8qX1MsMqRbiwKg2cQyx7nis1oHMgaCuc9c4VfvVdPN": "MOTHER",
    "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98jwC": "AI16Z",
    "KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVFE7suUBo9sS": "KMNO",
    "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump": "GOAT",
    "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump": "PNUT",
    "A8C3xuqscfmyLrte3VmTqrAq8kgMASius9AFNANwpump": "FWOG",
    "5mbK36SZ7J19An8jFochhQS4of8g6BwUjbeCSxBSoWdp": "MICHI",
    "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5": "MEW",
    "BRqZqwPuPLQXQ13LhAVumYP1qewLtnW28Mgk5k4cepLV": "KAITO",
    "Ey59PH7Z4BFU4HjyKnyMdWt5GGN76KazTAwQihoUXRnk": "LAUNCHCOIN",
    "AVeMcebYoKKVkJFmwVKWG21EemuN9vGFVFdpNtwcpump": "ASTER",
    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R": "RAY",
    "BZLbGTNCSFfoth2GYDtwr7e4imWzpR5jqcUuGEwr646K": "IO",
}

DRIFT_SYMBOL_SET = set(MARKET_INDEX.keys()) - {"1MBONK", "1MPEPE", "1KMEW", "1KWEN"}

DEEP_LIQUIDITY = {"SOL", "BTC", "ETH", "XRP", "DOGE", "LINK", "BNB", "LTC", "ADA", "AVAX"}
MEDIUM_LIQUIDITY = {"JUP", "JTO", "SUI", "SEI", "PYTH", "RENDER", "RAY", "DRIFT", "INJ", "OP", "ARB", "TON", "HNT", "TIA", "HYPE"}
THIN_LIQUIDITY = {"WIF", "BONK", "PENGU", "POPCAT", "GOAT", "PNUT", "AI16Z", "TRUMP", "IO", "KMNO", "TNSR", "ME", "BERA"}
THINNEST_LIQUIDITY = {"FARTCOIN", "MOODENG", "MELANIA", "FWOG", "MICHI", "MEW", "MOTHER", "KAITO", "LAUNCHCOIN", "PUMP", "ASTER", "CLOUD", "ZEX", "DBR", "DYM", "TAO", "RLB", "IP", "W", "APT", "POL", "PAXG"}

def get_slippage(symbol: str) -> float:
    if symbol in DEEP_LIQUIDITY:
        return 0.001
    if symbol in MEDIUM_LIQUIDITY:
        return 0.0015
    if symbol in THIN_LIQUIDITY:
        return 0.002
    return 0.003

SLIPPAGE_MODEL = {s: get_slippage(s) for s in MARKET_INDEX if s not in ("1MBONK", "1MPEPE", "1KMEW", "1KWEN")}

CONVICTION_TIERS = {
    1: {"name": "max", "leverage": 10.0, "capital_pct": 0.40, "min_confluence": 60, "min_consensus": 2,
        "wallet_tiers": {"gods", "elite_sniper"},
        "sl_pct": 0.15, "tp_pct": 0.30, "trail_activate": 0.08, "trail_distance": 0.05, "max_hold_hours": 24},
    2: {"name": "high", "leverage": 7.0, "capital_pct": 0.30, "min_confluence": 50, "min_consensus": 0,
        "wallet_tiers": {"diamond", "proven_trader"},
        "sl_pct": 0.14, "tp_pct": 0.25, "trail_activate": 0.08, "trail_distance": 0.05, "max_hold_hours": 12},
    3: {"name": "moderate", "leverage": 5.0, "capital_pct": 0.20, "min_confluence": 40, "min_consensus": 0,
        "wallet_tiers": {"gold"},
        "sl_pct": 0.10, "tp_pct": 0.20, "trail_activate": 0.06, "trail_distance": 0.04, "max_hold_hours": 6},
}


class SmartMoneyMirror(BaseStrategy):
    STRATEGY_ID = "smart_money_mirror"
    STRATEGY_NAME = "Smart Money Mirror"

    MAX_CONCURRENT_POSITIONS = 4
    DAILY_LOSS_LIMIT_PCT = 5.0
    SIGNAL_STALE_SEC = 60
    COOLDOWN_SAME_ASSET_SEC = 300

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self.drift: DriftExecutor | None = None
        self.sse: SSEConsumer | None = None
        self._signal_buffer: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._wallet_cache: dict = {}
        self._active_trades: list[dict] = []
        self._trade_log: list[dict] = []
        self._signal_log: list[dict] = []
        self._daily_pnl: float = 0.0
        self._daily_wins: int = 0
        self._daily_losses: int = 0
        self._daily_trade_count: int = 0
        self._daily_reset_time: float = 0.0
        self._last_trade_per_asset: dict[str, float] = {}
        self._tier_stats: dict[int, dict] = {t: {"trades": 0, "wins": 0, "pnl": 0.0} for t in [1, 2, 3]}
        self._last_wallet_refresh: float = 0.0

    async def warmup(self, price_history: list[dict]):
        from server.config import SMART_MONEY_SSE_URL

        self.drift = DriftExecutor(paper_mode=(self.mode != "live"))
        await self.drift.start()

        self.sse = SSEConsumer(SMART_MONEY_SSE_URL, self._on_sse_signal)
        self._wallet_cache = await self.sse.fetch_wallets()
        await self.sse.start()

        if DATABASE_URL:
            active = TradeStore.get_active()
            mirror_trades = [t for t in active if t.get("trade_type", "").startswith("mirror")]
            if mirror_trades:
                self._active_trades = mirror_trades
                log.info(f"Restored {len(mirror_trades)} mirror trades from DB")

        self._daily_reset_time = int(time.time() // 86400) * 86400
        log.info(f"Smart Money Mirror warmup: {len(self._wallet_cache)} wallets cached, SSE {'connected' if self.sse.connected else 'connecting'}")

    async def _on_sse_signal(self, event: dict):
        action = event.get("action", "")
        if not action:
            action = event.get("type", event.get("side", ""))
        action = action.upper()
        if action not in ("BUY", "SELL"):
            log.debug(f"SSE event skipped: action={event.get('action','')} keys={list(event.keys())[:5]}")
            return

        mint = event.get("mint", "")
        token_name = event.get("token", "").upper()
        symbol = DRIFT_MINT_TO_SYMBOL.get(mint)
        if not symbol:
            if token_name in DRIFT_SYMBOL_SET:
                symbol = token_name
            else:
                log.debug(f"Rejected: {token_name} mint={mint[:12]}... not on Drift")
                return

        if symbol in SETTLEMENT_MARKETS:
            return

        wallet_prefix = event.get("wallet", "")[:12]
        wallet_info = self._wallet_cache.get(wallet_prefix, {})
        wallet_tier = wallet_info.get("tier", "unknown")
        confluence = wallet_info.get("confluence_score", 0)
        consensus = wallet_info.get("consensus_count", 0)
        copy_wr = wallet_info.get("copy_wr", 0)

        enriched = {
            "timestamp": time.time(),
            "action": action,
            "symbol": symbol,
            "mint": mint,
            "wallet": wallet_prefix,
            "wallet_tier": wallet_tier,
            "confluence": confluence,
            "consensus": consensus,
            "copy_wr": copy_wr,
            "size_sol": event.get("size_sol", 0),
            "price": event.get("price", 0),
        }

        try:
            self._signal_buffer.put_nowait(enriched)
        except asyncio.QueueFull:
            try:
                self._signal_buffer.get_nowait()
                self._signal_buffer.put_nowait(enriched)
            except Exception:
                pass

        self._signal_log.append(enriched)
        if len(self._signal_log) > 200:
            self._signal_log = self._signal_log[-200:]

    def _classify_tier(self, signal: dict) -> int | None:
        wallet_tier = signal.get("wallet_tier", "unknown")
        confluence = signal.get("confluence", 0)
        consensus = signal.get("consensus", 0)

        for tier_num, cfg in sorted(CONVICTION_TIERS.items()):
            if wallet_tier in cfg["wallet_tiers"]:
                if confluence >= cfg["min_confluence"] and consensus >= cfg["min_consensus"]:
                    return tier_num
        return None

    async def update(self, market_data: dict):
        now = time.time()
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return

        if now - self._last_wallet_refresh > 300 and self.sse:
            self._wallet_cache = await self.sse.fetch_wallets()
            self._last_wallet_refresh = now

        prices = {}
        if self.drift and self.drift.client:
            prices = self.drift.get_oracle_prices()
        prices["SOL"] = sol_price

        for trade in list(self._active_trades):
            if trade["status"] != "active":
                continue

            asset = trade["asset"]
            price = prices.get(asset, 0)
            if price <= 0:
                continue

            trade["current_price"] = price
            leverage = trade["leverage"]

            if trade["direction"] == "long":
                raw_pnl = (price - trade["entry_price"]) / trade["entry_price"] * leverage
                trade["peak_price"] = max(trade.get("peak_price", trade["entry_price"]), price)
                trade["mae"] = min(trade.get("mae", 0), raw_pnl)
                trade["mfe"] = max(trade.get("mfe", 0), raw_pnl)
            else:
                raw_pnl = (trade["entry_price"] - price) / trade["entry_price"] * leverage
                trade["peak_price"] = min(trade.get("peak_price", trade["entry_price"]), price)
                trade["mae"] = min(trade.get("mae", 0), raw_pnl)
                trade["mfe"] = max(trade.get("mfe", 0), raw_pnl)

            trade["pnl_pct"] = raw_pnl * 100
            trade["pnl_usd"] = trade["collateral_usd"] * raw_pnl
            trade["last_update"] = now

            tier_cfg = CONVICTION_TIERS.get(trade.get("conviction_tier", 3), CONVICTION_TIERS[3])

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

            age_hours = (now - trade["opened_at"]) / 3600
            if age_hours >= tier_cfg["max_hold_hours"]:
                await self._close_trade(trade, price, f"max_hold_{tier_cfg['max_hold_hours']}h", market_data)
                continue

        self._check_daily_reset(now)
        self._sync_positions(sol_price)

        self.last_update = now
        self.metrics = {
            "sse_connected": self.sse.connected if self.sse else False,
            "signal_buffer": self._signal_buffer.qsize(),
            "wallet_cache_size": len(self._wallet_cache),
            "active_trades": len([t for t in self._active_trades if t["status"] == "active"]),
            "daily_trades": self._daily_trade_count,
            "daily_pnl": self._daily_pnl,
            "tier_stats": self._tier_stats,
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
        signals = []
        while not self._signal_buffer.empty():
            try:
                sig = self._signal_buffer.get_nowait()
                if now - sig["timestamp"] <= self.SIGNAL_STALE_SEC:
                    signals.append(sig)
            except asyncio.QueueEmpty:
                break

        best = None
        best_score = -1

        for sig in signals:
            tier = self._classify_tier(sig)
            if tier is None:
                continue

            asset = sig["symbol"]
            last_trade_time = self._last_trade_per_asset.get(asset, 0)
            if now - last_trade_time < self.COOLDOWN_SAME_ASSET_SEC:
                continue

            already_trading = any(t["asset"] == asset and t["status"] == "active" for t in self._active_trades)
            if already_trading:
                continue

            if sig["action"] == "SELL" and tier > 2:
                continue

            score = (4 - tier) * 1000 + sig.get("confluence", 0) * 10 + sig.get("copy_wr", 0)
            if score > best_score:
                best_score = score
                best = (sig, tier)

        if not best:
            return {"action": "hold", "reason": "no_qualifying_signal"}

        sig, tier = best
        tier_cfg = CONVICTION_TIERS[tier]
        direction = "long" if sig["action"] == "BUY" else "short"
        size = self.capital_allocated * tier_cfg["capital_pct"]

        return {
            "action": f"open_{direction}",
            "signal": sig,
            "conviction_tier": tier,
            "leverage": tier_cfg["leverage"],
            "size_usd": size,
        }

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        act = action["action"]
        if act not in ("open_long", "open_short"):
            return None

        sig = action["signal"]
        tier = action["conviction_tier"]
        tier_cfg = CONVICTION_TIERS[tier]
        leverage = action["leverage"]
        size = action["size_usd"]
        direction = "long" if act == "open_long" else "short"
        asset = sig["symbol"]

        entry_price = sig.get("price", 0)
        if entry_price <= 0:
            prices = self.drift.get_oracle_prices() if self.drift and self.drift.client else {}
            entry_price = prices.get(asset, sol_price if asset == "SOL" else 0)
        if entry_price <= 0:
            return None

        slippage = SLIPPAGE_MODEL.get(asset, 0.002)
        if direction == "long":
            entry_price *= (1 + slippage)
        else:
            entry_price *= (1 - slippage)

        collateral = size
        notional = collateral * leverage

        trade = {
            "id": str(uuid.uuid4())[:12],
            "direction": direction,
            "trade_type": f"mirror_t{tier}",
            "asset": asset,
            "entry_price": entry_price,
            "current_price": entry_price,
            "stop_loss": entry_price * (1 - tier_cfg["sl_pct"] / leverage) if direction == "long" else entry_price * (1 + tier_cfg["sl_pct"] / leverage),
            "take_profit": entry_price * (1 + tier_cfg["tp_pct"] / leverage) if direction == "long" else entry_price * (1 - tier_cfg["tp_pct"] / leverage),
            "size_usd": notional,
            "leverage": leverage,
            "collateral_usd": collateral,
            "pnl_usd": 0,
            "pnl_pct": 0,
            "peak_price": entry_price,
            "mae": 0,
            "mfe": 0,
            "conviction_tier": tier,
            "regime_at_entry": "mirror",
            "signal_confidence": sig.get("confluence", 0) / 100,
            "source_wallet": sig.get("wallet", ""),
            "wallet_tier": sig.get("wallet_tier", ""),
            "confluence": sig.get("confluence", 0),
            "consensus": sig.get("consensus", 0),
            "opened_at": time.time(),
            "last_update": time.time(),
            "status": "active",
        }

        if self.mode == "live" and self.drift:
            try:
                result = await self.drift.open_perp_position(asset, direction, notional, leverage)
                if result.get("oracle_price"):
                    trade["entry_price"] = result["oracle_price"]
                    trade["peak_price"] = result["oracle_price"]
                log.info(f"Mirror live {direction} {asset}: ${notional:.2f} T{tier} -> {result.get('status')}")
            except Exception as e:
                log.error(f"Mirror live open failed: {e}")
                return None

        self._active_trades.append(trade)
        self._daily_trade_count += 1
        self._last_trade_per_asset[asset] = time.time()

        if DATABASE_URL:
            TradeStore.save(trade)

        log.info(
            f"MIRROR {direction} T{tier} {asset}: ${collateral:.2f} col, {leverage}x lev, "
            f"wallet={sig.get('wallet_tier')} conf={sig.get('confluence')} "
            f"entry=${entry_price:.6f}"
        )

        self._sync_positions(sol_price)
        return self.active_positions[-1] if self.active_positions else None

    async def _close_trade(self, trade: dict, exit_price: float, reason: str, market_data: dict):
        trade["status"] = "closing"
        asset = trade["asset"]

        if self.mode == "live" and self.drift:
            try:
                await self.drift.close_perp_position(asset)
            except Exception as e:
                log.error(f"Mirror close failed: {e}")

        trade["status"] = "closed"

        slippage = SLIPPAGE_MODEL.get(asset, 0.002)
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

        tier = trade.get("conviction_tier", 3)
        if tier in self._tier_stats:
            self._tier_stats[tier]["trades"] += 1
            self._tier_stats[tier]["pnl"] += pnl_usd
            if pnl_usd > 0:
                self._tier_stats[tier]["wins"] += 1

        self._trade_log.append(dict(trade))
        if len(self._trade_log) > 200:
            self._trade_log = self._trade_log[-200:]

        if DATABASE_URL:
            TradeStore.save(trade)

        self._active_trades = [t for t in self._active_trades if t["status"] == "active"]

        log.info(
            f"MIRROR CLOSED T{tier} {trade['direction']} {asset}: "
            f"PnL=${pnl_usd:.2f} ({pnl_pct*100:.1f}%) reason={reason} "
            f"MAE={trade.get('mae',0):.1%} MFE={trade.get('mfe',0):.1%}"
        )

    def _check_daily_reset(self, now: float):
        today_utc = int(now // 86400) * 86400
        if self._daily_reset_time < today_utc:
            if self._daily_trade_count > 0:
                log.info(f"Mirror daily reset: {self._daily_trade_count}t W={self._daily_wins} L={self._daily_losses} PnL=${self._daily_pnl:.2f}")
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
                    "conviction_tier": trade.get("conviction_tier"),
                    "wallet_tier": trade.get("wallet_tier"),
                },
            ))

    def get_state(self) -> dict:
        base = super().get_state()
        base["active_trades"] = [t for t in self._active_trades if t["status"] == "active"]
        base["trade_log"] = self._trade_log[-50:]
        base["signal_log"] = self._signal_log[-50:]
        base["tier_stats"] = self._tier_stats
        base["daily_stats"] = {
            "trades_today": self._daily_trade_count,
            "wins": self._daily_wins,
            "losses": self._daily_losses,
            "daily_pnl_usd": self._daily_pnl,
            "win_rate": self._daily_wins / max(self._daily_wins + self._daily_losses, 1),
        }
        base["sse_connected"] = self.sse.connected if self.sse else False
        base["sse_stats"] = self.sse.stats if self.sse else {}
        base["wallet_cache_size"] = len(self._wallet_cache)
        return base

    def load_state(self, state: dict):
        super().load_state(state)
        self._trade_log = state.get("trade_log", [])
        self._tier_stats = state.get("tier_stats", self._tier_stats)
