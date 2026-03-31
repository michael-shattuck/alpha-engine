import json
import time
import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from server.config import DATABASE_URL

log = logging.getLogger("persistence")


def _conn():
    return psycopg2.connect(DATABASE_URL)


class TradeStore:
    @staticmethod
    def save(trade: dict):
        try:
            conn = _conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO trades (id, direction, trade_type, asset, entry_price, exit_price,
                    stop_loss, take_profit, size_usd, collateral_usd, leverage, pnl_usd, pnl_pct,
                    regime, signal_confidence, exit_reason, opened_at, closed_at, status, metadata)
                VALUES (%(id)s, %(direction)s, %(trade_type)s, %(asset)s, %(entry_price)s, %(exit_price)s,
                    %(stop_loss)s, %(take_profit)s, %(size_usd)s, %(collateral_usd)s, %(leverage)s,
                    %(pnl_usd)s, %(pnl_pct)s, %(regime)s, %(signal_confidence)s, %(exit_reason)s,
                    %(opened_at)s, %(closed_at)s, %(status)s, %(metadata)s)
                ON CONFLICT (id) DO UPDATE SET
                    exit_price = EXCLUDED.exit_price,
                    pnl_usd = EXCLUDED.pnl_usd,
                    pnl_pct = EXCLUDED.pnl_pct,
                    exit_reason = EXCLUDED.exit_reason,
                    closed_at = EXCLUDED.closed_at,
                    status = EXCLUDED.status
            """, {
                "id": trade["id"],
                "direction": trade["direction"],
                "trade_type": trade.get("trade_type", ""),
                "asset": trade.get("asset", "SOL"),
                "entry_price": trade["entry_price"],
                "exit_price": trade.get("exit_price"),
                "stop_loss": trade.get("stop_loss"),
                "take_profit": trade.get("take_profit"),
                "size_usd": trade.get("size_usd", 0),
                "collateral_usd": trade.get("collateral_usd", 0),
                "leverage": trade.get("leverage", 1),
                "pnl_usd": trade.get("pnl_usd", 0),
                "pnl_pct": trade.get("pnl_pct", 0),
                "regime": trade.get("regime_at_entry", ""),
                "signal_confidence": trade.get("signal_confidence", 0),
                "exit_reason": trade.get("exit_reason"),
                "opened_at": datetime.fromtimestamp(trade.get("opened_at", time.time()), tz=timezone.utc),
                "closed_at": datetime.fromtimestamp(trade["closed_at"], tz=timezone.utc) if trade.get("closed_at") else None,
                "status": trade.get("status", "active"),
                "metadata": json.dumps({k: v for k, v in trade.items() if k not in (
                    "id", "direction", "trade_type", "asset", "entry_price", "exit_price",
                    "stop_loss", "take_profit", "size_usd", "collateral_usd", "leverage",
                    "pnl_usd", "pnl_pct", "regime_at_entry", "signal_confidence", "exit_reason",
                    "opened_at", "closed_at", "status"
                )}),
            })
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"Trade save failed: {e}")

    @staticmethod
    def get_recent(limit: int = 100) -> list[dict]:
        try:
            conn = _conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM trades ORDER BY opened_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"Trade fetch failed: {e}")
            return []

    @staticmethod
    def get_active() -> list[dict]:
        try:
            conn = _conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM trades WHERE status = 'active' ORDER BY opened_at DESC")
            rows = cur.fetchall()
            conn.close()
            trades = []
            for r in rows:
                meta = json.loads(r.get("metadata", "{}")) if isinstance(r.get("metadata"), str) else (r.get("metadata") or {})
                trades.append({
                    "id": r["id"],
                    "direction": r["direction"],
                    "trade_type": r.get("trade_type", ""),
                    "asset": r.get("asset", "SOL"),
                    "entry_price": r["entry_price"],
                    "current_price": r.get("exit_price") or r["entry_price"],
                    "stop_loss": r.get("stop_loss", 0),
                    "take_profit": r.get("take_profit", 0),
                    "size_usd": r.get("size_usd", 0),
                    "leverage": r.get("leverage", 3.0),
                    "collateral_usd": r.get("collateral_usd", 0),
                    "pnl_usd": r.get("pnl_usd", 0),
                    "pnl_pct": r.get("pnl_pct", 0),
                    "peak_price": meta.get("peak_price", r["entry_price"]),
                    "regime_at_entry": r.get("regime", ""),
                    "signal_confidence": r.get("signal_confidence", 0),
                    "opened_at": r["opened_at"].timestamp() if r.get("opened_at") else time.time(),
                    "last_update": time.time(),
                    "status": "active",
                })
            return trades
        except Exception as e:
            log.error(f"Active trade fetch failed: {e}")
            return []

    @staticmethod
    def close_all_active():
        try:
            conn = _conn()
            cur = conn.cursor()
            cur.execute("UPDATE trades SET status = 'closed', exit_reason = 'system_restart' WHERE status = 'active'")
            count = cur.rowcount
            conn.commit()
            conn.close()
            if count > 0:
                log.info(f"Closed {count} stale active trades in DB")
            return count
        except Exception as e:
            log.error(f"Close all active failed: {e}")
            return 0


class SignalStore:
    @staticmethod
    def save(signal_type: str, asset: str, confidence: float, entry_price: float,
             stop_loss: float, take_profit: float, regime: str, trade_type: str,
             reason: str, indicators: dict):
        try:
            conn = _conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO signals (type, asset, confidence, entry_price, stop_loss,
                    take_profit, regime, trade_type, reason, indicators)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (signal_type, asset, confidence, entry_price, stop_loss,
                  take_profit, regime, trade_type, reason, json.dumps(indicators)))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"Signal save failed: {e}")


class SnapshotStore:
    @staticmethod
    def save(sol_price: float, total_value: float, total_pnl: float,
             regime: str, volatility: float, indicators: dict, allocations: dict):
        try:
            conn = _conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO snapshots (sol_price, total_value, total_pnl, regime,
                    volatility, indicators, allocations)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (sol_price, total_value, total_pnl, regime, volatility,
                  json.dumps(indicators), json.dumps(allocations)))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"Snapshot save failed: {e}")


class LPPositionStore:
    @staticmethod
    def save(position: dict):
        try:
            conn = _conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO lp_positions (id, pool, entry_price, lower_price, upper_price,
                    deposit_usd, equity_usd, borrowed_usd, leverage, range_pct, position_mint,
                    fees_earned, status, opened_at, closed_at, metadata)
                VALUES (%(id)s, %(pool)s, %(entry_price)s, %(lower_price)s, %(upper_price)s,
                    %(deposit_usd)s, %(equity_usd)s, %(borrowed_usd)s, %(leverage)s, %(range_pct)s,
                    %(position_mint)s, %(fees_earned)s, %(status)s, %(opened_at)s, %(closed_at)s, %(metadata)s)
                ON CONFLICT (id) DO UPDATE SET
                    fees_earned = EXCLUDED.fees_earned,
                    status = EXCLUDED.status,
                    closed_at = EXCLUDED.closed_at
            """, {
                "id": position.get("id", ""),
                "pool": position.get("pool", ""),
                "entry_price": position.get("entry_price", 0),
                "lower_price": position.get("lower_price", 0),
                "upper_price": position.get("upper_price", 0),
                "deposit_usd": position.get("deposit_usd", 0),
                "equity_usd": position.get("metadata", {}).get("equity", 0),
                "borrowed_usd": position.get("metadata", {}).get("borrowed_usd", 0),
                "leverage": position.get("metadata", {}).get("leverage", 1),
                "range_pct": position.get("metadata", {}).get("range_pct", 0),
                "position_mint": position.get("metadata", {}).get("position_mint", ""),
                "fees_earned": position.get("fees_earned_usd", 0),
                "status": position.get("status", "active"),
                "opened_at": datetime.fromtimestamp(position.get("opened_at", time.time()), tz=timezone.utc),
                "closed_at": datetime.fromtimestamp(position["closed_at"], tz=timezone.utc) if position.get("closed_at") else None,
                "metadata": json.dumps(position.get("metadata", {})),
            })
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"LP position save failed: {e}")
