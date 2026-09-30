"""Contract tests for OrderSpec provenance fields."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulor.types import Instrument, OrderSide, OrderSpec, OrderType, TargetSource, TimeInForce

AAPL = Instrument.stock("AAPL")


def _market_order(**overrides: object) -> OrderSpec:
    fields: dict[str, object] = {
        "instrument": AAPL,
        "side": OrderSide.BUY,
        "quantity": Decimal("10"),
        "order_type": OrderType.MARKET,
        "time_in_force": TimeInForce.DAY,
    }
    fields.update(overrides)
    return OrderSpec(**fields)  # type: ignore[arg-type]


def test_order_spec_defaults_provenance_to_none() -> None:
    order = _market_order()

    assert order.target_id is None
    assert order.source is None
    assert order.reason is None


def test_order_spec_carries_target_provenance() -> None:
    target_id = uuid4()

    order = _market_order(
        target_id=target_id,
        source=TargetSource.RISK_ADJUSTMENT,
        reason="rebalance to target",
    )

    assert order.target_id == target_id
    assert isinstance(order.target_id, UUID)
    assert order.source is TargetSource.RISK_ADJUSTMENT
    assert order.reason == "rebalance to target"


def test_order_spec_still_validates_order_type_requirements() -> None:
    with pytest.raises(ValueError, match="limit_price"):
        _market_order(order_type=OrderType.LIMIT)

    with pytest.raises(ValueError, match="positive"):
        _market_order(quantity=Decimal("0"))
