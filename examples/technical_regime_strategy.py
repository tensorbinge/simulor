"""Technical regime strategy example.

This example intentionally uses a more realistic authoring style than the
simple moving-average examples. It combines:

- regime gating across the universe
- momentum and breakout confirmation
- volatility-aware sizing
- portfolio drawdown protection
- rebalance thresholds via the execution model

The goal is to show what writing a non-trivial technical strategy looks like
with the current public API.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from math import sqrt
from pathlib import Path

from simulor.alpha.signal import Signal, SignalType
from simulor.core.events import MarketEvent
from simulor.core.models import AlphaModel, PortfolioConstructionModel, RiskModel
from simulor.data.csv_feed import CsvFeed
from simulor.engine import Engine
from simulor.execution import Immediate
from simulor.execution.simulation.broker import SimulatedBroker
from simulor.portfolio import Fund
from simulor.strategy import Strategy
from simulor.types import Instrument, Resolution
from simulor.universe import Static

logger = logging.getLogger(__name__)

UNIVERSE = [
    Instrument.stock("AAPL"),
    Instrument.stock("MSFT"),
    Instrument.stock("GOOGL"),
]


class RegimeMomentumBreakoutAlpha(AlphaModel):
    """Generate ranked long signals from trend, momentum, and breakout filters."""

    def __init__(
        self,
        momentum_lookback: int = 40,
        trend_window: int = 20,
        baseline_window: int = 60,
        breakout_window: int = 20,
        volatility_window: int = 20,
        max_candidates: int = 2,
    ) -> None:
        self.momentum_lookback = momentum_lookback
        self.trend_window = trend_window
        self.baseline_window = baseline_window
        self.breakout_window = breakout_window
        self.volatility_window = volatility_window
        self.max_candidates = max_candidates

    def generate_signals(self, market_event: MarketEvent) -> dict[Instrument, Signal]:
        instruments = sorted(market_event.instruments(), key=lambda instrument: instrument.symbol)
        if not instruments:
            return {}

        if not self._is_risk_on(instruments):
            logger.debug("Regime filter is risk-off at %s", market_event.time)
            return {}

        ranked: list[tuple[Instrument, Decimal, Decimal, Decimal, Decimal]] = []

        for instrument in instruments:
            closes = self._closes(instrument)
            if len(closes) < self.baseline_window + 1:
                continue

            close_now = closes[-1]
            if close_now <= 0:
                continue

            momentum = (close_now / closes[-self.momentum_lookback]) - Decimal("1")
            fast_trend = self._mean(closes[-self.trend_window :])
            slow_trend = self._mean(closes[-self.baseline_window :])
            trend_gap = (fast_trend / slow_trend) - Decimal("1")
            breakout_level = max(closes[-self.breakout_window : -1])
            breakout = (close_now / breakout_level) - Decimal("1") if breakout_level > 0 else Decimal("0")
            annualized_volatility = self._annualized_volatility(closes[-(self.volatility_window + 1) :])

            if momentum <= 0 or trend_gap <= 0 or breakout < Decimal("-0.01"):
                continue
            if annualized_volatility <= Decimal("0") or annualized_volatility > Decimal("0.60"):
                continue

            score = (momentum * Decimal("0.45")) + (trend_gap * Decimal("0.35")) + (breakout * Decimal("0.20"))
            ranked.append((instrument, score, momentum, breakout, annualized_volatility))

        ranked.sort(key=lambda item: item[1], reverse=True)
        leaders = ranked[: self.max_candidates]
        if not leaders:
            return {}

        best_score = leaders[0][1]
        scale = best_score if best_score > 0 else Decimal("1")
        signals: dict[Instrument, Signal] = {}

        for instrument, score, momentum, breakout, annualized_volatility in leaders:
            normalized_strength = min(Decimal("1"), max(Decimal("0.1"), score / scale))
            confidence = min(
                Decimal("1"),
                max(
                    Decimal("0.2"),
                    Decimal("0.55")
                    + min(momentum, Decimal("0.20"))
                    + min(max(breakout, Decimal("0")), Decimal("0.10")),
                ),
            )
            signals[instrument] = Signal(
                instrument=instrument,
                timestamp=market_event.time,
                signal_type=SignalType.TECHNICAL_INDICATOR,
                source_id=self.__class__.__name__,
                strength=normalized_strength,
                confidence=confidence,
                metadata={
                    "momentum": momentum,
                    "breakout": breakout,
                    "annualized_volatility": annualized_volatility,
                    "score": score,
                },
            )

        return signals

    def _is_risk_on(self, instruments: list[Instrument]) -> bool:
        strong_trends = 0
        eligible = 0
        for instrument in instruments:
            closes = self._closes(instrument)
            if len(closes) < self.baseline_window:
                continue
            eligible += 1
            fast_trend = self._mean(closes[-self.trend_window :])
            slow_trend = self._mean(closes[-self.baseline_window :])
            if fast_trend > slow_trend:
                strong_trends += 1
        return eligible > 0 and strong_trends / eligible >= 0.5

    def _closes(self, instrument: Instrument) -> list[Decimal]:
        return [bar.close for bar in self.market_store.get_trade_bars(instrument, Resolution.DAILY)]

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal:
        return sum(values) / Decimal(len(values))

    @staticmethod
    def _annualized_volatility(closes: list[Decimal]) -> Decimal:
        if len(closes) < 2:
            return Decimal("0")

        returns: list[float] = []
        for previous, current in zip(closes, closes[1:], strict=False):
            if previous <= 0:
                continue
            returns.append(float((current / previous) - Decimal("1")))

        if len(returns) < 2:
            return Decimal("0")

        mean_return = sum(returns) / len(returns)
        variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
        return Decimal(str(sqrt(variance) * sqrt(252)))


class VolatilityScaledTopK(PortfolioConstructionModel):
    """Size the strongest signals using inverse volatility weights."""

    def __init__(self, target_gross_exposure: Decimal = Decimal("0.50"), max_positions: int = 2) -> None:
        self.target_gross_exposure = target_gross_exposure
        self.max_positions = max_positions

    def calculate_targets(self, signals: dict[Instrument, Signal]) -> dict[Instrument, Decimal]:
        if not signals:
            return {}

        ranked = sorted(
            (signal for signal in signals.values() if signal.strength > 0),
            key=lambda signal: signal.weighted_strength,
            reverse=True,
        )[: self.max_positions]
        if not ranked:
            return {}

        inverse_vols: dict[Instrument, Decimal] = {}
        for signal in ranked:
            annualized_volatility = signal.metadata.get("annualized_volatility", Decimal("0.25"))
            if not isinstance(annualized_volatility, Decimal) or annualized_volatility <= 0:
                annualized_volatility = Decimal("0.25")
            inverse_vols[signal.instrument] = Decimal("1") / annualized_volatility

        total_inverse_vol = sum(inverse_vols.values())
        portfolio_value = self.portfolio.total_value
        targets: dict[Instrument, Decimal] = {}

        for signal in ranked:
            instrument = signal.instrument
            current_price = self.market_store.get_latest_price(instrument)
            if current_price <= 0:
                continue

            target_weight = (inverse_vols[instrument] / total_inverse_vol) * self.target_gross_exposure
            target_notional = portfolio_value * target_weight
            targets[instrument] = target_notional // current_price

        return targets


class DrawdownCappedRisk(RiskModel):
    """Flatten on portfolio stress and cap individual position concentration."""

    def __init__(self, max_position_pct: Decimal = Decimal("0.35"), max_drawdown: Decimal = Decimal("0.12")) -> None:
        self.max_position_pct = max_position_pct
        self.max_drawdown = max_drawdown

    def apply_limits(self, targets: dict[Instrument, Decimal]) -> dict[Instrument, Decimal]:
        if not targets:
            return {}

        if self._current_drawdown() >= self.max_drawdown:
            logger.warning("Drawdown limit breached, flattening portfolio targets")
            return {}

        total_value = self.portfolio.total_value
        if total_value <= 0:
            return {}

        adjusted: dict[Instrument, Decimal] = {}
        max_notional = total_value * self.max_position_pct

        for instrument, target_quantity in targets.items():
            current_price = self.market_store.get_latest_price(instrument)
            if current_price <= 0:
                continue

            capped_quantity = target_quantity
            position_notional = abs(target_quantity * current_price)
            if position_notional > max_notional:
                capped_quantity = max_notional // current_price

            adjusted[instrument] = capped_quantity

        return adjusted

    def _current_drawdown(self) -> Decimal:
        equity_series = self.portfolio.recorder.get_equity_series()
        if not equity_series:
            return Decimal("0")

        peak = max(equity for _, equity in equity_series)
        current = equity_series[-1][1]
        if peak <= 0:
            return Decimal("0")
        return (peak - current) / peak


def build_strategy() -> Strategy:
    return Strategy(
        name="RegimeMomentumBreakout",
        universe=Static(UNIVERSE),
        alpha=RegimeMomentumBreakoutAlpha(),
        construction=VolatilityScaledTopK(target_gross_exposure=Decimal("0.50"), max_positions=2),
        risk=DrawdownCappedRisk(max_position_pct=Decimal("0.35"), max_drawdown=Decimal("0.12")),
        execution=Immediate(min_notional=Decimal("1000"), min_pct_change=Decimal("0.10")),
    )


def build_engine() -> Engine:
    data_path = Path(__file__).resolve().parent / "data" / "daily_trade_bars.csv"
    return Engine(
        data=CsvFeed(path=data_path, resolution=Resolution.DAILY),
        fund=Fund(strategies=[build_strategy()], capital=Decimal("100000")),
        broker=SimulatedBroker(),
    )


def main() -> None:
    from simulor.analytics import Tearsheet

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    engine = build_engine()
    result = engine.run(start="2020-01-02 00:00:00", end="2020-12-31 23:59:59", mode="backtest")

    print(result.summary())

    output_path = Path(__file__).resolve().parent / "reports" / "technical_regime_strategy_tearsheet.html"
    Tearsheet(result).save(output_path)
    print(f"Tearsheet written to {output_path}")


if __name__ == "__main__":
    main()
