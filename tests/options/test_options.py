from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from simulor.core.events import EventBus, MarketEvent
from simulor.data.providers.csv import CSVDataProvider
from simulor.execution.simulation.broker import SimulatedBroker
from simulor.portfolio.manager import Portfolio
from simulor.types import (
    AssetType,
    Fill,
    Instrument,
    OptionType,
    OrderSide,
    OrderSpec,
    OrderType,
    Resolution,
    TimeInForce,
    TradeBar,
)


def _setup_broker() -> SimulatedBroker:
    broker = SimulatedBroker()
    broker.initialize(
        event_bus=EventBus(), global_portfolio=Portfolio(starting_cash=Decimal("100000")), strategy_portfolios={}
    )
    broker.connect()
    return broker


def _broker_with_strategy(cash: Decimal) -> SimulatedBroker:
    broker = _setup_broker()
    broker.register_strategy("s1", cash)
    return broker


def test_option_instrument_factory_sets_defaults() -> None:
    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )

    assert option.asset_type == AssetType.OPTION
    assert option.option_type == OptionType.CALL
    assert option.strike == Decimal("150")
    assert option.expiry == datetime(2024, 1, 19)
    assert option.contract_size == Decimal("100")
    assert option.symbol == "AAPL240119C00150000"


def test_option_instruments_use_series_identity() -> None:
    same_series_a = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
        exchange="NASDAQ",
    )
    same_series_b = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
        exchange="NASDAQ",
    )
    different_strike = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("155"),
        option_type=OptionType.CALL,
        exchange="NASDAQ",
    )

    assert same_series_a == same_series_b
    assert hash(same_series_a) == hash(same_series_b)
    assert same_series_a != different_strike


def test_stock_instrument_rejects_option_fields() -> None:
    with pytest.raises(ValueError, match="Strike price is only valid for options"):
        Instrument(
            symbol="AAPL",
            asset_type=AssetType.STOCK,
            strike=Decimal("100"),
        )


def test_future_asset_type_still_not_supported() -> None:
    with pytest.raises(NotImplementedError, match="not yet supported"):
        Instrument(
            symbol="ESZ24",
            asset_type=AssetType.FUTURE,
        )


def test_csv_provider_parses_occ_option_symbol(tmp_path: Path) -> None:
    csv_path = tmp_path / "options.csv"
    csv_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2024-01-02 09:30:00,AAPL240119C00150000,5.00,5.50,4.90,5.20,1000\n",
        encoding="utf-8",
    )

    provider = CSVDataProvider(path=csv_path, resolution=Resolution.DAILY)
    event = next(iter(provider))
    (instrument,) = tuple(event.instruments())

    assert instrument.asset_type == AssetType.OPTION
    assert instrument.option_type == OptionType.CALL
    assert instrument.strike == Decimal("150")
    assert instrument.contract_size == Decimal("100")


def test_csv_provider_explicit_option_type_overrides_inferred(tmp_path: Path) -> None:
    csv_path = tmp_path / "override.csv"
    csv_path.write_text(
        "timestamp,symbol,instrument_type,open,high,low,close,volume\n"
        "2024-01-02 09:30:00,AAPL240119P00150000,OPTION,5.00,5.50,4.90,5.20,1000\n",
        encoding="utf-8",
    )

    provider = CSVDataProvider(path=csv_path, resolution=Resolution.DAILY)
    event = next(iter(provider))
    (instrument,) = tuple(event.instruments())

    assert instrument.asset_type == AssetType.OPTION
    assert instrument.option_type == OptionType.PUT


def test_option_fill_uses_contract_multiplier_for_cash_and_value() -> None:
    portfolio = Portfolio(starting_cash=Decimal("10000"))
    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )

    fill = Fill(
        instrument=option,
        quantity=Decimal("1"),
        price=Decimal("2"),
        commission=Decimal("1"),
    )
    portfolio.update_position(fill)

    assert portfolio.cash == Decimal("9799")

    pos = portfolio.positions[option]
    assert pos.quantity == Decimal("1")
    pos.current_price = Decimal("3")

    assert pos.market_value == Decimal("300")
    assert portfolio.total_value == Decimal("10099")


def test_option_sell_increases_cash_with_multiplier() -> None:
    portfolio = Portfolio(starting_cash=Decimal("1000"))
    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("140"),
        option_type=OptionType.PUT,
    )

    fill = Fill(
        instrument=option,
        quantity=Decimal("-2"),
        price=Decimal("1.50"),
        commission=Decimal("0"),
    )
    portfolio.update_position(fill)

    assert portfolio.cash == Decimal("1300")
    assert portfolio.positions[option].quantity == Decimal("-2")


def test_get_security_positions_aggregates_options_and_stocks() -> None:
    broker = _setup_broker()
    broker.register_strategy("s1", Decimal("50000"))
    broker.register_strategy("s2", Decimal("50000"))

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )
    stock = Instrument.stock("AAPL")

    s1 = broker.strategy_portfolios["s1"]
    s2 = broker.strategy_portfolios["s2"]

    s1.seed_position(option, Decimal("1"), Decimal("2"))
    s2.seed_position(option, Decimal("2"), Decimal("2.5"))
    s1.seed_position(stock, Decimal("10"), Decimal("150"))

    s1.mark_to_market({option: Decimal("3"), stock: Decimal("151")})
    s2.mark_to_market({option: Decimal("3")})

    positions = broker.get_security_positions()

    assert len(positions) == 2

    by_symbol = {p.instrument.symbol: p for p in positions}
    assert by_symbol[option.symbol].quantity == Decimal("3")
    assert by_symbol[option.symbol].cost_price == Decimal("7") / Decimal("3")
    assert by_symbol[stock.symbol].quantity == Decimal("10")


def test_option_buy_cash_check_uses_contract_size() -> None:
    broker = _setup_broker()
    broker.register_strategy("s1", Decimal("250"))

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )

    order = OrderSpec(
        instrument=option,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    broker.submit_order("s1", order)

    event = MarketEvent(time=datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("UTC")))
    event.add(
        TradeBar(
            timestamp=event.time,
            instrument=option,
            resolution=Resolution.MINUTE,
            open=Decimal("3"),
            high=Decimal("3"),
            low=Decimal("3"),
            close=Decimal("3"),
            volume=Decimal("100"),
        )
    )

    with pytest.raises(ValueError, match="Insufficient cash"):
        broker.on_market_event(event)


def test_short_option_margin_rejects_when_cash_too_low() -> None:
    broker = SimulatedBroker(short_option_margin_ratio=Decimal("0.20"))
    broker.initialize(
        event_bus=EventBus(), global_portfolio=Portfolio(starting_cash=Decimal("100000")), strategy_portfolios={}
    )
    broker.connect()
    broker.register_strategy("s1", Decimal("30"))

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )
    order = OrderSpec(
        instrument=option,
        side=OrderSide.SELL,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    broker.submit_order("s1", order)

    event = MarketEvent(time=datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("UTC")))
    event.add(
        TradeBar(
            timestamp=event.time,
            instrument=option,
            resolution=Resolution.MINUTE,
            open=Decimal("2"),
            high=Decimal("2"),
            low=Decimal("2"),
            close=Decimal("2"),
            volume=Decimal("100"),
        )
    )

    with pytest.raises(ValueError, match="short option margin"):
        broker.on_market_event(event)


def test_short_option_margin_allows_open_when_cash_sufficient() -> None:
    broker = SimulatedBroker(short_option_margin_ratio=Decimal("0.20"))
    broker.initialize(
        event_bus=EventBus(), global_portfolio=Portfolio(starting_cash=Decimal("100000")), strategy_portfolios={}
    )
    broker.connect()
    broker.register_strategy("s1", Decimal("50"))

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )
    order = OrderSpec(
        instrument=option,
        side=OrderSide.SELL,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    broker.submit_order("s1", order)

    event = MarketEvent(time=datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("UTC")))
    event.add(
        TradeBar(
            timestamp=event.time,
            instrument=option,
            resolution=Resolution.MINUTE,
            open=Decimal("2"),
            high=Decimal("2"),
            low=Decimal("2"),
            close=Decimal("2"),
            volume=Decimal("100"),
        )
    )

    broker.on_market_event(event)

    assert broker.strategy_portfolios["s1"].cash == Decimal("250")
    assert broker.strategy_portfolios["s1"].positions[option].quantity == Decimal("-1")


def test_expired_itm_long_call_physically_settles_to_underlying() -> None:
    broker = _broker_with_strategy(Decimal("20000"))
    portfolio = broker.strategy_portfolios["s1"]

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )
    portfolio.seed_position(option, quantity=Decimal("1"), average_cost=Decimal("2"))

    underlying = Instrument.stock("AAPL")
    event = MarketEvent(time=datetime(2024, 1, 19, 20, 0, tzinfo=ZoneInfo("UTC")))
    event.add(
        TradeBar(
            timestamp=event.time,
            instrument=underlying,
            resolution=Resolution.DAILY,
            open=Decimal("160"),
            high=Decimal("160"),
            low=Decimal("160"),
            close=Decimal("160"),
            volume=Decimal("1000"),
        )
    )

    broker.on_market_event(event)

    positions = broker.get_security_positions()
    assert len(positions) == 1
    assert positions[0].instrument == underlying
    assert positions[0].quantity == Decimal("100")
    assert positions[0].cost_price == Decimal("150")

    assert portfolio.cash == Decimal("5000")


def test_expired_otm_long_call_expires_worthless() -> None:
    broker = _broker_with_strategy(Decimal("5000"))
    portfolio = broker.strategy_portfolios["s1"]

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 19),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )
    portfolio.seed_position(option, quantity=Decimal("1"), average_cost=Decimal("2"))

    underlying = Instrument.stock("AAPL")
    event = MarketEvent(time=datetime(2024, 1, 19, 20, 0, tzinfo=ZoneInfo("UTC")))
    event.add(
        TradeBar(
            timestamp=event.time,
            instrument=underlying,
            resolution=Resolution.DAILY,
            open=Decimal("140"),
            high=Decimal("140"),
            low=Decimal("140"),
            close=Decimal("140"),
            volume=Decimal("1000"),
        )
    )

    broker.on_market_event(event)

    assert broker.get_security_positions() == []
    assert portfolio.cash == Decimal("5000")


def test_early_exercise_disabled_by_default() -> None:
    broker = _broker_with_strategy(Decimal("20000"))
    portfolio = broker.strategy_portfolios["s1"]

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 31),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )
    portfolio.seed_position(option, quantity=Decimal("1"), average_cost=Decimal("2"))

    underlying = Instrument.stock("AAPL")
    event = MarketEvent(time=datetime(2024, 1, 10, 20, 0, tzinfo=ZoneInfo("UTC")))
    event.add(
        TradeBar(
            timestamp=event.time,
            instrument=underlying,
            resolution=Resolution.DAILY,
            open=Decimal("170"),
            high=Decimal("170"),
            low=Decimal("170"),
            close=Decimal("170"),
            volume=Decimal("1000"),
        )
    )

    broker.on_market_event(event)

    assert option in portfolio.positions
    assert portfolio.positions[option].quantity == Decimal("1")
    assert broker.get_security_positions()[0].instrument == option


def test_enabled_early_exercise_settles_itm_option_pre_expiry() -> None:
    broker = SimulatedBroker(enable_early_exercise=True, early_exercise_intrinsic_threshold=Decimal("5"))
    broker.initialize(
        event_bus=EventBus(), global_portfolio=Portfolio(starting_cash=Decimal("100000")), strategy_portfolios={}
    )
    broker.connect()
    broker.register_strategy("s1", Decimal("20000"))
    portfolio = broker.strategy_portfolios["s1"]

    option = Instrument.option(
        underlying="AAPL",
        expiry=datetime(2024, 1, 31),
        strike=Decimal("150"),
        option_type=OptionType.CALL,
    )
    portfolio.seed_position(option, quantity=Decimal("1"), average_cost=Decimal("2"))

    underlying = Instrument.stock("AAPL")
    event = MarketEvent(time=datetime(2024, 1, 10, 20, 0, tzinfo=ZoneInfo("UTC")))
    event.add(
        TradeBar(
            timestamp=event.time,
            instrument=underlying,
            resolution=Resolution.DAILY,
            open=Decimal("170"),
            high=Decimal("170"),
            low=Decimal("170"),
            close=Decimal("170"),
            volume=Decimal("1000"),
        )
    )

    broker.on_market_event(event)

    positions = broker.get_security_positions()
    assert len(positions) == 1
    assert positions[0].instrument == underlying
    assert positions[0].quantity == Decimal("100")
    assert positions[0].cost_price == Decimal("150")
    assert portfolio.cash == Decimal("5000")
