"""
Event system for the Simulor trading simulation framework.

"""

from __future__ import annotations

import queue
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Literal

from simulor.logging import get_logger
from simulor.types import Instrument, MarketData, QuoteBar, QuoteTick, Resolution, TradeBar, TradeTick

# Create module logger for event-related logging
logger = get_logger(__name__)

__all__ = ["EventType", "Event", "DataEvent", "EndOfStreamEvent", "SystemEvent", "MarketEvent", "EventBus"]


class EventType(Enum):
    """
    Enumeration of event types in the simulation.

    Currently supports:
    - MARKET: Events related to market data updates.
    """

    END_OF_STREAM = auto()
    MARKET = auto()
    FILL = auto()


@dataclass(slots=True)
class Event:
    """
    Base class for all events in the simulation.

    Attributes:
        type (EventType): The type of the event.
        time (datetime): The timestamp when the event occurred.
    """

    type: EventType
    time: datetime


@dataclass(slots=True)
class DataEvent(Event):
    """For Market Data (L2, Ticks)"""

    ...


@dataclass(slots=True)
class EndOfStreamEvent(DataEvent):
    """Event indicating the end of a data stream."""

    type: EventType = field(default=EventType.END_OF_STREAM, init=False)
    reason: str


@dataclass(slots=True)
class SystemEvent(Event):
    """For Control Signals (Risk, Fills, Orders, Errors)"""

    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MarketEvent(DataEvent):
    """Optimized time-slice containing data per instrument and resolution for efficient filtering.
    Eliminates the need for type checking during strategy execution.
    """

    type: EventType = field(default=EventType.MARKET, init=False)

    _count: int = field(default=0, init=False, repr=False)

    _trade_ticks: dict[Instrument, list[TradeTick]] = field(default_factory=dict, repr=False)
    _quote_ticks: dict[Instrument, list[QuoteTick]] = field(default_factory=dict, repr=False)
    _trade_bars: dict[Instrument, dict[Resolution, TradeBar]] = field(default_factory=dict, repr=False)
    _quote_bars: dict[Instrument, dict[Resolution, QuoteBar]] = field(default_factory=dict, repr=False)

    @property
    def count(self) -> int:
        """Get total number of data points in this event."""
        return self._count

    def instruments(self) -> set[Instrument]:
        """Get all instruments contained in this market event.

        Returns:
            Set of all instruments in the event
        """
        instruments: set[Instrument] = set()
        instruments.update(self._trade_ticks.keys())
        instruments.update(self._quote_ticks.keys())
        instruments.update(self._trade_bars.keys())
        instruments.update(self._quote_bars.keys())
        return instruments

    def filter_by_instrument(self, instruments: set[Instrument]) -> MarketEvent:
        """Create a filtered MarketEvent for a specific instrument and resolution.

        Args:
            instruments: The set of instruments to filter by
        Returns:
            A new MarketEvent containing only data for the specified instruments
        """

        filtered_event = MarketEvent(time=self.time)
        count = 0

        # No filtering needed
        if not instruments:
            return filtered_event

        # 1. Trade Ticks
        for inst in instruments:
            trade_ticks = self._trade_ticks.get(inst)
            if trade_ticks:
                filtered_event._trade_ticks[inst] = trade_ticks
                count += len(trade_ticks)

        # 2. Quote Ticks
        for inst in instruments:
            quote_ticks = self._quote_ticks.get(inst)
            if quote_ticks:
                filtered_event._quote_ticks[inst] = quote_ticks
                count += len(quote_ticks)

        # 3. Trade Bars
        for inst in instruments:
            trade_bar = self._trade_bars.get(inst)
            if trade_bar:
                filtered_event._trade_bars[inst] = trade_bar
                count += len(trade_bar)

        # 4. Quote Bars
        for inst in instruments:
            quote_bar = self._quote_bars.get(inst)
            if quote_bar:
                filtered_event._quote_bars[inst] = quote_bar
                count += len(quote_bar)

        filtered_event._count = count
        return filtered_event

    def flatten(self) -> list[MarketData]:
        """Flatten all market data in this event into a single list.

        Returns:
            List of all MarketData items contained in this event
        """
        data_points: list[MarketData] = []

        for trade_ticks in self._trade_ticks.values():
            data_points.extend(trade_ticks)

        for quote_ticks in self._quote_ticks.values():
            data_points.extend(quote_ticks)

        for trade_bars in self._trade_bars.values():
            data_points.extend(trade_bars.values())

        for quote_bars in self._quote_bars.values():
            data_points.extend(quote_bars.values())

        return data_points

    def add(self, market_data: MarketData) -> None:
        """Dispatch item to correct bucket.

        Args:
            market_data: Market data item to add
        Raises:
            TypeError: If market_data type is not recognized
        """
        if isinstance(market_data, TradeTick):
            self._trade_ticks.setdefault(market_data.instrument, []).append(market_data)
        elif isinstance(market_data, QuoteTick):
            self._quote_ticks.setdefault(market_data.instrument, []).append(market_data)
        elif isinstance(market_data, TradeBar):
            self._trade_bars.setdefault(market_data.instrument, {})[market_data.resolution] = market_data
        elif isinstance(market_data, QuoteBar):
            self._quote_bars.setdefault(market_data.instrument, {})[market_data.resolution] = market_data
        else:
            raise TypeError(f"Unknown data type: {type(market_data)}")

        self._count += 1

    @property
    def trade_ticks(self) -> dict[Instrument, list[TradeTick]]:
        """Get all trade ticks grouped by instrument."""
        return self._trade_ticks

    @property
    def quote_ticks(self) -> dict[Instrument, list[QuoteTick]]:
        """Get all quote ticks grouped by instrument."""
        return self._quote_ticks

    @property
    def trade_bars(self) -> dict[Instrument, dict[Resolution, TradeBar]]:
        """Get all trade bars grouped by instrument."""
        return self._trade_bars

    @property
    def quote_bars(self) -> dict[Instrument, dict[Resolution, QuoteBar]]:
        """Get all quote bars grouped by instrument."""
        return self._quote_bars

    def get_last_trade_tick(self, instrument: Instrument) -> TradeTick | None:
        """Get the last trade tick for a given instrument.

        Args:
            instrument: The instrument to retrieve the last trade tick for.
        Returns:
            The last TradeTick for the instrument, or None if no ticks are available.
        """
        ticks = self._trade_ticks.get(instrument)
        return ticks[-1] if ticks else None

    def get_last_quote_tick(self, instrument: Instrument) -> QuoteTick | None:
        """Get the last quote tick for a given instrument.

        Args:
            instrument: The instrument to retrieve the last quote tick for.
        Returns:
            The last QuoteTick for the instrument, or None if no ticks are available.
        """
        ticks = self._quote_ticks.get(instrument)
        return ticks[-1] if ticks else None

    def get_min_res_trade_bar(self, instrument: Instrument) -> TradeBar | None:
        """Get the minimum resolution trade bar for a given instrument.

        Args:
            instrument: The instrument to retrieve the last trade bar for.
        Returns:
            The last TradeBar for the instrument, or None if no bars are available.
        """

        trade_bars = self._trade_bars.get(instrument)

        if trade_bars:
            # Get the bar with the highest resolution (smallest time frame)
            best_res = min(trade_bars.keys())
            best_bar = trade_bars[best_res]
            return best_bar

        return None

    def get_min_res_quote_bar(self, instrument: Instrument) -> QuoteBar | None:
        """Get the minimum resolution quote bar for a given instrument.

        Args:
            instrument: The instrument to retrieve the last quote bar for.
        Returns:
            The last QuoteBar for the instrument, or None if no bars are available.
        """

        quote_bars = self._quote_bars.get(instrument)

        if quote_bars:
            # Get the bar with the highest resolution (smallest time frame)
            best_res = min(quote_bars.keys())
            best_bar = quote_bars[best_res]
            return best_bar

        return None


# Type alias for event handler functions
EventHandler = Callable[[Event], None]


class EventBus:
    def __init__(self, data_qsize: int = 4096) -> None:
        """
        Initialize the EventBus with specified queue sizes.

        Args:
            data_qsize (int): Maximum size of the data event queue. Defaults to 4096.
        """

        # Data: Flow Control (Block when full)
        self._data_queue: queue.Queue[DataEvent] = queue.Queue(maxsize=data_qsize)
        # System: Critical Path (Never block, infinite size)
        self._system_queue: queue.Queue[SystemEvent] = queue.Queue()

        # Dictionary to hold subscribers for each event type
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)
        # Lock for thread-safe access to subscribers
        self._lock = threading.Lock()
        # Counter for total events published
        self._consumed_event_count: int = 0

    @property
    def consumed_event_count(self) -> int:
        """Get the total number of events published."""
        return self._consumed_event_count

    def publish(self, event: Event, backpressure: Literal["block", "drop"] = "block") -> bool:
        """
        Publish an event to the appropriate queue based on its type.

        Args:
            event (Event): The event to publish. Must be DataEvent or SystemEvent.
            backpressure (Literal["block", "drop"]): Strategy for handling full queues.
                "block" will wait until space is available.
                "drop" will discard the event if the queue is full. Defaults to "block".
        Returns:
            bool: True if the event was successfully published, False otherwise.
        """
        try:
            if isinstance(event, DataEvent):
                # Apply Backpressure
                self._data_queue.put(event, block=(backpressure == "block"))
            elif isinstance(event, SystemEvent):
                # Critical events always go through
                self._system_queue.put(event, block=False)
            else:
                logger.warning("Unknown event type: %s", type(event))
                return False
        except queue.Full:
            logger.warning("Event queue full, dropping event: %s", event)
            return False

        return True

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """
        Subscribe a handler to a specific event type.

        Args:
            event_type (EventType): The type of event to subscribe to.
            handler (EventHandler): The function to call when the event occurs.
        """
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """
        Unsubscribe a handler from a specific event type.

        Args:
            event_type (EventType): The type of event to unsubscribe from.
            handler (EventHandler): The handler to remove.
        """
        with self._lock:
            handlers = self._subscribers.get(event_type)
            if not handlers:
                return
            try:
                handlers.remove(handler)
                if not handlers:
                    # remove empty entry to avoid leaking keys
                    del self._subscribers[event_type]
            except ValueError:
                logger.debug("Attempted to unsubscribe handler not present for %s: %s", event_type, handler)

    def _notify_subscribers(self, event: Event) -> None:
        """
        Notify all subscribers of an event by calling their handlers.

        Args:
            event (Event): The event to notify subscribers about.

        Note:
            Errors in handlers are logged but do not stop notification of other handlers.
        """
        # Copy handlers under lock to avoid races with subscribe/unsubscribe
        with self._lock:
            handlers = list(self._subscribers.get(event.type, []))

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("Error in event handler while processing %s", event)

    def next(self, timeout: float = 0.1) -> Event | None:
        """
        Retrieve the next available event, prioritizing system events.

        Args:
            timeout (float): Timeout in seconds for waiting on fast events. Defaults to 0.1.

        Returns:
            Event | None: The processed event, or None if no event was available.
        """
        # Check system events (non-blocking)
        try:
            system_event = self._system_queue.get_nowait()
            self._notify_subscribers(system_event)
            self._consumed_event_count += 1
            return system_event
        except queue.Empty:
            pass

        # Check data events (with timeout)
        try:
            data_event = self._data_queue.get(timeout=timeout)
            self._notify_subscribers(data_event)
            self._consumed_event_count += 1
            return data_event
        except queue.Empty:
            return None

    def task_done(self, queue_type: Literal["data", "system"]) -> None:
        """
        Indicate that a previously enqueued event has been processed.

        Args:
            queue_type (Literal["data", "system"]): The type of queue the event was from.
        """
        if queue_type == "data":
            self._data_queue.task_done()
        elif queue_type == "system":
            self._system_queue.task_done()
