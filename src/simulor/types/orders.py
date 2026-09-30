"""Order and fill type definitions.

Defines:
- OrderType: Enum for order types (market, limit, stop, etc.)
- OrderSpec: Order specification dataclass
- Fill: Executed trade result
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from simulor.types.common import OrderSide, TimeInForce
from simulor.types.instruments import Instrument
from simulor.types.target import TargetSource

__all__ = [
    "OrderType",
    "OrderSpec",
    "Fill",
]


class OrderType(Enum):
    """Type of order."""

    MARKET = "market"  # Execute at best available price
    LIMIT = "limit"  # Execute at specified price or better
    STOP = "stop"  # Market order triggered at stop price
    STOP_LIMIT = "stop_limit"  # Limit order triggered at stop price
    MARKET_IF_TOUCHED = "mit"  # Market order triggered at limit price
    LIMIT_IF_TOUCHED = "lit"  # Limit order triggered at limit price
    TRAILING_STOP = "trailing_stop"  # Stop order with trailing price
    TRAILING_STOP_LIMIT = "trailing_stop_limit"  # Stop-limit with trailing price


@dataclass(frozen=True)
class OrderSpec:
    """Order specification for execution.

    This is the output from ExecutionModel and input to the execution engine.
    Represents an order request without execution details (fills, status, etc.).

    Attributes:
        instrument: Instrument to trade
        side: Buy or sell
        quantity: Number of units to trade (always positive)
        order_type: Market, limit, stop, or stop-limit
        time_in_force: Order duration
        limit_price: Price for limit orders
        stop_price: Trigger price for stop orders
        reason: Optional description of why order was placed
        target_id: Target the order was generated from, if any
        source: Pipeline stage that produced the order, if any
        metadata: Optional additional data for tracking/debugging
    """

    instrument: Instrument
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    time_in_force: TimeInForce

    # Optional price parameters
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None

    # Only one of these should be set for TRAILING_STOP orders
    trailing_amount: Decimal | None = None
    trailing_percent: Decimal | None = None

    # Required if time_in_force is GTD (Good Till Date)
    expire_time: datetime | None = None

    # Provenance
    reason: str | None = None
    target_id: UUID | None = None  # Target this order was generated from
    source: TargetSource | None = None  # Stage that produced the order

    # Optional metadata
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate order specification logic."""
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive")

        # 1. Limit Price Validation
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.LIMIT_IF_TOUCHED) and (
            self.limit_price is None or self.limit_price <= 0
        ):
            raise ValueError(f"{self.order_type.value} orders require positive limit_price")

        # 2. Trigger Price Validation (Stop / Touched)
        trigger_types = (OrderType.STOP, OrderType.STOP_LIMIT, OrderType.MARKET_IF_TOUCHED, OrderType.LIMIT_IF_TOUCHED)
        if self.order_type in trigger_types and (self.stop_price is None or self.stop_price <= 0):
            raise ValueError(f"{self.order_type.value} orders require positive stop_price")

        # 3. Trailing Stop Validation
        if self.order_type in (OrderType.TRAILING_STOP, OrderType.TRAILING_STOP_LIMIT):
            if not (self.trailing_amount or self.trailing_percent):
                raise ValueError("Trailing orders require trailing_amount OR trailing_percent")
            if self.trailing_amount and self.trailing_percent:
                raise ValueError("Cannot specify both trailing_amount and trailing_percent")

        # 4. Expiry Validation
        # Assuming TimeInForce has a GTD member
        if self.time_in_force == TimeInForce.GTD and self.expire_time is None:
            raise ValueError("GTD orders require expire_time")

    @property
    def display_name(self) -> str:
        """Generate human-readable description."""
        parts = [
            self.side.value.upper(),
            f"{self.quantity}",
            self.instrument.symbol,
            f"@{self.order_type.name}",
        ]

        if self.limit_price:
            parts.append(f"LMT={self.limit_price}")
        if self.stop_price:
            parts.append(f"STOP={self.stop_price}")
        if self.trailing_amount:
            parts.append(f"TRAIL=${self.trailing_amount}")
        if self.trailing_percent:
            parts.append(f"TRAIL={self.trailing_percent}%")

        return " ".join(parts)


@dataclass(frozen=True)
class Fill:
    """Executed trade (fill) with all execution details.

    Represents a completed trade execution, including price, quantity,
    and transaction costs. This is the output of the execution layer
    and input to the Portfolio Manager.

    Attributes:
        instrument: Instrument that was executed
        quantity: Signed quantity (positive = buy, negative = sell)
        price: Execution price per unit
        commission: Total transaction costs (commissions, fees, spread)
    """

    instrument: Instrument
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        """Validate fill data."""
        if self.price <= 0:
            raise ValueError("Fill price must be positive")
        if self.quantity == 0:
            raise ValueError("Fill quantity cannot be zero")
        if self.commission < 0:
            raise ValueError("Commission cannot be negative")
