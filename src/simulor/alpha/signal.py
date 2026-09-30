"""Signal definitions for alpha models.

Defines:
- SignalDirection: Enumeration of forecast direction
- SignalType: Enumeration of signal sources
- Signal: Trading signal with strength and confidence
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from simulor.types import Instrument

__all__ = ["SignalDirection", "SignalType", "Signal"]


class SignalDirection(Enum):
    """Direction of a forecast."""

    LONG = "long"  # Expect the instrument to appreciate
    SHORT = "short"  # Expect the instrument to depreciate
    FLAT = "flat"  # No directional view


class SignalType(Enum):
    """Type of signal source."""

    TECHNICAL_INDICATOR = "technical_indicator"  # Indicator-based: MA, RSI, MACD, crossovers
    MACHINE_LEARNING = "machine_learning"  # ML models: neural nets, tree ensembles, etc.
    FUNDAMENTAL_ANALYSIS = "fundamental_analysis"  # Fundamentals: earnings, valuation, financial ratios
    SENTIMENT_ANALYSIS = "sentiment_analysis"  # Sentiment sources: news, social, alternative data
    STATISTICAL_ARBITRAGE = "statistical_arbitrage"  # Stat-arb: pairs, cointegration, mean-reversion
    ORDER_FLOW = "order_flow"  # Market microstructure: order flow, depth, volume profile
    EVENT_DRIVEN = "event_driven"  # Corporate/event signals: earnings, M&A, dividends
    MACRO_ECONOMIC = "macro_economic"  # Macro indicators: rates, GDP, CPI, policy events
    FACTOR_EXPOSURE = "factor_exposure"  # Factor/risk exposures: momentum, value, quality, size
    OTHER = "other"  # Misc / custom signal sources


@dataclass(frozen=True)
class Signal:
    """Trading signal describing forecast intent.

    A signal states a view, not a position size. Converting views into
    desired holdings is the responsibility of portfolio construction.

    Signals are immutable, and several signals may exist for the same
    instrument. The framework defines no aggregation rule for such
    duplicates: combination policy belongs to portfolio construction or
    later stages, not to this artifact.

    Attributes:
        instrument: Instrument this signal applies to
        timestamp: Time signal was generated
        direction: Direction of the forecast
        strength: Forecast magnitude from 0 (no conviction) to 1 (maximum conviction)
        confidence: Reliability estimate from 0 (uncertain) to 1 (very confident)
        signal_type: Source/type of signal
        source_id: Specific strategy or model generating the signal
        horizon: Validity duration of the signal
        id: Unique identifier for the signal
        metadata: Additional signal information
    """

    instrument: Instrument
    timestamp: datetime

    # The Core Alpha
    direction: SignalDirection  # Forecast direction
    strength: Decimal  # Normalized magnitude: 0.0 to 1.0
    confidence: Decimal  # Probability/Conviction: 0.0 to 1.0

    # Classification
    signal_type: SignalType | None = None  # Broad category (e.g., ML)
    source_id: str | None = None  # Specific Strategy/Model Name (e.g., "LSTM_Model_v2")

    # Lifecycle
    horizon: timedelta | None = None  # How long is this signal valid? (e.g., 1 day)

    id: uuid.UUID = field(default_factory=uuid.uuid4)

    # Extensibility
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate signal data."""
        if not (Decimal("0") <= self.strength <= Decimal("1")):
            raise ValueError(f"Strength must be in [0, 1], got {self.strength}")
        if not (Decimal("0") <= self.confidence <= Decimal("1")):
            raise ValueError(f"Confidence must be in [0, 1], got {self.confidence}")

    @property
    def signed_strength(self) -> Decimal:
        """Get strength carrying the sign implied by direction."""
        if self.direction is SignalDirection.LONG:
            return self.strength
        if self.direction is SignalDirection.SHORT:
            return -self.strength
        return Decimal("0")

    @property
    def is_buy(self) -> bool:
        """Check if signal is bullish."""
        return self.direction is SignalDirection.LONG

    @property
    def is_sell(self) -> bool:
        """Check if signal is bearish."""
        return self.direction is SignalDirection.SHORT

    @property
    def is_flat(self) -> bool:
        """Check if signal has no directional view."""
        return self.direction is SignalDirection.FLAT

    @property
    def weighted_strength(self) -> Decimal:
        """Get signed strength weighted by confidence."""
        return self.signed_strength * self.confidence
