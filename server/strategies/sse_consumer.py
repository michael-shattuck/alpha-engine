import asyncio
import json
import time
import logging
from typing import Callable, Awaitable

import aiohttp
import httpx

log = logging.getLogger("sse_consumer")

RECONNECT_DELAY = 2
RECONNECT_MAX = 15


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
        delay = RECONNECT_DELAY
        while self._running:
            try:
                await self._connect_aiohttp()
                delay = RECONNECT_DELAY
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.stats["errors"] += 1
                self.connected = False
                self.stats["disconnects"] += 1
                log.debug(f"SSE reconnecting in {delay}s: {str(e)[:60]}")
                await asyncio.sleep(delay)
                delay = min(delay + 1, RECONNECT_MAX)

    async def _connect_aiohttp(self):
        url = f"{self.base_url}/api/stream"
        timeout = aiohttp.ClientTimeout(total=None, connect=5, sock_read=60)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise ConnectionError(f"SSE returned {response.status}")

                self.connected = True
                self.stats["connects"] += 1
                log.info(f"SSE connected to {url}")

                event_type = None
                async for line_bytes in response.content:
                    if not self._running:
                        break

                    line = line_bytes.decode("utf-8", errors="replace").strip()
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
                            log.debug(f"SSE callback error: {e}")

        self.connected = False
        self.stats["disconnects"] += 1
