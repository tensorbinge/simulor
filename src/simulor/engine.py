"""Core event-driven engine for orchestrating backtests and live trading.

The Engine is the central orchestrator that manages the event loop and coordinates
all strategy components. It handles:
- Event loop orchestration: iterate through chronological MarketEvents
- Subscription-based dispatch: filter events per strategy subscriptions
- Component lifecycle: initialize and shutdown all models
- Strategy pipeline: Universe -> Alpha -> Portfolio -> Risk -> Execution
- Order routing: forward orders to Broker and route fills back
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from simulor.core.connectors import Broker
from simulor.core.events import DataEvent, EndOfStreamEvent, EventBus, MarketEvent, SystemEvent
from simulor.core.models import Context, Feed
from simulor.data import MarketStore
from simulor.execution.simulation.broker import SimulatedBroker
from simulor.logging import get_logger
from simulor.portfolio import Fund, Portfolio
from simulor.strategy import Strategy

if TYPE_CHECKING:
    from simulor.analytics import BacktestResult

__all__ = ["Engine"]

# Create module logger
logger = get_logger(__name__)


class Engine:
    """Event-driven backtesting and live trading engine.

    The Engine orchestrates the complete strategy execution flow:
    1. Iterate through MarketEvents from data provider
    2. Update strategy's MarketStore with subscribed data
    3. Execute strategy pipeline if data was delivered:
       - Universe selection (cached, only updates on rebalance)
       - Alpha signal generation
       - Portfolio construction
       - Risk management
       - Order generation
    4. Route orders to Broker for execution
    5. Apply fills to strategy portfolios

    Design decisions:
    - Bundled events: MarketEvent contains all same-timestamp data
    - Strategy isolation: Each strategy has its own MarketStore and Portfolio
    - Subscription filtering: Memory-efficient, O(subscribed) not O(all data)
    - Broker manages both global account and per-strategy subaccounts

    Example:
        >>> from simulor.engine import Engine
        >>> from simulor.data.providers.csv import CSVDataProvider
        >>> from simulor.portfolio.fund import Fund
        >>> from simulor.strategy import Strategy
        >>>
        >>> # Create data provider
        >>> data_provider = CSVDataProvider("data/market_snapshot.csv")
        >>>
        >>> # Create strategies
        >>> strategy1 = Strategy(...)
        >>> strategy2 = Strategy(...)
        >>>
        >>> # Create portfolio
        >>> fund = Fund(
        ...     strategies=[strategy1, strategy2],
        ...     capital=Decimal("100000")
        ... )
        >>>
        >>> # Run backtest
        >>> engine = Engine(data=data_provider, fund=fund)
        >>> result = engine.run(start='2020-01-01', end='2024-12-31', mode='backtest')
    """

    def __init__(
        self,
        data: Feed,
        fund: Fund,
        broker: Broker,
    ):
        """Initialize engine with data provider and portfolio configuration.

        Args:
            data: DataProvider yielding MarketEvents in chronological order
            fund: Fund containing strategies and capital allocation
            broker: Optional broker instance (created automatically if not provided)
        """
        logger.debug("Initializing engine with %d strategies", len(fund.strategies))

        self._data_feed = data
        self._fund = fund

        # Create or use provided broker
        self._broker = broker
        logger.debug("Creating broker with starting cash: $%s", fund.capital)

        # Execution state
        self._current_timestamp: datetime | None = None
        self._is_running = False

        # Strategy management
        self._strategies: dict[str, Strategy] = {}
        self._strategy_market_stores: dict[str, MarketStore] = {}

        # Event bus for internal event handling
        self._event_bus = EventBus()

        self._data_feed.initialize(self._event_bus)

        # Initialize broker with global portfolio
        strategy_portfolios: dict[str, Portfolio] = {}
        global_portfolio = Portfolio(starting_cash=self._fund.capital)
        self._broker.initialize(self._event_bus, global_portfolio, strategy_portfolios)

        # Initialize strategies from portfolio
        self._initialize_portfolio_strategies()

        logger.info(
            "Engine initialized with %d strategies, total capital=$%s",
            len(self._strategies),
            fund._capital,
        )

    @property
    def portfolio(self) -> Portfolio:
        """Get the global portfolio tracking all positions and cash."""
        return self._broker.global_portfolio

    @staticmethod
    def _parse_datetime(value: str | datetime | None) -> datetime | None:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt
        elif isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=ZoneInfo("UTC"))
            return value
        return value

    def _initialize_portfolio_strategies(self) -> None:
        """Initialize all strategies from the Fund."""
        for strategy in self._fund.strategies:
            allocated_capital = self._fund.get_allocation(strategy.name)
            self._add_strategy(strategy, allocated_capital)

    def _add_strategy(self, strategy: Strategy, allocated_capital: Decimal) -> None:
        """Internal method to register a strategy for execution.

        Allocates capital from broker's global account and creates
        isolated MarketStore and Portfolio for the strategy.

        Args:
            strategy: Strategy to add
            allocated_capital: Capital allocated to this strategy by Fund

        Raises:
            ValueError: If strategy name already registered
            RuntimeError: If engine is currently running
        """
        if self._is_running:
            logger.error("Cannot add strategy '%s' while engine is running", strategy.name)
            raise RuntimeError("Cannot add strategy while engine is running")

        if strategy.name in self._strategies:
            logger.error("Strategy '%s' is already registered", strategy.name)
            raise ValueError(f"Strategy '{strategy.name}' is already registered")

        logger.debug(
            "Registering strategy '%s' with allocated capital=$%s",
            strategy.name,
            allocated_capital,
        )

        # Register with broker (allocates capital, creates portfolio)
        self._broker.register_strategy(strategy.name, allocated_capital)

        # Store strategy
        self._strategies[strategy.name] = strategy

        # Create isolated market_store for this strategy
        self._strategy_market_stores[strategy.name] = MarketStore()

        logger.info(
            "Registered strategy '%s' with capital=$%s",
            strategy.name,
            allocated_capital,
        )

    def run(
        self,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        mode: Literal["backtest", "paper", "live"] = "backtest",
    ) -> BacktestResult:
        """Run the engine by iterating through all market data events.

        This is the main event loop that:
        1. Iterates through MarketEvents (optionally filtered by start/end)
        2. Processes each event for all strategies

        Args:
            start: Start date for backtest (optional, uses all data if not provided)
            end: End date for backtest (optional, uses all data if not provided)
            mode: Execution mode - 'backtest', 'paper', or 'live'

        Returns:
            BacktestResult instance with all metrics computed

        The engine maintains deterministic execution order by processing
        strategies in alphabetical order by name.
        """
        if self._is_running:
            logger.error("Engine is already running")
            raise RuntimeError("Engine is already running")

        logger.info(
            "Starting %s run from %s to %s with %d strategies",
            mode,
            start or "earliest data",
            end or "latest data",
            len(self._strategies),
        )

        self._is_running = True
        self._execution_mode = mode

        _start_dt = self._parse_datetime(start)
        _end_dt = self._parse_datetime(end)

        # Start the feed thread
        self._data_feed.start()

        # Connect broker
        self._broker.connect()

        try:
            # Phase 1: Initialize all strategies
            logger.debug("Initializing all strategies")
            self._initialize_strategies()
            logger.info("All strategies initialized successfully")

            # Phase 2: Main event loop
            logger.debug("Starting main event loop")

            while self._is_running:
                event = self._event_bus.next(timeout=0.05)
                if event is None:
                    continue

                # Filter by date range if provided
                if _start_dt and event.time < _start_dt:
                    continue
                if _end_dt and event.time > _end_dt:
                    break

                self._current_timestamp = event.time

                if isinstance(event, DataEvent):
                    self._handle_data_event(event)
                elif isinstance(event, SystemEvent):
                    self._handle_system_event(event)

            logger.info(
                "Event loop completed: processed %d events from %s to %s",
                self._event_bus.consumed_event_count,
                _start_dt or "earliest data",
                _end_dt or "latest data",
            )

            # Return results
            logger.debug("Generating backtest results")
            result = self._generate_results()
            logger.info("Backtest completed successfully")
            return result

        except Exception as e:
            logger.critical("Backtest failed with error: %s", str(e), exc_info=True)
            raise
        finally:
            self._is_running = False
            self._broker.disconnect()

            logger.debug("Engine stopped")

    def _handle_data_event(self, event: DataEvent) -> None:
        if isinstance(event, MarketEvent):
            self._handle_market_event(event)
        elif isinstance(event, EndOfStreamEvent):
            self._handle_end_of_stream_event(event)

        self._event_bus.task_done(queue_type="data")

    def _handle_market_event(self, market_event: MarketEvent) -> None:
        self._process_event(market_event)
        logger.debug(
            "Processed event #%d at %s with %d data points",
            self._event_bus.consumed_event_count,
            market_event.time,
            market_event.count,
        )

    def _handle_end_of_stream_event(self, event: EndOfStreamEvent) -> None:
        logger.info("Received EndOfStreamEvent at %s: %s, stopping engine", event.time, event.reason)
        self._current_timestamp = event.time
        self._is_running = False

    def _handle_system_event(self, _event: SystemEvent) -> None:
        self._event_bus.task_done(queue_type="system")

    def _initialize_strategies(self) -> None:
        """Initialize all strategies by injecting context.

        Injects Context (market_store, portfolio) into all strategy components.
        """
        for strategy_name in sorted(self._strategies.keys()):
            logger.debug("Initializing strategy '%s'", strategy_name)
            strategy = self._strategies[strategy_name]
            market_store = self._strategy_market_stores[strategy_name]
            portfolio = self._broker.strategy_portfolios[strategy_name]

            # Inject context into all components
            context = Context(market_store=market_store, portfolio=portfolio, event_bus=self._event_bus)
            strategy.universe.set_context(context)
            strategy.alpha.set_context(context)
            strategy.construction.set_context(context)
            strategy.risk.set_context(context)
            strategy.execution.set_context(context)

            logger.debug("Strategy '%s' initialized with context", strategy_name)

    def _process_event(self, market_event: MarketEvent) -> None:
        """Process a single MarketEvent for all strategies.

        For each strategy:
        1. Filter and extract market data from event buckets
        2. Update strategy's MarketStore
        3. Execute strategy pipeline if data was delivered
        4. Route generated orders to Broker

        Strategies are processed in deterministic alphabetical order.

        Args:
            event: MarketEvent to process
        """
        logger.debug(
            "Processing event at %s with %d market data points",
            market_event.time,
            market_event.count,
        )

        # Set broker current time before processing strategies
        if isinstance(self._broker, SimulatedBroker):
            self._broker._current_time = market_event.time

        # Process strategies in deterministic order
        for strategy_name, strategy in sorted(self._strategies.items()):
            market_store = self._strategy_market_stores[strategy_name]
            portfolio = self._broker.strategy_portfolios[strategy_name]

            # Execute strategy pipeline
            self._execute_strategy_pipeline(
                strategy=strategy,
                market_event=market_event,
                portfolio=portfolio,
                market_store=market_store,
            )

        # Process broker events after orders are submitted
        if isinstance(self._broker, SimulatedBroker):
            self._broker.on_market_event(event=market_event)
            self._broker.sync_global_portfolio(timestamp=market_event.time)

    def _execute_strategy_pipeline(
        self,
        strategy: Strategy,
        market_event: MarketEvent,
        portfolio: Portfolio,
        market_store: MarketStore,
    ) -> None:
        """Execute the 5-component strategy pipeline.

        Pipeline flow:
        1. Universe -> List[Instrument]
        2. Alpha -> List[Signal]
        3. Portfolio -> List[Target] (desired holdings)
        4. Risk -> List[Target] (risk-adjusted targets)
        5. Execution -> List[OrderSpec]

        Then route orders to Broker for execution.

        Args:
            strategy: Strategy to execute
            market_event: MarketEvent for this strategy
            portfolio: Strategy's Portfolio
        """
        # 1. Universe selection (typically cached, only updates on rebalance)
        universe = strategy.universe.select_universe()
        logger.debug(
            "Strategy '%s': universe contains %d instruments",
            strategy.name,
            len(universe),
        )

        filtered_event = market_event.filter_by_instrument(set(universe))

        if filtered_event.count == 0:
            # No subscribed data for this strategy
            logger.debug(
                "No subscribed data for strategy '%s' at %s",
                strategy.name,
                market_event.time,
            )
            return None

        logger.debug(
            "Strategy '%s' received %d subscribed data points at %s",
            strategy.name,
            filtered_event.count,
            market_event.time,
        )

        market_store.update(filtered_event)

        # 2. Alpha: Generate signals
        all_signals = strategy.alpha.generate_signals(filtered_event)
        logger.debug(
            "Strategy '%s': generated %d signals",
            strategy.name,
            len(all_signals),
        )

        # 3. Portfolio construction: Create desired holdings
        targets = strategy.construction.create_targets(all_signals)
        logger.debug(
            "Strategy '%s': created %d targets",
            strategy.name,
            len(targets),
        )

        # 4. Risk management: Adjust desired holdings
        constrained_targets = strategy.risk.adjust_targets(targets)
        if len(constrained_targets) != len(targets):
            logger.debug(
                "Strategy '%s': risk management changed targets from %d to %d",
                strategy.name,
                len(targets),
                len(constrained_targets),
            )

        # 5. Execution: Generate orders
        orders = strategy.execution.generate_orders(constrained_targets)
        if orders:
            logger.debug(
                "Strategy '%s': generated %d orders at %s",
                strategy.name,
                len(orders),
                market_event.time,
            )

        # Route orders to Broker
        # TODO: Broker should emit order events (rejected/accepted/filled) and
        # engine should process them based on user configuration:
        # - on_order_rejected: callback(order, reason) or raise/log/ignore
        # - on_order_filled: callback(fill) for custom fill tracking
        # - on_insufficient_cash: 'skip', 'raise', 'liquidate', or callback
        # - on_insufficient_shares: 'skip', 'raise', 'partial', or callback
        # This will provide better observability and control over order lifecycle
        for order_spec in orders:
            resp = self._broker.submit_order(strategy_name=strategy.name, order_spec=order_spec)
            logger.info("Strategy '%s': submitted order %s", strategy.name, resp.order_id)

        # Mark portfolio to market with latest prices from this event
        # This happens on EVERY event - whether signals were generated or not
        # If orders were executed, new positions will now get their current_price set
        market_store = self._strategy_market_stores[strategy.name]
        portfolio.mark_to_market(market_store.get_latest_prices([*portfolio.positions.keys()]))
        portfolio.record_state(timestamp=market_event.time)

    def _generate_results(self) -> BacktestResult:
        """Generate BacktestResult with all metrics eagerly computed.

        Returns:
            BacktestResult instance with comprehensive performance metrics
        """
        # Extract recorders from portfolios
        global_recorder = self._broker.global_portfolio.recorder

        strategy_recorders = {name: portfolio.recorder for name, portfolio in self._broker.strategy_portfolios.items()}

        # Collect all trades from global portfolio
        trades = self._broker.global_portfolio.trades

        # Get actual start/end dates from recorded data
        snapshots = global_recorder.get_snapshots()
        if snapshots:
            actual_start = snapshots[0].timestamp
            actual_end = snapshots[-1].timestamp
        else:
            # Fallback to current timestamp if no snapshots
            actual_start = self._current_timestamp or datetime.now()
            actual_end = self._current_timestamp or datetime.now()

        from simulor.analytics import BacktestResult

        # Create BacktestResult (metrics computed in __post_init__)
        return BacktestResult(
            global_recorder=global_recorder,
            strategy_recorders=strategy_recorders,
            trades=trades,
            initial_capital=self._fund.capital,
            start_date=actual_start,
            end_date=actual_end,
            benchmark_returns=None,  # TODO: Add benchmark support
        )
