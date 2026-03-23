import os
import time
import logging
import asyncio
import httpx

log = logging.getLogger("alerts")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ALERT_COOLDOWN = 60


class AlertManager:
    def __init__(self):
        self.http: httpx.AsyncClient | None = None
        self._last_alert_time: dict[str, float] = {}
        self._alert_history: list[dict] = []

    async def start(self):
        self.http = httpx.AsyncClient(timeout=10)

    async def stop(self):
        if self.http:
            await self.http.aclose()

    def _should_send(self, alert_key: str) -> bool:
        now = time.time()
        last = self._last_alert_time.get(alert_key, 0)
        if now - last < ALERT_COOLDOWN:
            return False
        self._last_alert_time[alert_key] = now
        return True

    async def send(self, level: str, title: str, message: str, alert_key: str | None = None):
        key = alert_key or f"{level}:{title}"
        if not self._should_send(key):
            return

        entry = {
            "timestamp": time.time(),
            "level": level,
            "title": title,
            "message": message,
        }
        self._alert_history.append(entry)
        if len(self._alert_history) > 500:
            self._alert_history = self._alert_history[-500:]

        log.info(f"ALERT [{level}] {title}: {message}")

        if DISCORD_WEBHOOK_URL and self.http:
            try:
                color_map = {"info": 3447003, "warning": 16776960, "error": 15158332, "critical": 10038562}
                level_emoji = {"info": "INFO", "warning": "WARN", "error": "ERROR", "critical": "CRIT"}
                await self.http.post(DISCORD_WEBHOOK_URL, json={
                    "embeds": [{
                        "title": f"[{level_emoji.get(level, level.upper())}] {title}",
                        "description": message,
                        "color": color_map.get(level, 0),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }]
                })
            except Exception as e:
                log.error(f"Discord webhook failed: {e}")

    async def position_opened(self, equity: float, leverage: float, borrowed: float, range_pct: float):
        await self.send("info", "Position Opened",
            f"Equity: ${equity:.2f} | Leverage: {leverage:.1f}x | "
            f"Borrowed: ${borrowed:.2f} | Range: {range_pct*100:.1f}%")

    async def position_closed(self, reason: str, pnl: float):
        level = "info" if pnl >= 0 else "warning"
        await self.send(level, "Position Closed",
            f"Reason: {reason} | PnL: ${pnl:.2f}")

    async def rebalance(self, reason: str, new_capital: float):
        await self.send("info", "Rebalance",
            f"Reason: {reason} | Capital: ${new_capital:.2f}")

    async def leverage_event(self, action: str, sol: float, usdc: float):
        await self.send("info", f"MarginFi {action}",
            f"SOL: {sol:.4f} | USDC: ${usdc:.2f}")

    async def error_alert(self, context: str, error: str):
        await self.send("error", f"Error: {context}", error[:500])

    async def risk_alert(self, risk_level: str, drawdown: float, details: str):
        level = "critical" if risk_level == "critical" else "warning"
        await self.send(level, f"Risk: {risk_level.upper()}",
            f"Drawdown: {drawdown:.2f}% | {details}")

    def get_history(self, limit: int = 100) -> list[dict]:
        return self._alert_history[-limit:]


alerts = AlertManager()
