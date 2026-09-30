"""Execution models.

Defines the ExecutionModel protocol and reference implementations:
- ExecutionModel: Protocol for converting targets into orders
- Immediate: Generate market orders for immediate execution
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from simulor.core.models import ExecutionModel
from simulor.logging import get_logger
from simulor.types import Instrument, OrderSide, OrderSpec, OrderType, Target, TargetKind, TimeInForce

# Create module logger
logger = get_logger(__name__)

__all__ = ["Immediate"]


@dataclass(frozen=True)
class _DesiredHolding:
    """Net desired quantity for one instrument, with its order provenance."""

    instrument: Instrument
    quantity: Decimal
    origin: Target | None  # Set only when a single target determined the holding


class Immediate(ExecutionModel):
    """Immediate execution via market orders with rebalancing controls.

    Generates market orders to reach desired holdings immediately. Targets are
    normalized into signed quantities and netted per instrument, then compared
    to current positions to create orders for the delta.

    A flat target overrides every other target for the same instrument; other
    targets for an instrument are summed.

    Supports multiple tolerance modes to prevent excessive trading:
    - min_shares: Minimum absolute share quantity to trade
    - min_notional: Minimum dollar value to trade
    - min_pct_change: Minimum percentage change in position

    Real-world constraints:
    - US stocks: Most brokers support fractional shares (0.000001 shares minimum)
    - Typical minimum notional: $1-5 per trade

    Example:
        Current: 100 shares AAPL @ $150
        Target: 150 shares AAPL
        With min_notional=$1000: Order BUY 50 shares ($7,500) is sent
        With min_notional=$10000: No order (delta only $7,500)
    """

    def __init__(
        self,
        min_shares: Decimal | None = None,
        min_notional: Decimal | None = None,
        min_pct_change: Decimal | None = None,
    ) -> None:
        """Initialize immediate execution model with rebalancing controls.

        Args:
            min_shares: Minimum absolute share quantity to trade (e.g., 1.0 for whole shares)
            min_notional: Minimum dollar value to trade (e.g., 100 for $100 minimum)
            min_pct_change: Minimum percentage change to trade (e.g., 0.02 for 2%)
        """
        if min_shares is not None and min_shares < 0:
            raise ValueError("min_shares must be non-negative")
        if min_notional is not None and min_notional < 0:
            raise ValueError("min_notional must be non-negative")
        if min_pct_change is not None and (min_pct_change < 0 or min_pct_change >= 1):
            raise ValueError("min_pct_change must be in [0, 1)")

        self.min_shares = min_shares
        self.min_notional = min_notional
        self.min_pct_change = min_pct_change

    def generate_orders(self, targets: list[Target]) -> list[OrderSpec]:
        """Generate market orders for immediate execution with rebalancing controls.

        Args:
            targets: Targets after risk management

        Returns:
            List of market orders to reach the desired holdings, filtered by tolerance settings
        """
        orders: list[OrderSpec] = []

        if not targets:
            return orders

        for holding in self._resolve_holdings(targets):
            order = self._order_for(holding)
            if order is not None:
                orders.append(order)

        return orders

    def _resolve_holdings(self, targets: list[Target]) -> list[_DesiredHolding]:
        """Normalize targets into one net desired quantity per instrument.

        Targets are grouped per instrument because execution must not assume
        that the framework has already merged them. A flat target overrides
        the other targets for its instrument, otherwise contributions add up.
        """
        grouped: dict[Instrument, list[Target]] = {}
        for target in targets:
            grouped.setdefault(target.instrument, []).append(target)

        holdings: list[_DesiredHolding] = []

        for instrument, group in grouped.items():
            flat_targets = [target for target in group if target.kind is TargetKind.FLAT]
            if flat_targets:
                # A flat instruction wins over every other target
                origin = flat_targets[0] if len(flat_targets) == 1 else None
                holdings.append(_DesiredHolding(instrument, Decimal("0"), origin))
                continue

            contributions: list[tuple[Target, Decimal]] = []
            for target in group:
                quantity = self._to_quantity(target)
                if quantity is not None:
                    contributions.append((target, quantity))

            if not contributions:
                continue

            total = Decimal("0")
            for _, quantity in contributions:
                total += quantity

            holdings.append(
                _DesiredHolding(instrument, total, contributions[0][0] if len(contributions) == 1 else None)
            )

        return holdings

    def _to_quantity(self, target: Target) -> Decimal | None:
        """Convert a target into a signed quantity, or None when it cannot be sized."""
        if target.kind is TargetKind.QUANTITY:
            return target.value

        if target.kind is TargetKind.FLAT:
            return Decimal("0")

        price = self._price_of(target.instrument)
        if price is None:
            return None

        magnitude = abs(target.value)

        if target.kind is TargetKind.WEIGHT:
            total_value = self.portfolio.total_value
            if total_value <= 0:
                logger.warning(
                    "Immediate: portfolio value is %s, skipping %s",
                    total_value,
                    target.instrument,
                )
                return None
            notional = magnitude * total_value
        else:
            # NOTIONAL targets are already expressed in currency
            notional = magnitude

        shares = notional // price
        return shares if target.value >= 0 else -shares

    def _order_for(self, holding: _DesiredHolding) -> OrderSpec | None:
        """Build the order that moves the current position to the desired holding."""
        instrument = holding.instrument
        current_position = self.portfolio.positions.get(instrument)
        current_quantity = current_position.quantity if current_position else Decimal("0")

        delta = holding.quantity - current_quantity

        # Skip if no change needed
        if delta == 0:
            return None

        if not self._passes_tolerances(instrument, delta, current_quantity):
            return None

        origin = holding.origin

        return OrderSpec(
            instrument=instrument,
            side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
            quantity=abs(delta),
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            reason=f"Rebalance to target: {holding.quantity}",
            target_id=origin.id if origin is not None else None,
            source=origin.source if origin is not None else None,
        )

    def _passes_tolerances(self, instrument: Instrument, delta: Decimal, current_quantity: Decimal) -> bool:
        """Check the rebalancing tolerance filters for a position change."""
        abs_delta = abs(delta)

        # Filter 1: Minimum shares
        if self.min_shares is not None and abs_delta < self.min_shares:
            logger.debug(
                "Immediate: skipping %s, delta %.2f < min_shares %.2f",
                instrument,
                abs_delta,
                self.min_shares,
            )
            return False

        # Filter 2: Minimum notional value
        if self.min_notional is not None:
            price = self._price_of(instrument)
            if price is None:
                return False
            notional = abs_delta * price
            if notional < self.min_notional:
                logger.debug(
                    "Immediate: skipping %s, notional $%.2f < min_notional $%.2f",
                    instrument,
                    notional,
                    self.min_notional,
                )
                return False

        # Filter 3: Minimum percentage change
        if self.min_pct_change is not None and current_quantity != 0:
            pct_change = abs_delta / abs(current_quantity)
            if pct_change < self.min_pct_change:
                logger.debug(
                    "Immediate: skipping %s, pct_change %.2f%% < min %.2f%%",
                    instrument,
                    pct_change * 100,
                    self.min_pct_change * 100,
                )
                return False

        return True

    def _price_of(self, instrument: Instrument) -> Decimal | None:
        """Return the latest market price, or None when the instrument cannot be priced."""
        price = self.market_store.get_latest_prices([instrument]).get(instrument)

        if price is None or price <= Decimal("0"):
            logger.warning("Immediate: no usable price for %s, skipping target", instrument)
            return None

        return price
