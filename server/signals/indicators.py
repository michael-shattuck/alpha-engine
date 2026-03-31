import math


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0) for c in changes]
    losses = [abs(min(c, 0)) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_series(closes: list[float], period: int = 14) -> list[float]:
    result = [50.0] * len(closes)
    if len(closes) < period + 1:
        return result

    changes = [0.0] + [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0) for c in changes]
    losses = [abs(min(c, 0)) for c in changes]

    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    if avg_loss > 0:
        result[period] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    elif avg_gain > 0:
        result[period] = 100.0

    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            result[i] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    return result


def bollinger_bands(
    closes: list[float], period: int = 20, std_dev: float = 2.0
) -> tuple[float, float, float]:
    if len(closes) < period:
        return (0.0, 0.0, 0.0)
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((p - middle) ** 2 for p in window) / period
    std = math.sqrt(variance)
    return (middle - std_dev * std, middle, middle + std_dev * std)


def bollinger_band_width(
    closes: list[float], period: int = 20, std_dev: float = 2.0
) -> float:
    lower, middle, upper = bollinger_bands(closes, period, std_dev)
    if middle <= 0:
        return 0.0
    return (upper - lower) / middle


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    multiplier = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = v * multiplier + result * (1.0 - multiplier)
    return result


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    result = [0.0] * len(values)
    if len(values) < period:
        running = 0.0
        for i, v in enumerate(values):
            running += v
            result[i] = running / (i + 1)
        return result

    sma = sum(values[:period]) / period
    result[period - 1] = sma
    multiplier = 2.0 / (period + 1)
    prev = sma
    for i in range(period, len(values)):
        prev = values[i] * multiplier + prev * (1.0 - multiplier)
        result[i] = prev
    return result


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


def vwap(
    highs: list[float], lows: list[float], closes: list[float], volumes: list[float]
) -> float:
    if not closes:
        return 0.0
    total_vp = 0.0
    total_v = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        tp = (h + l + c) / 3.0
        vol = v if v > 0 else 1.0
        total_vp += tp * vol
        total_v += vol
    return total_vp / total_v if total_v > 0 else 0.0


def vwap_from_candles(candles: list) -> float:
    if not candles:
        return 0.0
    total_vp = 0.0
    total_v = 0.0
    for c in candles:
        tp = (c.high + c.low + c.close) / 3.0
        vol = c.volume if c.volume > 0 else 1.0
        total_vp += tp * vol
        total_v += vol
    return total_vp / total_v if total_v > 0 else 0.0


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float:
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return 0.0

    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return sum(trs) / len(trs)

    result = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        result = (result * (period - 1) + trs[i]) / period
    return result


def adx(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float:
    val, _, _ = adx_with_di(highs, lows, closes, period)
    return val


def adx_with_di(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> tuple[float, float, float]:
    n = min(len(highs), len(lows), len(closes))
    if n < period * 2 + 1:
        return (0.0, 0.0, 0.0)

    plus_dm_list = []
    minus_dm_list = []
    tr_list = []

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    smooth_plus_dm = sum(plus_dm_list[:period])
    smooth_minus_dm = sum(minus_dm_list[:period])
    smooth_tr = sum(tr_list[:period])

    dx_values = []

    for i in range(period, len(plus_dm_list)):
        smooth_plus_dm = smooth_plus_dm - smooth_plus_dm / period + plus_dm_list[i]
        smooth_minus_dm = smooth_minus_dm - smooth_minus_dm / period + minus_dm_list[i]
        smooth_tr = smooth_tr - smooth_tr / period + tr_list[i]

        if smooth_tr > 0:
            plus_di = 100.0 * smooth_plus_dm / smooth_tr
            minus_di = 100.0 * smooth_minus_dm / smooth_tr
        else:
            plus_di = 0.0
            minus_di = 0.0

        di_sum = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
        dx_values.append((dx, plus_di, minus_di))

    if len(dx_values) < period:
        if dx_values:
            last = dx_values[-1]
            avg_dx = sum(d[0] for d in dx_values) / len(dx_values)
            return (avg_dx, last[1], last[2])
        return (0.0, 0.0, 0.0)

    adx_val = sum(d[0] for d in dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx_val = (adx_val * (period - 1) + dx_values[i][0]) / period

    last_di = dx_values[-1]
    return (adx_val, last_di[1], last_di[2])


def price_velocity(closes: list[float], period: int = 5) -> float:
    if len(closes) < period + 1:
        return 0.0
    if closes[-period - 1] == 0:
        return 0.0
    return (closes[-1] - closes[-period - 1]) / closes[-period - 1] * 100.0


def price_acceleration(closes: list[float], period: int = 5) -> float:
    if len(closes) < period * 2 + 1:
        return 0.0
    v_now = price_velocity(closes, period)
    v_prev = price_velocity(closes[:-period], period)
    return v_now - v_prev
