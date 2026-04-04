import asyncio
import json
import time
import logging
from typing import Callable, Awaitable

import httpx

log = logging.getLogger("sse_consumer")

RECONNECT_BASE = 1
RECONNECT_MAX = 10


class SSEConsumer:
    def __init__(self, base_url: str, callback: Callable[[dict], Awaitable[None]]):
        self.base_url = base_url.rstrip("/")
        self._callback = callback
        self.connected = False
        self._running = False
        self._task: asyncio.Task | None = None
        self.stats = {
            "connects": 0,
            "disconnects": 0,
            "events_received": 0,
            "last_event_time": 0,
            "errors": 0,
        }

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        log.info(f"SSE consumer started: {self.base_url}")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.connected = False

    async def fetch_wallets(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(f"{self.base_url}/api/wallets")
                if r.status_code == 200:
                    data = r.json()
                    wallets = {}
                    for w in data if isinstance(data, list) else data.get("wallets", []):
                        addr = w.get("address", "")
                        if addr:
                            wallets[addr[:12]] = w
                    log.info(f"Fetched {len(wallets)} wallets from smart money bot")
                    return wallets
        except Exception as e:
            log.warning(f"Failed to fetch wallets: {e}")
        return {}

    async def _run(self):
        delay = RECONNECT_BASE
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.stats["errors"] += 1
                log.warning(f"SSE error: {e}, reconnecting in {delay}s")
                self.connected = False
                self.stats["disconnects"] += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)

    async def _connect(self):
        url = f"{self.base_url}/api/stream"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5, read=30.0)) as http:
            async with http.stream("GET", url) as response:
                if response.status_code != 200:
                    raise ConnectionError(f"SSE returned {response.status_code}")

                self.connected = True
                self.stats["connects"] += 1
                log.info(f"SSE connected to {url}")

                event_type = None
                async for line in response.aiter_lines():
                    if not self._running:
                        break

                    line = line.strip()
                    if not line:
                        event_type = None
                        continue

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:") and event_type in ("trade", "state"):
                        try:
                            data = json.loads(line[5:].strip())
                            data["_event_type"] = event_type
                            self.stats["events_received"] += 1
                            self.stats["last_event_time"] = time.time()
                            await self._callback(data)
                        except json.JSONDecodeError:
                            pass
                        except Exception as e:
                            log.warning(f"SSE callback error: {e}")

        self.connected = False
        self.stats["disconnects"] += 1
