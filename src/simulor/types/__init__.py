"""Pure type definitions with zero dependencies.

This package contains all fundamental types used throughout Simulor.
It has NO dependencies on other simulor packages to prevent circular imports.

Types exported:
- Instrument identification: Instrument, AssetType, OptionType
- Market data: MarketData, TradeBar, TradeTick, QuoteBar, QuoteTick
- Orders: OrderSide, TimeInForce
- Targets: Target, TargetKind, TargetSource
- Time: Resolution, ColumnName
- Enums: TickDirection
"""

from __future__ import annotations

from simulor.types.common import AssetType, ColumnName, OptionType, OrderSide, Resolution, TickDirection, TimeInForce
from simulor.types.instruments import Instrument
from simulor.types.market_data import MarketData, QuoteBar, QuoteTick, TradeBar, TradeTick
from simulor.types.orders import Fill, OrderSpec, OrderType
from simulor.types.target import Target, TargetKind, TargetSource

__all__ = [
    # Instruments
    "Instrument",
    "AssetType",
    "OptionType",
    # Market Data
    "MarketData",
    "TradeBar",
    "TradeTick",
    "QuoteBar",
    "QuoteTick",
    # Orders
    "OrderSide",
    "TimeInForce",
    "OrderType",
    "OrderSpec",
    "Fill",
    # Targets
    "Target",
    "TargetKind",
    "TargetSource",
    # Time & Columns
    "Resolution",
    "ColumnName",
    # Enums
    "TickDirection",
]
