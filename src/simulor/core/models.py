"""Strategy component model definitions.

Defines abstract base classes for all strategy components in the framework:
- UniverseSelectionModel: Determine which instruments to trade
- AlphaModel: Generate trading signals from market data
- PortfolioConstructionModel: Create desired holdings from signals
- RiskModel: Adjust desired holdings to respect risk constraints
- ExecutionModel: Convert desired holdings into orders
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

    from simulor.alpha.signal import Signal
    from simulor.core.connectors import Connector
    from simulor.core.events import DataEvent, EventBus, MarketEvent
    from simulor.data.market_store import MarketStore
    from simulor.portfolio.manager import Portfolio
    from simulor.types import Instrument, OrderSpec, Target

__all__ = [
    "Context",
    "Model",
    "Feed",
    "UniverseSelectionModel",
    "AlphaModel",
    "PortfolioConstructionModel",
    "RiskModel",
    "ExecutionModel",
    "AllocationModel",
]


class Context:
    """Execution context for all component models.

    Provides access to engine state for strategy components.
    """

    def __init__(self, market_store: MarketStore, portfolio: Portfolio, event_bus: EventBus) -> None:
        self.market_store = market_store
        self.portfolio = portfolio
        self.event_bus = event_bus


class Model:
    """Base class for strategy models with context access."""

    def set_context(self, context: Context) -> None:
        """Set the context for this model."""
        self._context = context

    @property
    def market_store(self) -> MarketStore:
        """Get the market store from the context."""
        return self._context.market_store

    @property
    def portfolio(self) -> Portfolio:
        """Get the portfolio from the context."""
        return self._context.portfolio

    @property
    def event_bus(self) -> EventBus:
        """Get the event bus from the context."""
        return self._context.event_bus


class Feed(ABC):
    """Base class for data feeds that publish market events.

    Feeds are responsible for:
    - Streaming market data from various sources (files, APIs, brokers)
    - Publishing DataEvent objects to the event bus
    - Managing their own lifecycle (start/stop/reconnect if applicable)

    Feeds may optionally use a Connector for external data sources that
    require connection management (broker APIs, databases, etc.).
    """

    def __init__(self, connector: Connector | None = None):
        """Initialize feed with optional connector.

        Args:
            connector: Optional connector for external data sources requiring
                connection management (e.g., broker APIs, databases)
        """
        self._event_bus: EventBus | None = None
        self._connector = connector

    def initialize(self, event_bus: EventBus) -> None:
        """Set the event bus for publishing market data events.

        Args:
            event_bus: Event bus to publish events to
        """
        self._event_bus = event_bus

    @abstractmethod
    def stream(self) -> None:
        """Stream market data and publish events to the event bus.

        Main data processing loop. Implementations should:
        1. Read/receive market data from source
        2. Create DataEvent objects containing ticks/bars
        3. Call publish_event() to send events to engine
        4. Publish EndOfStreamEvent when data stream completes

        This method is called by start() in a background thread, or can
        be called directly for synchronous processing.
        """
        ...

    def start(self) -> None:
        """Start streaming data in a background daemon thread.

        Spawns a new thread that calls stream(). The thread is daemonized
        so it won't prevent the program from exiting.

        For synchronous processing, call stream() directly instead.
        """
        thread = threading.Thread(target=self.stream, daemon=True)
        thread.start()

    def publish_event(self, event: DataEvent) -> None:
        """Publish a market data event to the event bus.

        Args:
            event: Data event to publish (typically MarketEvent or EndOfStreamEvent)

        Raises:
            RuntimeError: If event bus has not been set via initialize()
        """
        if self._event_bus is None:
            raise RuntimeError("Event bus not set. Call initialize() first.")
        self._event_bus.publish(event)

    # Connection lifecycle management - delegates to connector if present

    def connect(self) -> None:
        """Connect to external data source if connector is used.

        Delegates to the connector's connect() method if one was provided.
        For feeds without connectors (e.g., file-based), this is a no-op.
        """
        if self._connector:
            self._connector.connect()

    def disconnect(self) -> None:
        """Disconnect from external data source if connector is used.

        Delegates to the connector's disconnect() method if one was provided.
        For feeds without connectors, this is a no-op.
        """
        if self._connector:
            self._connector.disconnect()

    def is_connected(self) -> bool:
        """Check if feed is connected to its data source.

        Returns:
            True if connected (or no connector needed), False otherwise
        """
        return self._connector.is_connected() if self._connector else True


class UniverseSelectionModel(Model, ABC):
    """Abstract base class for universe selection.

    Universe selection models determine which instruments the strategy
    should consider trading at any point in time.

    Components have access to:
    - self.market_store: Historical market data
    - self.portfolio: Current portfolio state
    """

    @abstractmethod
    def select_universe(self) -> list[Instrument]:
        """Return list of instruments to trade.

        Returns:
            List of instruments in the current trading universe.
            This list can change over time (dynamic universe selection).
        """
        ...


class AlphaModel(Model, ABC):
    """Abstract base class for alpha signal generation.

    Alpha models analyze market data and generate trading signals
    indicating direction (long/short/flat), strength, and confidence.

    Components have access to:
    - self.market_store: Historical market data
    - self.portfolio: Current portfolio state
    """

    @abstractmethod
    def generate_signals(self, market_event: MarketEvent) -> list[Signal]:
        """Generate trading signals from market data.

        Args:
            market_event: Current market data event
        Returns:
            List of signals. Only return signals for instruments you want to
            trade and omit instruments with no view. Several signals may be
            returned for the same instrument: the framework does not force
            them to be merged before portfolio construction.
        """
        ...


class PortfolioConstructionModel(Model, ABC):
    """Abstract base class for portfolio construction.

    Portfolio construction models convert trading signals into desired
    holdings, handling position sizing and portfolio weight allocation.

    Components have access to:
    - self.market_store: Historical market data
    - self.portfolio: Current portfolio state
    """

    @abstractmethod
    def create_targets(self, signals: list[Signal]) -> list[Target]:
        """Create desired holdings from trading signals.

        Args:
            signals: Trading signals from alpha models

        Returns:
            List of targets describing the desired end-state portfolio.
            Targets may be expressed as quantities, weights, notionals, or
            flat instructions, and several targets may be returned for the
            same instrument.
        """
        ...


class RiskModel(Model, ABC):
    """Abstract base class for risk management.

    Risk models review desired holdings and keep them within defined risk
    parameters, for example by suppressing an entry, reducing exposure,
    flattening a position, or adding a hedge.

    Components have access to:
    - self.market_store: Historical market data
    - self.portfolio: Current portfolio state
    """

    @abstractmethod
    def adjust_targets(self, targets: list[Target]) -> list[Target]:
        """Adjust desired holdings to respect risk constraints.

        Args:
            targets: Targets from portfolio construction

        Returns:
            Targets after risk review. Implementations may replace targets
            with new ones, remove targets, or add new targets. Targets that
            pass unchanged are typically returned as-is.
        """
        ...


class ExecutionModel(Model, ABC):
    """Abstract base class for order execution.

    Execution models convert desired holdings into executable orders,
    handling target normalization, order types, timing, and other
    execution details.

    Components have access to:
    - self.market_store: Historical market data
    - self.portfolio: Current portfolio state
    """

    @abstractmethod
    def generate_orders(self, targets: list[Target]) -> list[OrderSpec]:
        """Generate orders that move the portfolio towards the targets.

        Args:
            targets: Targets after risk management

        Returns:
            List of order specifications to execute. Implementations must
            not assume that targets have already been merged per instrument:
            normalizing the target stream into executable quantity
            transitions is the responsibility of the execution model.
        """
        ...


class AllocationModel(Model, ABC):
    """Abstract base class for portfolio-level capital allocation across strategies.

    Allocation models determine how total portfolio capital is distributed
    among multiple strategies. Examples include equal weight, risk parity,
    performance-based, or custom allocation schemes.
    """

    @abstractmethod
    def allocate(self, strategy_names: Iterable[str], total_capital: Decimal) -> dict[str, Decimal]:
        """Calculate capital allocation for each strategy.

        Args:
            strategy_names: Set of strategy names to allocate capital to
            total_capital: Total capital available for allocation

        Returns:
            Dictionary mapping strategy name to allocated capital amount
        """
        ...
