from dataclasses import dataclass, field, asdict
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
    timestamp: float
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    closed: bool = False


TIMEFRAME_SECONDS = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
}

MAX_CANDLES = {
    Timeframe.M1: 1440,
    Timeframe.M5: 576,
    Timeframe.M15: 384,
    Timeframe.H1: 168,
    Timeframe.H4: 180,
}


def _candle_open_time(timestamp: float, period_seconds: int) -> float:
    return (int(timestamp) // period_seconds) * period_seconds


class CandleAggregator:
    def __init__(self):
        self._candles: dict[Timeframe, list[Candle]] = defaultdict(list)
        self._current: dict[Timeframe, Candle | None] = {tf: None for tf in Timeframe}

    def on_tick(self, price: float, timestamp: float):
        for tf in Timeframe:
            period = TIMEFRAME_SECONDS[tf]
            candle_start = _candle_open_time(timestamp, period)
            current = self._current[tf]

            if current is None or candle_start > current.timestamp:
                if current is not None:
                    current.closed = True
                    self._candles[tf].append(current)
                    max_c = MAX_CANDLES[tf]
                    if len(self._candles[tf]) > max_c:
                        self._candles[tf] = self._candles[tf][-max_c:]

                self._current[tf] = Candle(
                    timestamp=candle_start,
                    timeframe=tf,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                )
            else:
                current.high = max(current.high, price)
                current.low = min(current.low, price)
                current.close = price

    def get_candles(self, timeframe: Timeframe, count: int = 100) -> list[Candle]:
        candles = self._candles[timeframe]
        return candles[-count:] if len(candles) > count else list(candles)

    def get_current(self, timeframe: Timeframe) -> Candle | None:
        return self._current[timeframe]

    def get_closes(self, timeframe: Timeframe, count: int = 100) -> list[float]:
        return [c.close for c in self.get_candles(timeframe, count)]

    def get_highs(self, timeframe: Timeframe, count: int = 100) -> list[float]:
        return [c.high for c in self.get_candles(timeframe, count)]

    def get_lows(self, timeframe: Timeframe, count: int = 100) -> list[float]:
        return [c.low for c in self.get_candles(timeframe, count)]

    def candle_count(self, timeframe: Timeframe) -> int:
        return len(self._candles[timeframe])

    def get_state(self) -> dict:
        state = {"candles": {}, "current": {}}
        for tf in Timeframe:
            state["candles"][tf.value] = [asdict(c) for c in self._candles[tf]]
            cur = self._current[tf]
            state["current"][tf.value] = asdict(cur) if cur else None
        return state

    def load_state(self, state: dict):
        for tf in Timeframe:
            raw = state.get("candles", {}).get(tf.value, [])
            self._candles[tf] = [
                Candle(timeframe=Timeframe(d.pop("timeframe", tf.value)), **d)
                for d in raw
            ]
            cur = state.get("current", {}).get(tf.value)
            if cur:
                self._current[tf] = Candle(
                    timeframe=Timeframe(cur.pop("timeframe", tf.value)), **cur
                )
            else:
                self._current[tf] = None
