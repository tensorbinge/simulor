"""Contract tests for the Signal artifact."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulor.alpha.signal import Signal, SignalDirection, SignalType
from simulor.types import Instrument

TIMESTAMP = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
AAPL = Instrument.stock("AAPL")


def test_signal_records_direction_strength_and_confidence() -> None:
    signal = Signal(
        instrument=AAPL,
        timestamp=TIMESTAMP,
        direction=SignalDirection.LONG,
        strength=Decimal("0.8"),
        confidence=Decimal("0.6"),
    )

    assert signal.direction is SignalDirection.LONG
    assert signal.strength == Decimal("0.8")
    assert signal.confidence == Decimal("0.6")
    assert isinstance(signal.id, UUID)


def test_strength_is_magnitude_and_rejects_values_outside_unit_interval() -> None:
    with pytest.raises(ValueError, match="Strength"):
        Signal(
            instrument=AAPL,
            timestamp=TIMESTAMP,
            direction=SignalDirection.LONG,
            strength=Decimal("1.5"),
            confidence=Decimal("0.5"),
        )

    with pytest.raises(ValueError, match="Strength"):
        Signal(
            instrument=AAPL,
            timestamp=TIMESTAMP,
            direction=SignalDirection.SHORT,
            strength=Decimal("-0.5"),
            confidence=Decimal("0.5"),
        )


def test_confidence_must_be_within_unit_interval() -> None:
    with pytest.raises(ValueError, match="Confidence"):
        Signal(
            instrument=AAPL,
            timestamp=TIMESTAMP,
            direction=SignalDirection.LONG,
            strength=Decimal("0.5"),
            confidence=Decimal("1.5"),
        )


def test_signed_strength_follows_direction() -> None:
    long_signal = Signal(
        instrument=AAPL,
        timestamp=TIMESTAMP,
        direction=SignalDirection.LONG,
        strength=Decimal("0.8"),
        confidence=Decimal("1.0"),
    )
    short_signal = Signal(
        instrument=AAPL,
        timestamp=TIMESTAMP,
        direction=SignalDirection.SHORT,
        strength=Decimal("0.4"),
        confidence=Decimal("1.0"),
    )

    assert long_signal.signed_strength == Decimal("0.8")
    assert short_signal.signed_strength == Decimal("-0.4")


def test_flat_direction_has_no_signed_strength() -> None:
    signal = Signal(
        instrument=AAPL,
        timestamp=TIMESTAMP,
        direction=SignalDirection.FLAT,
        strength=Decimal("0.9"),
        confidence=Decimal("1.0"),
    )

    assert signal.signed_strength == Decimal("0")


def test_direction_predicates() -> None:
    def build(direction: SignalDirection) -> Signal:
        return Signal(
            instrument=AAPL,
            timestamp=TIMESTAMP,
            direction=direction,
            strength=Decimal("0.5"),
            confidence=Decimal("0.5"),
        )

    assert build(SignalDirection.LONG).is_buy
    assert not build(SignalDirection.LONG).is_sell
    assert build(SignalDirection.SHORT).is_sell
    assert not build(SignalDirection.SHORT).is_buy
    assert build(SignalDirection.FLAT).is_flat


def test_weighted_strength_scales_signed_strength_by_confidence() -> None:
    signal = Signal(
        instrument=AAPL,
        timestamp=TIMESTAMP,
        direction=SignalDirection.SHORT,
        strength=Decimal("0.5"),
        confidence=Decimal("0.4"),
    )

    assert signal.weighted_strength == Decimal("-0.20")


def test_signal_is_immutable() -> None:
    signal = Signal(
        instrument=AAPL,
        timestamp=TIMESTAMP,
        direction=SignalDirection.LONG,
        strength=Decimal("0.5"),
        confidence=Decimal("0.5"),
    )

    with pytest.raises(FrozenInstanceError):
        signal.strength = Decimal("0.9")  # type: ignore[misc]


def test_signal_carries_classification_and_metadata() -> None:
    signal = Signal(
        instrument=AAPL,
        timestamp=TIMESTAMP,
        direction=SignalDirection.LONG,
        strength=Decimal("0.5"),
        confidence=Decimal("0.5"),
        signal_type=SignalType.TECHNICAL_INDICATOR,
        source_id="MovingAverageCrossover",
        horizon=timedelta(days=1),
        metadata={"fast_ma": Decimal("10")},
    )

    assert signal.signal_type is SignalType.TECHNICAL_INDICATOR
    assert signal.source_id == "MovingAverageCrossover"
    assert signal.horizon == timedelta(days=1)
    assert signal.metadata == {"fast_ma": Decimal("10")}


def test_multiple_signals_for_same_instrument_are_allowed() -> None:
    signals = [
        Signal(
            instrument=AAPL,
            timestamp=TIMESTAMP,
            direction=SignalDirection.LONG,
            strength=Decimal("0.8"),
            confidence=Decimal("1.0"),
        ),
        Signal(
            instrument=AAPL,
            timestamp=TIMESTAMP,
            direction=SignalDirection.LONG,
            strength=Decimal("0.5"),
            confidence=Decimal("0.5"),
        ),
    ]

    assert len(signals) == 2
    assert signals[0].id != signals[1].id
