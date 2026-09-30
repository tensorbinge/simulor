"""Contract tests for the universe -> alpha -> construction -> risk -> execution pipeline.

The pipeline passes typed, immutable artifacts between stages:

    MarketEvent -> list[Signal] -> list[Target] -> list[Target] -> list[OrderSpec]

These tests pin the stage interfaces and the combination policy of the built-in
models (confidence-weighted signal netting, flat targets overriding others).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulor.alpha.models import MovingAverageCrossover
from simulor.alpha.signal import Signal, SignalDirection
from simulor.core.events import EventBus, MarketEvent
from simulor.core.models import Context, RiskModel
from simulor.data import MarketStore
from simulor.execution import Immediate
from simulor.portfolio import EqualWeight, Fund, Portfolio
from simulor.risk import PositionLimit
from simulor.types import (
    Instrument,
    OrderSide,
    Resolution,
    Target,
    TargetKind,
    TargetSource,
    TradeBar,
)

TIMESTAMP = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
AAPL = Instrument.stock("AAPL")
MSFT = Instrument.stock("MSFT")


def _portfolio(cash: str = "100000") -> Portfolio:
    return Portfolio(starting_cash=Decimal(cash))


def _context(market_store: MarketStore, portfolio: Portfolio | None = None) -> Context:
    return Context(
        market_store=market_store,
        portfolio=portfolio if portfolio is not None else _portfolio(),
        event_bus=EventBus(),
    )


def _bar_event(instrument: Instrument, close: str, timestamp: datetime = TIMESTAMP) -> MarketEvent:
    price = Decimal(close)
    event = MarketEvent(time=timestamp)
    event.add(
        TradeBar(
            instrument=instrument,
            timestamp=timestamp,
            resolution=Resolution.DAILY,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("1000"),
        )
    )
    return event


def _store(prices: dict[Instrument, str]) -> MarketStore:
    store = MarketStore()
    for instrument, close in prices.items():
        store.update(_bar_event(instrument, close))
    return store


def _seed_history(store: MarketStore, instrument: Instrument, closes: list[str]) -> MarketEvent:
    """Append one daily bar per close and return the latest event."""
    event = _bar_event(instrument, closes[0])
    for offset, close in enumerate(closes):
        event = _bar_event(instrument, close, TIMESTAMP + timedelta(days=offset))
        store.update(event)
    return event


def _signal(
    instrument: Instrument,
    direction: SignalDirection,
    strength: str,
    confidence: str,
) -> Signal:
    return Signal(
        instrument=instrument,
        timestamp=TIMESTAMP,
        direction=direction,
        strength=Decimal(strength),
        confidence=Decimal(confidence),
    )


def _by_instrument(targets: list[Target]) -> dict[Instrument, Target]:
    return {target.instrument: target for target in targets}


# ---------------------------------------------------------------------------
# AlphaModel: list[Signal]
# ---------------------------------------------------------------------------


def test_moving_average_crossover_returns_signal_list_from_trade_bars() -> None:
    store = MarketStore()
    event = _seed_history(store, AAPL, ["100", "101", "102", "103", "110"])

    alpha = MovingAverageCrossover(fast_period=2, slow_period=4)
    alpha.set_context(_context(store))

    signals = alpha.generate_signals(event)

    assert isinstance(signals, list)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.instrument == AAPL
    assert signal.direction is SignalDirection.LONG
    assert Decimal("0") < signal.strength <= Decimal("1")
    assert signal.timestamp == event.time


def test_moving_average_crossover_skips_instruments_without_enough_history() -> None:
    store = MarketStore()
    event = _seed_history(store, AAPL, ["100", "101"])

    alpha = MovingAverageCrossover(fast_period=2, slow_period=4)
    alpha.set_context(_context(store))

    assert alpha.generate_signals(event) == []


# ---------------------------------------------------------------------------
# PortfolioConstructionModel: list[Signal] -> list[Target]
# ---------------------------------------------------------------------------


def test_equal_weight_nets_signals_per_instrument_into_one_target() -> None:
    construction = EqualWeight()
    construction.set_context(_context(_store({AAPL: "100", MSFT: "50"})))

    signals = [
        _signal(AAPL, SignalDirection.LONG, "0.8", "1.0"),
        _signal(AAPL, SignalDirection.LONG, "0.5", "0.5"),
        _signal(MSFT, SignalDirection.SHORT, "0.4", "1.0"),
    ]

    targets = construction.create_targets(signals)

    assert len(targets) == 2
    by_instrument = _by_instrument(targets)

    aapl_target = by_instrument[AAPL]
    assert aapl_target.kind is TargetKind.QUANTITY
    assert aapl_target.value == Decimal("1000")  # 100000 / 100, only AAPL is active
    assert aapl_target.source is TargetSource.PORTFOLIO_CONSTRUCTION
    assert aapl_target.signal_id is None  # two signals contributed
    assert aapl_target.timestamp == TIMESTAMP

    assert by_instrument[MSFT].kind is TargetKind.FLAT


def test_equal_weight_nets_conflicting_signals_before_selecting_positions() -> None:
    construction = EqualWeight()
    construction.set_context(_context(_store({AAPL: "100"})))

    signals = [
        _signal(AAPL, SignalDirection.LONG, "0.2", "1.0"),
        _signal(AAPL, SignalDirection.SHORT, "0.6", "1.0"),
    ]

    targets = construction.create_targets(signals)

    # Net score is (0.2 - 0.6) / 2 = -0.2, so long-only construction stays flat.
    assert len(targets) == 1
    assert targets[0].kind is TargetKind.FLAT
    assert targets[0].value == Decimal("0")


def test_equal_weight_records_signal_provenance_for_single_signal() -> None:
    construction = EqualWeight()
    construction.set_context(_context(_store({AAPL: "100"})))
    signal = _signal(AAPL, SignalDirection.LONG, "0.8", "1.0")

    targets = construction.create_targets([signal])

    assert targets[0].signal_id == signal.id


def test_equal_weight_returns_empty_list_without_signals() -> None:
    construction = EqualWeight()
    construction.set_context(_context(_store({AAPL: "100"})))

    assert construction.create_targets([]) == []


# ---------------------------------------------------------------------------
# RiskModel: list[Target] -> list[Target]
# ---------------------------------------------------------------------------


def test_position_limit_scales_down_oversized_target_and_links_parent() -> None:
    risk = PositionLimit(max_position=Decimal("0.1"))
    risk.set_context(_context(_store({AAPL: "100"})))

    original = Target.quantity(AAPL, TIMESTAMP, Decimal("1000"))  # 100% of portfolio value
    adjusted = risk.adjust_targets([original])

    assert len(adjusted) == 1
    scaled = adjusted[0]
    assert scaled.kind is TargetKind.QUANTITY
    assert scaled.value == Decimal("100")
    assert scaled.source is TargetSource.RISK_ADJUSTMENT
    assert scaled.parent_target_id == original.id
    assert scaled.id != original.id


def test_position_limit_leaves_targets_within_limit_untouched() -> None:
    risk = PositionLimit(max_position=Decimal("0.1"))
    risk.set_context(_context(_store({AAPL: "100"})))

    original = Target.quantity(AAPL, TIMESTAMP, Decimal("50"))  # 5% of portfolio value
    adjusted = risk.adjust_targets([original])

    assert len(adjusted) == 1
    assert adjusted[0].value == Decimal("50")
    assert adjusted[0].source is TargetSource.PORTFOLIO_CONSTRUCTION


def test_position_limit_clamps_weight_targets_without_market_price() -> None:
    risk = PositionLimit(max_position=Decimal("0.25"))
    risk.set_context(_context(MarketStore()))

    adjusted = risk.adjust_targets([Target.weight(AAPL, TIMESTAMP, Decimal("0.60"))])

    assert len(adjusted) == 1
    assert adjusted[0].kind is TargetKind.WEIGHT
    assert adjusted[0].value == Decimal("0.25")
    assert adjusted[0].source is TargetSource.RISK_ADJUSTMENT


def test_position_limit_clamps_notional_targets() -> None:
    risk = PositionLimit(max_position=Decimal("0.1"))
    risk.set_context(_context(_store({AAPL: "100"})))

    adjusted = risk.adjust_targets([Target.notional(AAPL, TIMESTAMP, Decimal("50000"))])

    assert len(adjusted) == 1
    assert adjusted[0].value == Decimal("10000")  # 10% of 100000
    assert adjusted[0].source is TargetSource.RISK_ADJUSTMENT


def test_position_limit_preserves_flat_targets() -> None:
    risk = PositionLimit(max_position=Decimal("0.1"))
    risk.set_context(_context(_store({AAPL: "100"})))

    adjusted = risk.adjust_targets([Target.flat(AAPL, TIMESTAMP, source=TargetSource.RISK_ADJUSTMENT)])

    assert len(adjusted) == 1
    assert adjusted[0].kind is TargetKind.FLAT
    assert adjusted[0].value == Decimal("0")


def test_position_limit_removes_quantity_targets_without_market_price() -> None:
    risk = PositionLimit(max_position=Decimal("0.1"))
    risk.set_context(_context(MarketStore()))

    assert risk.adjust_targets([Target.quantity(AAPL, TIMESTAMP, Decimal("10"))]) == []


def test_position_limit_returns_empty_list_for_empty_input() -> None:
    risk = PositionLimit(max_position=Decimal("0.1"))
    risk.set_context(_context(_store({AAPL: "100"})))

    assert risk.adjust_targets([]) == []


class _HedgeRisk(RiskModel):
    """Risk model that bolts a hedge target onto the incoming target stream."""

    def __init__(self, hedge: Instrument, hedge_weight: Decimal) -> None:
        self.hedge = hedge
        self.hedge_weight = hedge_weight

    def adjust_targets(self, targets: list[Target]) -> list[Target]:
        if not targets:
            return []

        hedge = Target.weight(
            self.hedge,
            targets[0].timestamp,
            self.hedge_weight,
            source=TargetSource.RISK_ADJUSTMENT,
            reason="index hedge",
        )
        return [*targets, hedge]


def test_risk_model_can_add_hedge_targets_that_execution_executes() -> None:
    qqq = Instrument.stock("QQQ")
    store = _store({AAPL: "100", qqq: "300"})

    risk = _HedgeRisk(qqq, Decimal("-0.05"))
    risk.set_context(_context(store))

    approved = risk.adjust_targets([Target.quantity(AAPL, TIMESTAMP, Decimal("100"))])

    assert len(approved) == 2

    orders = _execution(store).generate_orders(approved)
    by_instrument = {order.instrument: order for order in orders}

    assert by_instrument[AAPL].side is OrderSide.BUY
    assert by_instrument[AAPL].quantity == Decimal("100")

    hedge_order = by_instrument[qqq]
    assert hedge_order.side is OrderSide.SELL
    assert hedge_order.quantity == Decimal("16")  # 5% of 100000 at $300
    assert hedge_order.source is TargetSource.RISK_ADJUSTMENT
    assert hedge_order.target_id == approved[1].id


# ---------------------------------------------------------------------------
# ExecutionModel: list[Target] -> list[OrderSpec]
# ---------------------------------------------------------------------------


def _execution(store: MarketStore, **kwargs: Decimal | None) -> Immediate:
    execution = Immediate(**kwargs)
    execution.set_context(_context(store))
    return execution


def test_immediate_nets_multiple_targets_per_instrument() -> None:
    orders = _execution(_store({AAPL: "100"})).generate_orders(
        [
            Target.quantity(AAPL, TIMESTAMP, Decimal("100")),
            Target.quantity(AAPL, TIMESTAMP, Decimal("-30")),
        ]
    )

    assert len(orders) == 1
    assert orders[0].side is OrderSide.BUY
    assert orders[0].quantity == Decimal("70")
    assert orders[0].target_id is None  # netted from several targets


def test_immediate_records_single_target_provenance() -> None:
    target = Target.quantity(AAPL, TIMESTAMP, Decimal("100"))

    orders = _execution(_store({AAPL: "100"})).generate_orders([target])

    assert len(orders) == 1
    assert orders[0].target_id == target.id
    assert orders[0].source is TargetSource.PORTFOLIO_CONSTRUCTION


def test_immediate_converts_weight_target_into_quantity() -> None:
    orders = _execution(_store({AAPL: "100"})).generate_orders([Target.weight(AAPL, TIMESTAMP, Decimal("0.10"))])

    assert len(orders) == 1
    assert orders[0].quantity == Decimal("100")  # 10% of 100000 at $100
    assert orders[0].target_id is not None


def test_immediate_converts_notional_target_into_quantity() -> None:
    orders = _execution(_store({AAPL: "100"})).generate_orders([Target.notional(AAPL, TIMESTAMP, Decimal("2500"))])

    assert len(orders) == 1
    assert orders[0].quantity == Decimal("25")


def test_immediate_flat_target_overrides_other_targets() -> None:
    portfolio = _portfolio()
    portfolio.seed_position(AAPL, Decimal("100"), Decimal("100"))
    execution = Immediate()
    execution.set_context(_context(_store({AAPL: "100"}), portfolio))

    orders = execution.generate_orders(
        [
            Target.quantity(AAPL, TIMESTAMP, Decimal("100")),
            Target.flat(AAPL, TIMESTAMP, source=TargetSource.LIQUIDATION),
        ]
    )

    assert len(orders) == 1
    assert orders[0].side is OrderSide.SELL
    assert orders[0].quantity == Decimal("100")
    assert orders[0].source is TargetSource.LIQUIDATION


def test_immediate_applies_tolerances_to_netted_target() -> None:
    execution = _execution(_store({AAPL: "100"}), min_notional=Decimal("10000"))

    orders = execution.generate_orders(
        [
            Target.quantity(AAPL, TIMESTAMP, Decimal("100")),
            Target.quantity(AAPL, TIMESTAMP, Decimal("-30")),
        ]
    )

    assert orders == []  # 70 shares * $100 = $7000 < $10000


def test_immediate_generates_orders_for_several_instruments() -> None:
    orders = _execution(_store({AAPL: "100", MSFT: "50"})).generate_orders(
        [
            Target.quantity(AAPL, TIMESTAMP, Decimal("10")),
            Target.quantity(MSFT, TIMESTAMP, Decimal("-20")),
        ]
    )

    by_instrument = {order.instrument: order for order in orders}
    assert by_instrument[AAPL].side is OrderSide.BUY
    assert by_instrument[AAPL].quantity == Decimal("10")
    assert by_instrument[MSFT].side is OrderSide.SELL
    assert by_instrument[MSFT].quantity == Decimal("20")


def test_immediate_returns_empty_list_without_targets() -> None:
    assert _execution(_store({AAPL: "100"})).generate_orders([]) == []


# ---------------------------------------------------------------------------
# Engine: full pipeline end to end
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_engine_runs_pipeline_with_list_contracts() -> None:
    from simulor.data import CsvFeed
    from simulor.engine import Engine
    from simulor.execution import SimulatedBroker
    from simulor.strategy import Strategy
    from simulor.universe import Static

    data_path = Path(__file__).resolve().parents[1] / "examples" / "data" / "daily_trade_bars.csv"
    instrument = Instrument.stock("AAPL")

    strategy = Strategy(
        name="contract_smoke",
        universe=Static([instrument]),
        alpha=MovingAverageCrossover(fast_period=5, slow_period=20),
        construction=EqualWeight(),
        risk=PositionLimit(max_position=Decimal("0.4")),
        execution=Immediate(min_notional=Decimal("1000")),
    )

    engine = Engine(
        data=CsvFeed(path=data_path, resolution=Resolution.DAILY),
        fund=Fund(strategies=[strategy], capital=Decimal("100000")),
        broker=SimulatedBroker(),
    )

    result = engine.run(start="2024-01-02 00:00:00", end="2024-12-31 23:59:59", mode="backtest")

    assert result.trades, "pipeline produced no trades"
    assert result.initial_capital == Decimal("100000")
