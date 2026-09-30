"""Buy and Hold Strategy Example.

This example demonstrates a simple buy-and-hold strategy that purchases
equal-weighted positions in AAPL, MSFT, and GOOGL at the start and holds them.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from simulor.alpha.signal import Signal, SignalDirection, SignalType
from simulor.analytics import Tearsheet
from simulor.core.events import MarketEvent
from simulor.core.models import AlphaModel
from simulor.data.csv_feed import CsvFeed
from simulor.engine import Engine
from simulor.execution import Immediate
from simulor.execution.simulation.broker import SimulatedBroker
from simulor.portfolio import EqualWeight, Fund
from simulor.risk import PositionLimit
from simulor.strategy import Strategy
from simulor.types import Instrument, Resolution, Target
from simulor.universe import Static


class BuyAndHoldAlphaModel(AlphaModel):
    """Buy and hold alpha model.

    Generates strong buy signals only on the first day for each instrument,
    then holds the positions indefinitely.
    """

    def generate_signals(self, market_event: MarketEvent) -> list[Signal]:
        """Generate buy signal only on first occurrence of each instrument.

        Args:
            market_event: Current market data event
        Returns:
            List with one buy signal per instrument in the event
        """

        # Always generate buy signals
        return [
            Signal(
                instrument=instrument,
                timestamp=market_event.time,
                direction=SignalDirection.LONG,
                signal_type=SignalType.TECHNICAL_INDICATOR,
                source_id=self.__class__.__name__,
                strength=Decimal("1.0"),  # Maximum buy signal
                confidence=Decimal("1.0"),  # High confidence
            )
            for instrument in market_event.instruments()
        ]


class BuyAndHoldPortfolioConstructionModel(EqualWeight):
    """Equal weight portfolio construction with 5% reserve."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize equal weight model with reserve percentage.

        Args:
            reserve_pct: Percentage of capital to reserve (not invest)
        """
        super().__init__(*args, **kwargs)
        self._initialized_instruments: set[Instrument] = set()

    def create_targets(self, signals: list[Signal]) -> list[Target]:
        uninitialized = [signal for signal in signals if signal.instrument not in self._initialized_instruments]
        self._initialized_instruments.update(signal.instrument for signal in uninitialized)
        return super().create_targets(uninitialized)


def main() -> None:
    """Run a buy and hold strategy."""
    # Configure logging to see important events during backtest
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            # Console handler - shows logs in terminal
            logging.StreamHandler(),
            # File handler - saves logs to file
            logging.FileHandler(f"examples/logs/simulor.{datetime.now().strftime('%Y%m%d%H%M%S')}.log", mode="w"),
        ],
    )
    logger = logging.getLogger(__name__)

    # Create the strategy (capital allocated by Fund)
    strategy = Strategy(
        name="BuyAndHold",
        # Define the universe
        universe=Static(
            [
                Instrument.stock(symbol="MSFT"),
                Instrument.stock(symbol="AAPL"),
                Instrument.stock(symbol="GOOGL"),
            ]
        ),
        # Define the alpha model (buy and hold)
        alpha=BuyAndHoldAlphaModel(),
        # Define portfolio construction (equal weight)
        construction=BuyAndHoldPortfolioConstructionModel(reserve_pct=Decimal("0.05")),
        # Define risk management (max 40% per position)
        risk=PositionLimit(max_position=Decimal("0.40")),
        # Define execution model (immediate execution)
        execution=Immediate(),
    )

    # Create and run engine
    engine = Engine(
        # Load data
        data=CsvFeed(path=Path("examples/data/daily_trade_bars.csv"), resolution=Resolution.DAILY),
        # Create fund with initial capital
        fund=Fund(strategies=[strategy], capital=Decimal("100000")),
        broker=SimulatedBroker(),
    )

    result = engine.run(start="2024-01-01 00:00:00", end="2024-12-31 23:59:59", mode="backtest")

    # Display comprehensive analytics
    logger.info(result.summary())

    # Generate tearsheet and charts
    tearsheet = Tearsheet(result)
    tearsheet.save("examples/reports/buy_and_hold_tearsheet.html")

    # View individual charts
    # result.plot_equity_curve().show()
    # result.plot_drawdown().show()
    # result.plot_monthly_returns().show()


if __name__ == "__main__":
    main()
