"""Target definitions for portfolio construction and risk.

Defines:
- TargetKind: Enumeration of target units
- TargetSource: Enumeration of stages that produce targets
- Target: Desired end-state holding for a single instrument
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from simulor.types.instruments import Instrument

__all__ = ["TargetKind", "TargetSource", "Target"]


class TargetKind(Enum):
    """Unit in which a target holding is expressed."""

    QUANTITY = "quantity"  # Signed number of shares or contracts
    WEIGHT = "weight"  # Signed fraction of total portfolio value
    NOTIONAL = "notional"  # Signed currency value
    FLAT = "flat"  # No holding


class TargetSource(Enum):
    """Pipeline stage that produced a target."""

    PORTFOLIO_CONSTRUCTION = "portfolio_construction"  # Converted from signals
    RISK_ADJUSTMENT = "risk_adjustment"  # Emitted or modified by risk
    MANUAL_OVERRIDE = "manual_override"  # Set outside the pipeline
    REBALANCE = "rebalance"  # Scheduled rebalance
    LIQUIDATION = "liquidation"  # Position being closed out


@dataclass(frozen=True)
class Target:
    """Desired end-state holding for a single instrument.

    A target states what the portfolio should hold, not how to reach it.
    Converting targets into orders is the responsibility of execution models.

    Targets are immutable, and several targets may exist for the same
    instrument. The framework defines no merge rule for such duplicates:
    combination policy belongs to portfolio construction, risk, or execution
    logic, not to this artifact.

    Attributes:
        instrument: Instrument the target applies to
        timestamp: Time the target was created
        kind: Unit in which value is expressed
        value: Signed target value in the unit given by kind
        source: Stage that created the target
        reason: Optional description of why the target exists
        signal_id: Signal that motivated this target, if any
        parent_target_id: Target this one was derived from, if any
        metadata: Additional target information
        id: Unique identifier for the target
    """

    instrument: Instrument
    timestamp: datetime

    kind: TargetKind
    value: Decimal

    source: TargetSource
    reason: str | None = None

    signal_id: UUID | None = None
    parent_target_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        """Validate target data."""
        if self.kind is TargetKind.FLAT and self.value != Decimal("0"):
            raise ValueError(f"FLAT targets must have value 0, got {self.value}")

    @classmethod
    def quantity(
        cls,
        instrument: Instrument,
        timestamp: datetime,
        value: Decimal,
        *,
        source: TargetSource = TargetSource.PORTFOLIO_CONSTRUCTION,
        reason: str | None = None,
        signal_id: UUID | None = None,
        parent_target_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Target:
        """Create a target expressed as a signed quantity of the instrument."""
        return cls(
            instrument=instrument,
            timestamp=timestamp,
            kind=TargetKind.QUANTITY,
            value=value,
            source=source,
            reason=reason,
            signal_id=signal_id,
            parent_target_id=parent_target_id,
            metadata=metadata or {},
        )

    @classmethod
    def weight(
        cls,
        instrument: Instrument,
        timestamp: datetime,
        value: Decimal,
        *,
        source: TargetSource = TargetSource.PORTFOLIO_CONSTRUCTION,
        reason: str | None = None,
        signal_id: UUID | None = None,
        parent_target_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Target:
        """Create a target expressed as a signed fraction of portfolio value."""
        return cls(
            instrument=instrument,
            timestamp=timestamp,
            kind=TargetKind.WEIGHT,
            value=value,
            source=source,
            reason=reason,
            signal_id=signal_id,
            parent_target_id=parent_target_id,
            metadata=metadata or {},
        )

    @classmethod
    def notional(
        cls,
        instrument: Instrument,
        timestamp: datetime,
        value: Decimal,
        *,
        source: TargetSource = TargetSource.PORTFOLIO_CONSTRUCTION,
        reason: str | None = None,
        signal_id: UUID | None = None,
        parent_target_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Target:
        """Create a target expressed as a signed currency value."""
        return cls(
            instrument=instrument,
            timestamp=timestamp,
            kind=TargetKind.NOTIONAL,
            value=value,
            source=source,
            reason=reason,
            signal_id=signal_id,
            parent_target_id=parent_target_id,
            metadata=metadata or {},
        )

    @classmethod
    def flat(
        cls,
        instrument: Instrument,
        timestamp: datetime,
        *,
        source: TargetSource = TargetSource.PORTFOLIO_CONSTRUCTION,
        reason: str | None = None,
        signal_id: UUID | None = None,
        parent_target_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Target:
        """Create a target that closes the holding for the instrument."""
        return cls(
            instrument=instrument,
            timestamp=timestamp,
            kind=TargetKind.FLAT,
            value=Decimal("0"),
            source=source,
            reason=reason,
            signal_id=signal_id,
            parent_target_id=parent_target_id,
            metadata=metadata or {},
        )
