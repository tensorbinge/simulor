"""Risk management model implementations.

Reference implementations:
- PositionLimit: Max position size limits
- StopLoss: Automatic stop-loss placement
- LeverageLimit: Maximum leverage constraints
- DrawdownLimit: Reduce exposure on drawdown
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from simulor.core.models import RiskModel
from simulor.logging import get_logger
from simulor.types import Target, TargetKind, TargetSource

# Create module logger
logger = get_logger(__name__)

__all__ = ["PositionLimit"]


class PositionLimit(RiskModel):
    """Position size limit risk model.

    Enforces maximum position size as a percentage of total portfolio value.
    Targets that exceed the limit are replaced by scaled-down targets that
    preserve direction (long/short) and relative strength. Targets that
    respect the limit pass through unchanged.

    Targets are measured in their own unit:
    - quantity targets are valued at the latest market price
    - weight targets are compared directly against the limit
    - notional targets are compared against the limit in currency terms
    - flat targets always pass through

    Example:
        >>> risk = PositionLimit(max_position=Decimal("0.1"))  # 10% max per position
        >>> targets = [Target.quantity(aapl, now, Decimal("1000"))]  # Worth 15% of portfolio
        >>> adjusted = risk.adjust_targets(targets)  # Scaled down to 10%
    """

    def __init__(self, max_position: Decimal) -> None:
        """Initialize position limit risk model.

        Args:
            max_position: Maximum position size as fraction of portfolio value
        """
        if max_position <= Decimal("0") or max_position > Decimal("1"):
            raise ValueError("max_position must be between 0 and 1")

        self.max_position = max_position

    def adjust_targets(self, targets: list[Target]) -> list[Target]:
        """Apply position size limits to targets.

        Args:
            targets: Targets from portfolio construction

        Returns:
            Targets scaled down to the maximum position size. Targets within
            the limit are returned unchanged with their original provenance.
            Quantity targets without a usable market price are removed.
        """
        if not targets:
            return []

        # Get total portfolio value for percentage calculations
        total_value = self.portfolio.total_value

        if total_value <= 0:
            # No capital, can't take positions
            return []

        adjusted: list[Target] = []

        for target in targets:
            scaled_value = self._scaled_value(target, total_value)

            if scaled_value is None:
                # Target cannot be sized, remove it
                continue

            if scaled_value == target.value:
                # Within limits, keep as-is
                adjusted.append(target)
                continue

            logger.debug(
                "PositionLimit: scaled down %s from %s to %s",
                target.instrument,
                target.value,
                scaled_value,
            )

            adjusted.append(
                replace(
                    target,
                    id=uuid4(),
                    value=scaled_value,
                    source=TargetSource.RISK_ADJUSTMENT,
                    parent_target_id=target.id,
                    reason=f"scaled to respect max_position {self.max_position}",
                    metadata={**target.metadata, "original_value": target.value},
                )
            )

        return adjusted

    def _scaled_value(self, target: Target, total_value: Decimal) -> Decimal | None:
        """Return the value the target may keep, or None if it cannot be sized."""
        if target.kind is TargetKind.FLAT:
            return target.value

        if target.kind is TargetKind.WEIGHT:
            return self._clamp(target.value, self.max_position)

        if target.kind is TargetKind.NOTIONAL:
            return self._clamp(target.value, self.max_position * total_value)

        # Quantity targets need a price to be valued
        price = self.market_store.get_latest_prices([target.instrument]).get(target.instrument)

        if price is None or price <= Decimal("0"):
            logger.warning("PositionLimit: no usable price for %s, removing target", target.instrument)
            return None

        position_pct = abs(target.value * price) / total_value

        if position_pct <= self.max_position:
            return target.value

        # Scale factor to bring the position down to max_position
        scale_factor = self.max_position / position_pct
        return target.value * scale_factor // Decimal("1")

    @staticmethod
    def _clamp(value: Decimal, limit: Decimal) -> Decimal:
        """Clamp a value to +/- limit while preserving its sign."""
        if abs(value) <= limit:
            return value
        return limit if value > 0 else -limit
