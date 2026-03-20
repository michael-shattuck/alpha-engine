import math


class MarketSignals:

    VOLATILITY_THRESHOLDS = {
        "low": 2.0,
        "medium": 5.0,
        "high": 10.0,
    }

    RANGE_BY_VOLATILITY = {
        "low": 0.02,
        "medium": 0.04,
        "high": 0.08,
        "extreme": 0.15,
    }

    def analyze(self, market_data: dict) -> dict:
        trend = self.trend_signal(market_data)
        volatility = self.volatility_signal(market_data)
        volume = self.volume_signal(market_data)
        score = self.risk_score(market_data)
        lp_range = self.recommended_range(market_data)
        allocation = self.recommended_allocation(market_data)

        return {
            "trend": trend,
            "volatility": volatility,
            "volume": volume,
            "risk_score": score,
            "risk_level": self._score_to_level(score),
            "recommended_range_percent": lp_range * 100,
            "recommended_allocation": allocation,
            "price_1h_change": market_data.get("price_change_1h", 0.0),
            "price_24h_change": market_data.get("price_change_24h", 0.0),
        }

    def trend_signal(self, market_data: dict) -> str:
        change_1h = market_data.get("price_change_1h", 0.0)
        change_24h = market_data.get("price_change_24h", 0.0)

        weighted_trend = change_1h * 0.6 + change_24h * 0.4

        if weighted_trend > 1.0:
            return "bullish"
        elif weighted_trend < -1.0:
            return "bearish"
        return "neutral"

    def volatility_signal(self, market_data: dict) -> str:
        volatility = abs(market_data.get("volatility_24h", 0.0))

        if volatility < self.VOLATILITY_THRESHOLDS["low"]:
            return "low"
        elif volatility < self.VOLATILITY_THRESHOLDS["medium"]:
            return "medium"
        elif volatility < self.VOLATILITY_THRESHOLDS["high"]:
            return "high"
        return "extreme"

    def volume_signal(self, market_data: dict) -> str:
        pool_apy = market_data.get("pool_apy", 0.0)
        base_apy = market_data.get("base_apy", 10.0)

        apy_ratio = pool_apy / base_apy if base_apy > 0 else 0.0

        if apy_ratio < 0.5:
            return "low"
        elif apy_ratio < 1.5:
            return "normal"
        return "high"

    def risk_score(self, market_data: dict) -> float:
        volatility = abs(market_data.get("volatility_24h", 0.0))
        volatility_component = min(volatility / 15.0, 1.0) * 40.0

        change_1h = market_data.get("price_change_1h", 0.0)
        change_24h = market_data.get("price_change_24h", 0.0)
        trend_strength = math.sqrt(change_1h ** 2 + change_24h ** 2)
        trend_component = min(trend_strength / 20.0, 1.0) * 30.0

        acceleration = abs(change_1h * 24 - change_24h)
        acceleration_component = min(acceleration / 30.0, 1.0) * 30.0

        return min(volatility_component + trend_component + acceleration_component, 100.0)

    def recommended_range(self, market_data: dict) -> float:
        volatility = self.volatility_signal(market_data)
        return self.RANGE_BY_VOLATILITY[volatility]

    def recommended_allocation(self, market_data: dict) -> dict:
        trend = self.trend_signal(market_data)
        volatility = self.volatility_signal(market_data)

        allocation = {
            "tight_range_lp": 0.35,
            "jlp": 0.25,
            "fee_compounder": 0.0,
            "multi_pool": 0.25,
            "volatile_pairs": 0.15,
        }

        if trend == "bearish" and volatility in ("high", "extreme"):
            allocation["volatile_pairs"] = 0.05
            allocation["tight_range_lp"] = 0.15
            allocation["jlp"] = 0.45
            allocation["multi_pool"] = 0.35
            allocation["fee_compounder"] = 0.0

        elif trend == "bearish" and volatility in ("low", "medium"):
            allocation["volatile_pairs"] = 0.10
            allocation["tight_range_lp"] = 0.25
            allocation["jlp"] = 0.35
            allocation["multi_pool"] = 0.30
            allocation["fee_compounder"] = 0.0

        elif trend == "bullish" and volatility in ("low", "medium"):
            allocation["tight_range_lp"] = 0.45
            allocation["jlp"] = 0.15
            allocation["multi_pool"] = 0.20
            allocation["volatile_pairs"] = 0.20
            allocation["fee_compounder"] = 0.0

        elif trend == "bullish" and volatility in ("high", "extreme"):
            allocation["tight_range_lp"] = 0.20
            allocation["jlp"] = 0.30
            allocation["multi_pool"] = 0.25
            allocation["volatile_pairs"] = 0.25
            allocation["fee_compounder"] = 0.0

        elif volatility == "extreme":
            allocation["tight_range_lp"] = 0.10
            allocation["jlp"] = 0.50
            allocation["multi_pool"] = 0.30
            allocation["volatile_pairs"] = 0.05
            allocation["fee_compounder"] = 0.05

        return allocation

    def _score_to_level(self, score: float) -> str:
        if score < 25:
            return "low"
        elif score < 50:
            return "medium"
        elif score < 75:
            return "high"
        return "critical"
