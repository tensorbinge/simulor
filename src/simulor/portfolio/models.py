"""Portfolio construction model implementations.

Reference implementations of PortfolioConstructionModel:
- EqualWeight: Equal allocation across signals
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from simulor.core.models import PortfolioConstructionModel
from simulor.logging import get_logger
from simulor.types import Instrument, Target

if TYPE_CHECKING:
    from simulor.alpha.signal import Signal

# Create module logger
logger = get_logger(__name__)

__all__ = ["PositionType", "EqualWeight"]


class PositionType(Enum):
    """Type of positions allowed in portfolio construction."""

    LONG_SHORT = "long_short"  # Allow both long and short positions
    LONG_ONLY = "long_only"  # Only long positions
    SHORT_ONLY = "short_only"  # Only short positions


@dataclass(frozen=True)
class _NettedSignal:
    """Signals for a single instrument collapsed into one view."""

    instrument: Instrument
    timestamp: datetime
    score: Decimal  # Confidence-weighted mean signed strength
    signal_id: UUID | None  # Set only when a single signal fed this view


def _net_signals(signals: list[Signal]) -> dict[Instrument, _NettedSignal]:
    """Collapse signals per instrument into a confidence-weighted score.

    The score is the confidence-weighted mean of each signal's signed strength,
    so one high-conviction view is not drowned out by many low-conviction ones.
    Signals carrying no confidence net to a neutral score.

    Combining several signals for the same instrument is the policy of this
    model, not of the framework: callers who want a different rule should
    provide their own portfolio construction model.
    """
    weighted: dict[Instrument, Decimal] = {}
    confidence: dict[Instrument, Decimal] = {}
    timestamps: dict[Instrument, datetime] = {}
    counts: dict[Instrument, int] = {}
    latest_id: dict[Instrument, UUID] = {}

    for signal in signals:
        instrument = signal.instrument
        weighted[instrument] = weighted.get(instrument, Decimal("0")) + signal.signed_strength * signal.confidence
        confidence[instrument] = confidence.get(instrument, Decimal("0")) + signal.confidence
        if instrument not in timestamps or signal.timestamp > timestamps[instrument]:
            timestamps[instrument] = signal.timestamp
        counts[instrument] = counts.get(instrument, 0) + 1
        latest_id[instrument] = signal.id

    netted: dict[Instrument, _NettedSignal] = {}
    for instrument, total_weight in weighted.items():
        total_confidence = confidence[instrument]
        netted[instrument] = _NettedSignal(
            instrument=instrument,
            timestamp=timestamps[instrument],
            score=total_weight / total_confidence if total_confidence > 0 else Decimal("0"),
            signal_id=latest_id[instrument] if counts[instrument] == 1 else None,
        )

    return netted


class EqualWeight(PortfolioConstructionModel):
    """Equal weight allocation across signals.

    Signals are first netted per instrument into a single confidence-weighted
    score (see `_net_signals`). Each active instrument then receives an equal
    share of usable capital.

    Logic for the netted score:
    - Score > 0: Enter Long (or rebalance Long)
    - Score < 0: Enter Short (or rebalance Short)
    - Score == 0:
        - If position held: HOLD (Keep in portfolio, rebalance to 1/N weight)
        - If no position: IGNORE (Do not enter)

    Instruments that are not active receive a flat target so that stale
    positions are closed.

    A reserve percentage (default 0%) is held back to account for
    transaction costs, rounding, and market impact.
    """

    def __init__(
        self,
        reserve_pct: Decimal = Decimal("0.0"),
        position_type: PositionType = PositionType.LONG_ONLY,
    ) -> None:
        """Initialize equal weight constructor.

        Args:
            reserve_pct: Percentage of portfolio value to reserve for
                transaction costs and rounding (default: 0.0 = no reserve)
            position_type: Type of positions allowed (default: LONG_ONLY)

        Raises:
            ValueError: If reserve_pct not in [0, 1)
        """
        if not 0 <= reserve_pct < 1:
            raise ValueError(f"reserve_pct must be in [0, 1), got {reserve_pct}")

        self.reserve_pct = reserve_pct
        self.position_type = position_type

    def create_targets(self, signals: list[Signal]) -> list[Target]:
        """Create equal-weighted targets from signals.

        Args:
            signals: Trading signals from alpha models

        Returns:
            One target per signaled instrument, expressed as a quantity.
            Instruments without an active allocation receive a flat target.
        """
        if not signals:
            return []

        netted = _net_signals(signals)
        current_positions = self.portfolio.positions

        # 1. Identify "Active" Instruments (The 'N' in 1/N)
        active: set[Instrument] = set()
        for instrument, view in netted.items():
            position = current_positions.get(instrument)
            is_held = position is not None and position.quantity != 0
            if self._is_active(view.score, is_held):
                active.add(instrument)

        # 2. Handle Liquidation (If everything is inactive, close every signaled instrument)
        if not active:
            logger.debug("EqualWeight: no active instruments, flattening %d signaled instruments", len(netted))
            return [
                Target.flat(
                    view.instrument,
                    view.timestamp,
                    signal_id=view.signal_id,
                    reason="no active allocation",
                )
                for view in netted.values()
            ]

        # 3. Calculate Allocation
        available_capital = self.portfolio.total_value
        usable_capital = available_capital * (1 - self.reserve_pct)

        # Divide by active count only, preventing cash drag
        capital_per_position = usable_capital / len(active)

        logger.debug(
            "EqualWeight: allocating $%.2f per position (%d active, $%.2f usable)",
            capital_per_position,
            len(active),
            usable_capital,
        )

        # 4. Generate Targets
        targets: list[Target] = []

        for instrument, view in netted.items():
            # If not active (e.g. Sell signal in LongOnly, or 0 score and not held), close it.
            if instrument not in active:
                targets.append(
                    Target.flat(
                        instrument,
                        view.timestamp,
                        signal_id=view.signal_id,
                        reason=f"inactive for {self.position_type.value}",
                    )
                )
                continue

            current_price = self.market_store.get_latest_price(instrument)

            # Sanity check for bad data
            if current_price <= 0:
                logger.warning("EqualWeight: price for %s is %s, forcing flat", instrument, current_price)
                targets.append(
                    Target.flat(
                        instrument,
                        view.timestamp,
                        signal_id=view.signal_id,
                        reason="invalid market price",
                    )
                )
                continue

            # Calculate Base Shares (Magnitude)
            # TODO: Add logic here for AssetType to switch between // and /
            # Currently using floor division (integer shares) to match stock logic
            base_shares = capital_per_position // current_price

            position = current_positions.get(instrument)
            current_quantity = position.quantity if position is not None else Decimal("0")
            target_sign = self._target_sign(view.score, current_quantity)

            targets.append(
                Target.quantity(
                    instrument,
                    view.timestamp,
                    base_shares * target_sign,
                    signal_id=view.signal_id,
                    reason=f"equal weight allocation from score {view.score}",
                )
            )

        return targets

    def _is_active(self, score: Decimal, is_held: bool) -> bool:
        """Check whether an instrument receives an allocation slot."""
        if self.position_type is PositionType.LONG_ONLY:
            # Active if: Long score OR (Neutral and already held)
            return score > 0 or (score == 0 and is_held)
        if self.position_type is PositionType.SHORT_ONLY:
            # Active if: Short score OR (Neutral and already held)
            return score < 0 or (score == 0 and is_held)
        # Active if: Directional score OR (Neutral and already held)
        return score != 0 or (score == 0 and is_held)

    def _target_sign(self, score: Decimal, current_quantity: Decimal) -> Decimal:
        """Determine the sign of the target quantity for an active instrument."""
        if score == 0:
            # Neutral score keeps the direction of the existing holding
            return Decimal("1") if current_quantity > 0 else Decimal("-1")

        if self.position_type is PositionType.SHORT_ONLY:
            return Decimal("-1")
        if self.position_type is PositionType.LONG_SHORT:
            return Decimal("1") if score > 0 else Decimal("-1")
        return Decimal("1")  # LONG_ONLY
