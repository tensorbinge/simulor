"""Simulor: Event-driven backtesting framework.

Simulor is a high-performance backtesting framework designed for institutional-grade
quantitative research and algorithmic trading strategy development.

Key Features:
    - Event-driven architecture for realistic simulation
    - Pluggable strategy components (Alpha, Portfolio, Risk, Execution)
    - Multiple data resolutions (tick, minute, hour, daily)
    - Institutional-grade analytics and performance metrics
    - Rust-accelerated performance (optional)
    - Environment parity (backtest, paper, live)

Quick Start:
    >>> from decimal import Decimal
    >>> from simulor import (
    ...     Strategy, Engine, Fund,
    ...     MovingAverageCrossover, EqualWeight, PositionLimit, Immediate, Static,
    ...     CsvFeed, SimulatedBroker, Resolution, Instrument
    ... )
    >>>
    >>> strategy = Strategy(
    ...     name='MA_Crossover',
    ...     universe=Static([Instrument.stock('SPY'), Instrument.stock('QQQ')]),
    ...     alpha=MovingAverageCrossover(fast_period=10, slow_period=20),
    ...     construction=EqualWeight(),
    ...     risk=PositionLimit(max_position=Decimal('0.1')),
    ...     execution=Immediate()
    ... )
    >>>
    >>> fund = Fund(strategies=[strategy], capital=Decimal('100000'))
    >>> engine = Engine(
    ...     data=CsvFeed('data/bars.csv', resolution=Resolution.DAILY),
    ...     fund=fund,
    ...     broker=SimulatedBroker()
    ... )
    >>> results = engine.run(start='2020-01-01', end='2023-12-31', mode='backtest')
"""

from __future__ import annotations

__version__ = "0.2.0b1"
__author__ = "Simulor Contributors"

from importlib import import_module
from typing import TYPE_CHECKING, Any

from simulor.allocation import WeightBasedAllocationModel
from simulor.alpha import MovingAverageCrossover, Signal, SignalType
from simulor.analytics import BacktestResult, StrategyMetrics, Tearsheet
from simulor.core.events import MarketEvent
from simulor.data import CsvFeed, MarketStore
from simulor.engine import Engine
from simulor.execution import Immediate, SimulatedBroker
from simulor.models import (
    AllocationModel,
    AlphaModel,
    ExecutionModel,
    PortfolioConstructionModel,
    RiskModel,
    UniverseSelectionModel,
)
from simulor.portfolio import EqualWeight, Fund, Portfolio, Position
from simulor.risk import PositionLimit
from simulor.strategy import Strategy
from simulor.types import (
    AssetType,
    ColumnName,
    Fill,
    Instrument,
    MarketData,
    OptionType,
    OrderSide,
    OrderSpec,
    OrderType,
    QuoteBar,
    QuoteTick,
    Resolution,
    TickDirection,
    TimeInForce,
    TradeBar,
    TradeTick,
)
from simulor.universe import Static

if TYPE_CHECKING:
    from simulor.execution.live.connectors import LongbridgeConnector
    from simulor.execution.live.longbridge import Longbridge

__all__ = [
    "__version__",
    "__author__",
    # Core Engine & Orchestration
    "Engine",
    # Strategy Framework
    "Strategy",
    # Portfolio Management
    "Fund",
    "Portfolio",
    "Position",
    "EqualWeight",
    # Alpha Generation
    "AlphaModel",
    "MovingAverageCrossover",
    "Signal",
    "SignalType",
    # Risk Management
    "RiskModel",
    "PositionLimit",
    # Execution
    "ExecutionModel",
    "Immediate",
    "SimulatedBroker",
    "OrderSpec",
    "OrderType",
    "OrderSide",
    "TimeInForce",
    "Fill",
    # Universe Selection
    "UniverseSelectionModel",
    "Static",
    # Capital Allocation
    "AllocationModel",
    "WeightBasedAllocationModel",
    # Data Providers & Structures
    "CsvFeed",
    "MarketStore",
    "Instrument",
    "MarketData",
    "TradeBar",
    "TradeTick",
    "QuoteBar",
    "QuoteTick",
    "Resolution",
    "AssetType",
    "OptionType",
    "TickDirection",
    "ColumnName",
    # Events (for custom components)
    "MarketEvent",
    # Protocols (for custom implementations)
    "PortfolioConstructionModel",
    # Analytics & Results
    "BacktestResult",
    "StrategyMetrics",
    "Tearsheet",
]

_OPTIONAL_EXPORTS = {
    "Longbridge": ("simulor.live", "Longbridge"),
    "LongbridgeConnector": ("simulor.live", "LongbridgeConnector"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _OPTIONAL_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'simulor' has no attribute {name!r}") from exc

    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
