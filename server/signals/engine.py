import time
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum

from server.signals.candles import CandleAggregator, Timeframe
from server.signals.regime import RegimeDetector, MarketRegime
from server.signals import indicators as ind

log = logging.getLogger("signal_engine")


class SignalType(str, Enum):
    LONG = "long"
    SHORT = "short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    NO_SIGNAL = "no_signal"


@dataclass
class TradeSignal:
    type: SignalType
    asset: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    regime: str
    trade_type: str
    reason: str
    timestamp: float
    indicators: dict = field(default_factory=dict)


class SignalEngine:
    MIN_CONFIDENCE = 0.40

    EXTREME_RSI_LONG = 25
    EXTREME_RSI_SHORT = 75

    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD_AGGRESSIVE = 35
    RSI_OVERBOUGHT_AGGRESSIVE = 65
    BB_ENTRY_THRESHOLD = 0.02

    MR_TP_PCT = 0.007
    MR_SL_PCT = 0.003
    MR_MAX_HOLD = 1200

    AMR_TP_PCT = 0.012
    AMR_SL_PCT = 0.005
    AMR_MAX_HOLD = 900

    MOM_VELOCITY_MIN = 0.4
    MOM_TP_PCT = 0.015
    MOM_SL_PCT = 0.005
    MOM_TRAILING_STOP_PCT = 0.006
    MOM_MAX_HOLD = 5400

    COOLDOWN_AFTER_CLOSE = 60
    COOLDOWN_AFTER_LOSS = 120

    def __init__(self, asset: str = "SOL"):
        self.asset = asset
        self.candles = CandleAggregator()
        self.regime_detector = RegimeDetector()
        self._last_close_time: float = 0
        self._last_close_was_loss: bool = False
        self._signal_history: list[dict] = []
        self._warmed_up: bool = False

    async def warmup(self, price_history: list[dict]):
        if not price_history:
            log.warning("No price history for warmup")
            return
        count = 0
        for point in price_history:
            t = point.get("t", 0)
            p = point.get("p", 0)
            if t > 0 and p > 0:
                self.candles.on_tick(p, t)
                count += 1
        candle_counts = {tf.value: self.candles.candle_count(tf) for tf in Timeframe}
        log.info(f"Warmup complete: {count} ticks -> candles: {candle_counts}")
        self._warmed_up = count >= 100

    async def warmup_from_lens(self):
        import httpx
        from server.signals.candles import Candle, TIMEFRAME_SECONDS, MAX_CANDLES
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                tf_map = {"1m": Timeframe.M1, "5m": Timeframe.M5, "15m": Timeframe.M15, "1h": Timeframe.H1}
                for interval, tf in tf_map.items():
                    r = await http.get(
                        "https://lens.soon.app/api/assets/SOL/history",
                        params={"interval": interval, "limit": "5000"},
                        headers={"x-api-key": "your-dev-key"},
                    )
                    if r.status_code != 200:
                        continue
                    raw = r.json().get("data", [])
                    candles_list = []
                    for c in raw:
                        candles_list.append(Candle(
                            timestamp=c["timestamp"] / 1000,
                            timeframe=tf,
                            open=c["open"],
                            high=c["high"],
                            low=c["low"],
                            close=c["close"],
                            volume=c.get("volume", 0),
                            closed=True,
                        ))
                    max_c = MAX_CANDLES[tf]
                    self.candles._candles[tf] = candles_list[-max_c:]
                    if candles_list:
                        last = candles_list[-1]
                        self.candles._current[tf] = Candle(
                            timestamp=last.timestamp + TIMEFRAME_SECONDS[tf],
                            timeframe=tf,
                            open=last.close, high=last.close,
                            low=last.close, close=last.close,
                        )

            candle_counts = {tf.value: self.candles.candle_count(tf) for tf in Timeframe}
            log.info(f"Lens warmup complete: {candle_counts}")
            self._warmed_up = True
        except Exception as e:
            log.warning(f"Lens warmup failed: {e}")

    @property
    def is_warmed_up(self) -> bool:
        if self._warmed_up:
            return True
        return self.candles.candle_count(Timeframe.M5) >= 25

    def on_tick(self, price: float, timestamp: float):
        self.candles.on_tick(price, timestamp)

    @property
    def current_price(self) -> float:
        c = self.candles.get_current(Timeframe.M1)
        return c.close if c else 0

    TREND_TP_PCT = 0.02
    TREND_SL_PCT = 0.015
    TREND_MAX_HOLD = 5400

    def evaluate(self, current_price: float = 0) -> TradeSignal:
        now = time.time()
        price = current_price or self.current_price
        if price <= 0:
            return self._no_signal(price)

        closes_15m = self.candles.get_closes(Timeframe.M15, 30)
        if len(closes_15m) < 22:
            closes_5m = self.candles.get_closes(Timeframe.M5, 30)
            if len(closes_5m) < 22:
                return self._no_signal(price, f"warmup (need 22 candles)")
            closes = closes_5m
            timeframe_label = "5m"
        else:
            closes = closes_15m
            timeframe_label = "15m"

        cooldown = self.COOLDOWN_AFTER_LOSS if self._last_close_was_loss else self.COOLDOWN_AFTER_CLOSE
        if now - self._last_close_time < cooldown:
            return self._no_signal(price, "cooldown")

        rsi = ind.rsi(closes)
        rsi_prev = ind.rsi(closes[:-1]) if len(closes) > 16 else rsi
        ema_9 = ind.ema(closes, 9)
        ema_21 = ind.ema(closes, 21)
        velocity = ind.price_velocity(closes, 3)

        long_signal = ema_9 > ema_21 and rsi > rsi_prev and 40 < rsi < 65 and velocity > 0.1
        short_signal = ema_9 < ema_21 and rsi < rsi_prev and 35 < rsi < 60 and velocity < -0.1

        if not long_signal and not short_signal:
            reasons = []
            if ema_9 <= ema_21 and ema_9 >= ema_21:
                reasons.append("EMAs flat")
            elif ema_9 > ema_21:
                if rsi <= rsi_prev:
                    reasons.append(f"RSI falling ({rsi:.0f})")
                elif rsi >= 65:
                    reasons.append(f"RSI too high ({rsi:.0f})")
                elif rsi <= 40:
                    reasons.append(f"RSI too low ({rsi:.0f})")
                elif velocity <= 0.1:
                    reasons.append(f"velocity too low ({velocity:.2f}%)")
            else:
                if rsi >= rsi_prev:
                    reasons.append(f"RSI rising ({rsi:.0f})")
                elif rsi <= 35:
                    reasons.append(f"RSI too low ({rsi:.0f})")
                elif rsi >= 60:
                    reasons.append(f"RSI too high ({rsi:.0f})")
                elif velocity >= -0.1:
                    reasons.append(f"velocity too high ({velocity:.2f}%)")
            ema_dir = "up" if ema_9 > ema_21 else "down"
            return self._no_signal(price, f"EMA {ema_dir} RSI={rsi:.0f} vel={velocity:.2f}% {' '.join(reasons)}")

        if long_signal:
            confidence = 0.7
            reasons = [f"EMA9>EMA21 ({timeframe_label})", f"RSI={rsi:.0f} rising", f"vel={velocity:.2f}%"]
            if rsi < 50:
                confidence += 0.1
                reasons.append("RSI<50 (room to run)")
            if velocity > 0.3:
                confidence += 0.1
                reasons.append("strong velocity")

            return TradeSignal(
                type=SignalType.LONG, asset=self.asset, confidence=confidence,
                entry_price=price,
                stop_loss=price * (1 - self.TREND_SL_PCT),
                take_profit=price * (1 + self.TREND_TP_PCT),
                regime="trend_follow", trade_type="trend_follow",
                reason=", ".join(reasons), timestamp=now,
                indicators={"rsi": rsi, "ema_9": ema_9, "ema_21": ema_21, "velocity": velocity},
            )
        else:
            confidence = 0.7
            reasons = [f"EMA9<EMA21 ({timeframe_label})", f"RSI={rsi:.0f} falling", f"vel={velocity:.2f}%"]
            if rsi > 50:
                confidence += 0.1
                reasons.append("RSI>50 (room to fall)")
            if velocity < -0.3:
                confidence += 0.1
                reasons.append("strong velocity")

            return TradeSignal(
                type=SignalType.SHORT, asset=self.asset, confidence=confidence,
                entry_price=price,
                stop_loss=price * (1 + self.TREND_SL_PCT),
                take_profit=price * (1 - self.TREND_TP_PCT),
                regime="trend_follow", trade_type="trend_follow",
                reason=", ".join(reasons), timestamp=now,
                indicators={"rsi": rsi, "ema_9": ema_9, "ema_21": ema_21, "velocity": velocity},
            )

    def _confirmed_reversal(self, price: float, direction: str, rsi_val: float, assessment) -> TradeSignal:
        confidence = 0.80
        if direction == "long":
            return TradeSignal(
                type=SignalType.LONG, asset=self.asset, confidence=confidence,
                entry_price=price,
                stop_loss=price * (1 - self.AMR_SL_PCT),
                take_profit=price * (1 + self.AMR_TP_PCT),
                regime=assessment.regime.value, trade_type="confirmed_reversal",
                reason=f"RSI={rsi_val:.1f} turning up from extreme (counter-trend)",
                timestamp=time.time(),
                indicators={"rsi_5m": rsi_val},
            )
        else:
            return TradeSignal(
                type=SignalType.SHORT, asset=self.asset, confidence=confidence,
                entry_price=price,
                stop_loss=price * (1 + self.AMR_SL_PCT),
                take_profit=price * (1 - self.AMR_TP_PCT),
                regime=assessment.regime.value, trade_type="confirmed_reversal",
                reason=f"RSI={rsi_val:.1f} turning down from extreme (counter-trend)",
                timestamp=time.time(),
                indicators={"rsi_5m": rsi_val},
            )

    def _trend_signal(self, price: float, direction: str, rsi_val: float, velocity: float,
                      bb_lower: float, bb_middle: float, bb_upper: float, assessment) -> TradeSignal:
        confidence = 0.0
        reasons = []

        if direction == "short":
            if velocity < -0.2:
                confidence += 0.35
                reasons.append(f"vel={velocity:.2f}%")
            if rsi_val > 45 and rsi_val < 65:
                confidence += 0.20
                reasons.append(f"RSI={rsi_val:.1f} mid-range (room to fall)")
            elif rsi_val >= 65:
                confidence += 0.30
                reasons.append(f"RSI={rsi_val:.1f} overbought in downtrend")
            if bb_upper > 0 and price > bb_middle:
                confidence += 0.15
                reasons.append("above BB middle")
            if assessment.adx > 25:
                confidence += 0.10
                reasons.append(f"strong trend ADX={assessment.adx:.0f}")
        else:
            if velocity > 0.2:
                confidence += 0.35
                reasons.append(f"vel={velocity:.2f}%")
            if rsi_val < 55 and rsi_val > 35:
                confidence += 0.20
                reasons.append(f"RSI={rsi_val:.1f} mid-range (room to rise)")
            elif rsi_val <= 35:
                confidence += 0.30
                reasons.append(f"RSI={rsi_val:.1f} oversold in uptrend")
            if bb_lower > 0 and price < bb_middle:
                confidence += 0.15
                reasons.append("below BB middle")
            if assessment.adx > 25:
                confidence += 0.10
                reasons.append(f"strong trend ADX={assessment.adx:.0f}")

        confidence *= assessment.confidence

        if confidence < self.MIN_CONFIDENCE:
            return self._no_signal(price, f"trend_{direction}_weak_{confidence:.2f}")

        if direction == "short":
            return TradeSignal(
                type=SignalType.SHORT, asset=self.asset, confidence=confidence,
                entry_price=price,
                stop_loss=price * (1 + self.MOM_SL_PCT),
                take_profit=price * (1 - self.MOM_TP_PCT),
                regime=assessment.regime.value, trade_type="trend_follow",
                reason=", ".join(reasons), timestamp=time.time(),
                indicators={"rsi_5m": rsi_val, "velocity": velocity},
            )
        else:
            return TradeSignal(
                type=SignalType.LONG, asset=self.asset, confidence=confidence,
                entry_price=price,
                stop_loss=price * (1 - self.MOM_SL_PCT),
                take_profit=price * (1 + self.MOM_TP_PCT),
                regime=assessment.regime.value, trade_type="trend_follow",
                reason=", ".join(reasons), timestamp=time.time(),
                indicators={"rsi_5m": rsi_val, "velocity": velocity},
            )

    def _mean_reversion_signal(self, price: float, regime: MarketRegime, assessment,
                               last_candle_green: bool = True, last_candle_red: bool = True) -> TradeSignal:
        aggressive = regime == MarketRegime.VOLATILE_RANGING
        tp = self.AMR_TP_PCT if aggressive else self.MR_TP_PCT
        sl = self.AMR_SL_PCT if aggressive else self.MR_SL_PCT

        score = self._score_entry(price, last_candle_green, last_candle_red)
        if not score:
            return self._no_signal(price, "no_scoring_data")

        direction = score["direction"]
        if not direction:
            return self._no_signal(price, f"no_signal (long={score['long_score']:.0f} short={score['short_score']:.0f} need 5)")

        confidence = min(score["score"] / 8, 0.95) * assessment.confidence
        if confidence < self.MIN_CONFIDENCE:
            return self._no_signal(price, f"score={score['score']:.0f}/8 conf={confidence:.2f} {score['reasons']}")

        if direction == "long":
            return TradeSignal(
                type=SignalType.LONG, asset=self.asset, confidence=confidence,
                entry_price=price, stop_loss=price * (1 - sl), take_profit=price * (1 + tp),
                regime=regime.value, trade_type="mean_reversion",
                reason=score["reasons"], timestamp=time.time(),
                indicators=score["indicators"],
            )
        else:
            return TradeSignal(
                type=SignalType.SHORT, asset=self.asset, confidence=confidence,
                entry_price=price, stop_loss=price * (1 + sl), take_profit=price * (1 - tp),
                regime=regime.value, trade_type="mean_reversion",
                reason=score["reasons"], timestamp=time.time(),
                indicators=score["indicators"],
            )

    def _momentum_signal(self, price: float, direction: str, assessment) -> TradeSignal:
        closes_15m = self.candles.get_closes(Timeframe.M15, 30)
        if len(closes_15m) < 21:
            return self._no_signal(price, "insufficient_15m_data")

        rsi_val = ind.rsi(closes_15m)
        ema_9 = ind.ema(closes_15m, 9)
        ema_21 = ind.ema(closes_15m, 21)
        velocity = ind.price_velocity(closes_15m, 3)

        signal = None
        reasons = []
        confidence = 0.0

        if direction == "long":
            near_ema21 = abs(price - ema_21) / ema_21 < 0.01 if ema_21 > 0 else False
            rsi_bouncing = 40 < rsi_val < 55

            if near_ema21 and rsi_bouncing:
                signal = SignalType.LONG
                confidence = 0.75
                reasons = [f"pullback to EMA21 (${ema_21:.2f})", f"RSI={rsi_val:.1f} bouncing"]

            elif velocity > self.MOM_VELOCITY_MIN and assessment.adx > self.regime_detector.ADX_TREND_THRESHOLD:
                closes_1h = self.candles.get_closes(Timeframe.H1, 5)
                if closes_1h and price > max(closes_1h):
                    signal = SignalType.LONG
                    confidence = 0.7
                    reasons = [f"breakout above 1h high", f"velocity={velocity:.2f}%"]

        else:
            near_ema21 = abs(price - ema_21) / ema_21 < 0.01 if ema_21 > 0 else False
            rsi_failing = 45 < rsi_val < 60

            if near_ema21 and rsi_failing:
                signal = SignalType.SHORT
                confidence = 0.75
                reasons = [f"rally to EMA21 (${ema_21:.2f})", f"RSI={rsi_val:.1f} failing"]

            elif velocity < -self.MOM_VELOCITY_MIN and assessment.adx > self.regime_detector.ADX_TREND_THRESHOLD:
                closes_1h = self.candles.get_closes(Timeframe.H1, 5)
                if closes_1h and price < min(closes_1h):
                    signal = SignalType.SHORT
                    confidence = 0.7
                    reasons = [f"breakdown below 1h low", f"velocity={velocity:.2f}%"]

        if signal is None:
            return self._no_signal(price, f"no_momentum_{direction}")

        confidence *= assessment.confidence
        if confidence < self.MIN_CONFIDENCE:
            return self._no_signal(price, f"confidence_too_low_{confidence:.2f}")

        if signal == SignalType.LONG:
            sl_price = price * (1 - self.MOM_SL_PCT)
            if ema_21 > 0:
                sl_price = max(sl_price, ema_21 * 0.995)
            return TradeSignal(
                type=signal, asset=self.asset, confidence=confidence,
                entry_price=price, stop_loss=sl_price, take_profit=price * (1 + self.MOM_TP_PCT),
                regime=assessment.regime.value, trade_type="momentum",
                reason=", ".join(reasons), timestamp=time.time(),
                indicators={"rsi_15m": rsi_val, "ema_9": ema_9, "ema_21": ema_21, "velocity": velocity},
            )
        else:
            sl_price = price * (1 + self.MOM_SL_PCT)
            if ema_21 > 0:
                sl_price = min(sl_price, ema_21 * 1.005)
            return TradeSignal(
                type=signal, asset=self.asset, confidence=confidence,
                entry_price=price, stop_loss=sl_price, take_profit=price * (1 - self.MOM_TP_PCT),
                regime=assessment.regime.value, trade_type="momentum",
                reason=", ".join(reasons), timestamp=time.time(),
                indicators={"rsi_15m": rsi_val, "ema_9": ema_9, "ema_21": ema_21, "velocity": velocity},
            )

    def check_exits(self, position: dict, current_price: float) -> TradeSignal | None:
        now = time.time()
        direction = position["direction"]
        entry = position["entry_price"]
        sl = position["stop_loss"]
        tp = position["take_profit"]
        opened_at = position["opened_at"]
        trade_type = position["trade_type"]
        peak = position.get("peak_price", entry)

        if direction == "long" and current_price <= sl:
            return self._exit_signal(current_price, SignalType.CLOSE_LONG, "stop_loss_hit")
        if direction == "short" and current_price >= sl:
            return self._exit_signal(current_price, SignalType.CLOSE_SHORT, "stop_loss_hit")

        if direction == "long" and current_price >= tp:
            return self._exit_signal(current_price, SignalType.CLOSE_LONG, "take_profit_hit")
        if direction == "short" and current_price <= tp:
            return self._exit_signal(current_price, SignalType.CLOSE_SHORT, "take_profit_hit")

        if trade_type in ("momentum", "trend_follow"):
            if direction == "long" and peak > entry:
                trail_stop = peak * (1 - self.MOM_TRAILING_STOP_PCT)
                if current_price <= trail_stop:
                    return self._exit_signal(current_price, SignalType.CLOSE_LONG, f"trailing_stop (peak=${peak:.2f})")
            if direction == "short" and peak < entry:
                trail_stop = peak * (1 + self.MOM_TRAILING_STOP_PCT)
                if current_price >= trail_stop:
                    return self._exit_signal(current_price, SignalType.CLOSE_SHORT, f"trailing_stop (trough=${peak:.2f})")

        assessment = self.regime_detector._last_assessment
        if assessment:
            regime = assessment.regime
            if regime == MarketRegime.DEAD:
                close_type = SignalType.CLOSE_LONG if direction == "long" else SignalType.CLOSE_SHORT
                return self._exit_signal(current_price, close_type, "regime_changed_to_dead")
            if direction == "long" and regime == MarketRegime.TRENDING_DOWN and trade_type == "mean_reversion":
                return self._exit_signal(current_price, SignalType.CLOSE_LONG, "regime_now_trending_down")
            if direction == "short" and regime == MarketRegime.TRENDING_UP and trade_type == "mean_reversion":
                return self._exit_signal(current_price, SignalType.CLOSE_SHORT, "regime_now_trending_up")

        age = now - opened_at
        if trade_type == "trend_follow":
            max_hold = self.TREND_MAX_HOLD
        elif trade_type == "mean_reversion":
            max_hold = self.AMR_MAX_HOLD if self.regime_detector.regime == MarketRegime.VOLATILE_RANGING else self.MR_MAX_HOLD
        else:
            max_hold = self.MOM_MAX_HOLD
        if age > max_hold:
            close_type = SignalType.CLOSE_LONG if direction == "long" else SignalType.CLOSE_SHORT
            return self._exit_signal(current_price, close_type, f"time_exit ({age/60:.0f}min)")

        return None

    def update_trailing_stop(self, position: dict, current_price: float) -> float | None:
        if position["trade_type"] not in ("momentum", "trend_follow"):
            return None
        direction = position["direction"]
        peak = position.get("peak_price", position["entry_price"])

        if direction == "long" and current_price > peak:
            new_sl = current_price * (1 - self.MOM_TRAILING_STOP_PCT)
            if new_sl > position["stop_loss"]:
                return new_sl
        if direction == "short" and current_price < peak:
            new_sl = current_price * (1 + self.MOM_TRAILING_STOP_PCT)
            if new_sl < position["stop_loss"]:
                return new_sl
        return None

    def record_close(self, was_loss: bool):
        self._last_close_time = time.time()
        self._last_close_was_loss = was_loss

    def record_outcome(self, signal: TradeSignal, exit_price: float, pnl_pct: float):
        self._signal_history.append({
            "type": signal.type.value,
            "regime": signal.regime,
            "trade_type": signal.trade_type,
            "entry": signal.entry_price,
            "exit": exit_price,
            "pnl_pct": pnl_pct,
            "confidence": signal.confidence,
            "timestamp": signal.timestamp,
        })
        if len(self._signal_history) > 1000:
            self._signal_history = self._signal_history[-1000:]

    def get_performance_stats(self) -> dict:
        if not self._signal_history:
            return {"total_signals": 0, "win_rate": 0, "profit_factor": 0}

        wins = [s for s in self._signal_history if s["pnl_pct"] > 0]
        losses = [s for s in self._signal_history if s["pnl_pct"] <= 0]
        total = len(self._signal_history)

        gross_wins = sum(s["pnl_pct"] for s in wins)
        gross_losses = abs(sum(s["pnl_pct"] for s in losses))

        by_regime = {}
        for s in self._signal_history:
            r = s["regime"]
            if r not in by_regime:
                by_regime[r] = {"count": 0, "wins": 0, "total_pnl": 0}
            by_regime[r]["count"] += 1
            by_regime[r]["total_pnl"] += s["pnl_pct"]
            if s["pnl_pct"] > 0:
                by_regime[r]["wins"] += 1
        for r in by_regime:
            c = by_regime[r]["count"]
            by_regime[r]["win_rate"] = by_regime[r]["wins"] / c if c > 0 else 0
            by_regime[r]["avg_pnl"] = by_regime[r]["total_pnl"] / c if c > 0 else 0

        return {
            "total_signals": total,
            "win_rate": len(wins) / total if total > 0 else 0,
            "avg_win_pct": gross_wins / len(wins) if wins else 0,
            "avg_loss_pct": gross_losses / len(losses) if losses else 0,
            "profit_factor": gross_wins / gross_losses if gross_losses > 0 else 999,
            "by_regime": by_regime,
        }

    def get_indicator_snapshot(self) -> dict:
        closes_5m = self.candles.get_closes(Timeframe.M5, 30)
        closes_15m = self.candles.get_closes(Timeframe.M15, 30)
        highs_1h = self.candles.get_highs(Timeframe.H1, 30)
        lows_1h = self.candles.get_lows(Timeframe.H1, 30)
        closes_1h = self.candles.get_closes(Timeframe.H1, 30)

        bb_l, bb_m, bb_u = ind.bollinger_bands(closes_5m)
        adx_val, plus_di, minus_di = ind.adx_with_di(highs_1h, lows_1h, closes_1h)

        return {
            "rsi_5m": ind.rsi(closes_5m),
            "rsi_15m": ind.rsi(closes_15m),
            "bb_lower_5m": bb_l,
            "bb_middle_5m": bb_m,
            "bb_upper_5m": bb_u,
            "bb_width_1h": ind.bollinger_band_width(closes_1h),
            "ema_9_15m": ind.ema(closes_15m, 9),
            "ema_21_15m": ind.ema(closes_15m, 21),
            "adx_1h": adx_val,
            "plus_di_1h": plus_di,
            "minus_di_1h": minus_di,
            "atr_1h": ind.atr(highs_1h, lows_1h, closes_1h),
            "vwap": ind.vwap_from_candles(self.candles.get_candles(Timeframe.M5, 30)),
            "velocity_5m": ind.price_velocity(closes_5m),
            "acceleration_5m": ind.price_acceleration(closes_5m),
            "regime": self.regime_detector.regime.value,
            "regime_confidence": self.regime_detector.confidence,
        }

    def _no_signal(self, price: float, reason: str = "") -> TradeSignal:
        return TradeSignal(
            type=SignalType.NO_SIGNAL, asset=self.asset, confidence=0,
            entry_price=price, stop_loss=0, take_profit=0,
            regime=self.regime_detector.regime.value, trade_type="",
            reason=reason, timestamp=time.time(),
        )

    def _exit_signal(self, price: float, signal_type: SignalType, reason: str) -> TradeSignal:
        return TradeSignal(
            type=signal_type, asset=self.asset, confidence=1.0,
            entry_price=price, stop_loss=0, take_profit=0,
            regime=self.regime_detector.regime.value, trade_type="exit",
            reason=reason, timestamp=time.time(),
        )

    def _snapshot(self, rsi_val, bb_lower, bb_middle, bb_upper, vwap_val) -> dict:
        return {
            "rsi_5m": rsi_val,
            "bb_lower": bb_lower, "bb_middle": bb_middle, "bb_upper": bb_upper,
            "vwap": vwap_val,
        }

    def get_state(self) -> dict:
        return {
            "candles": self.candles.get_state(),
            "regime": self.regime_detector.get_state(),
            "signal_history": self._signal_history[-200:],
            "last_close_time": self._last_close_time,
            "last_close_was_loss": self._last_close_was_loss,
        }

    def load_state(self, state: dict):
        if "candles" in state:
            self.candles.load_state(state["candles"])
        if "regime" in state:
            self.regime_detector.load_state(state["regime"])
        self._signal_history = state.get("signal_history", [])
        self._last_close_time = state.get("last_close_time", 0)
        self._last_close_was_loss = state.get("last_close_was_loss", False)
