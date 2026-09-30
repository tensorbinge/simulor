"""Contract tests for the Target artifact."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulor.types import Instrument, Target, TargetKind, TargetSource

TIMESTAMP = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
AAPL = Instrument.stock("AAPL")


def test_quantity_target_records_signed_quantity() -> None:
    target = Target.quantity(AAPL, TIMESTAMP, Decimal("-50"), source=TargetSource.PORTFOLIO_CONSTRUCTION)

    assert target.kind is TargetKind.QUANTITY
    assert target.value == Decimal("-50")
    assert target.instrument == AAPL
    assert target.timestamp == TIMESTAMP
    assert target.source is TargetSource.PORTFOLIO_CONSTRUCTION
    assert isinstance(target.id, UUID)


def test_weight_target_records_weight_kind() -> None:
    target = Target.weight(AAPL, TIMESTAMP, Decimal("0.10"))

    assert target.kind is TargetKind.WEIGHT
    assert target.value == Decimal("0.10")
    assert target.source is TargetSource.PORTFOLIO_CONSTRUCTION


def test_notional_target_records_notional_kind() -> None:
    target = Target.notional(AAPL, TIMESTAMP, Decimal("25000"))

    assert target.kind is TargetKind.NOTIONAL
    assert target.value == Decimal("25000")


def test_flat_target_has_zero_value_and_keeps_source() -> None:
    target = Target.flat(AAPL, TIMESTAMP, source=TargetSource.LIQUIDATION)

    assert target.kind is TargetKind.FLAT
    assert target.value == Decimal("0")
    assert target.source is TargetSource.LIQUIDATION


def test_flat_target_rejects_non_zero_value() -> None:
    with pytest.raises(ValueError, match="FLAT"):
        Target(
            instrument=AAPL,
            timestamp=TIMESTAMP,
            kind=TargetKind.FLAT,
            value=Decimal("1"),
            source=TargetSource.RISK_ADJUSTMENT,
        )


def test_target_is_immutable() -> None:
    target = Target.quantity(AAPL, TIMESTAMP, Decimal("10"))

    with pytest.raises(FrozenInstanceError):
        target.value = Decimal("20")  # type: ignore[misc]


def test_targets_have_distinct_identity() -> None:
    first = Target.quantity(AAPL, TIMESTAMP, Decimal("10"))
    second = Target.quantity(AAPL, TIMESTAMP, Decimal("10"))

    assert first.id != second.id
    assert first != second


def test_target_carries_provenance_and_metadata() -> None:
    signal_id = uuid4()
    parent_id = uuid4()

    target = Target.quantity(
        AAPL,
        TIMESTAMP,
        Decimal("10"),
        reason="risk trimmed position",
        signal_id=signal_id,
        parent_target_id=parent_id,
        metadata={"model": "equal_weight"},
    )

    assert target.reason == "risk trimmed position"
    assert target.signal_id == signal_id
    assert target.parent_target_id == parent_id
    assert target.metadata == {"model": "equal_weight"}


def test_target_defaults_provenance_to_empty() -> None:
    target = Target.weight(AAPL, TIMESTAMP, Decimal("0.05"))

    assert target.reason is None
    assert target.signal_id is None
    assert target.parent_target_id is None
    assert target.metadata == {}


def test_multiple_targets_for_same_instrument_are_allowed() -> None:
    targets = [
        Target.weight(AAPL, TIMESTAMP, Decimal("0.10")),
        Target.flat(AAPL, TIMESTAMP, source=TargetSource.RISK_ADJUSTMENT),
    ]

    assert len(targets) == 2
    assert [target.kind for target in targets] == [TargetKind.WEIGHT, TargetKind.FLAT]
