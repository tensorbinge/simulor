# Execution

The execution module is responsible for transforming strategy trading decisions into simulated or live order executions. It provides realistic order lifecycle modeling, transaction cost simulation, and a unified API that works identically across backtesting, paper trading, and live trading environments.

## Table of Contents

- [Design Philosophy](#design-philosophy)
- [Core Responsibilities](#core-responsibilities)
- [Architecture Overview](#architecture-overview)
- [Order Types & Lifecycle](#order-types--lifecycle)
  - [Supported Order Types](#supported-order-types)
  - [Order Lifecycle States](#order-lifecycle-states)
  - [Order Modifications](#order-modifications)
- [Fill Models](#fill-models)
  - [InstantFillModel](#instantfillmodel-default---fast)
  - [SpreadFillModel](#spreadfillmodel-default--spread)
  - [TradeTapeMatchModel](#tradetapematchmodel-realistic---medium-speed)
  - [L2MatchingModel](#l2matchingmodel-most-realistic---requires-l2-data)
  - [ProbabilisticFillModel](#probabilisticfillmodel-for-sparse-data)
  - [Custom Fill Models](#custom-fill-models)
- [Transaction Cost Modeling](#transaction-cost-modeling)
  - [Explicit Costs (Fees)](#explicit-costs-fees)
  - [Implicit Costs (Spread, Slippage, Impact)](#implicit-costs-spread-slippage-impact)
- [Cash Settlement & Lifecycle](#cash-settlement--lifecycle)
- [Latency Modeling](#latency-modeling)
- [User-Facing API](#user-facing-api)
- [Multi-Venue Execution](#multi-venue-execution)
- [Execution Diagnostics & Persistence](#execution-diagnostics--persistence)
- [Configuration Presets](#configuration-presets)
- [Production Deployment (Backtest → Live)](#production-deployment-backtest--live)
- [Design Principles Summary](#design-principles-summary)
- [Future Extensions](#future-extensions)

---

## Design Philosophy

Simulor's execution layer differentiates itself from traditional backtest frameworks through:

**Realism with Flexibility**: Provide fast, deterministic default models for rapid iteration alongside pluggable realistic models (market impact, L2 order book matching) for final validation. Users shouldn't choose between speed and accuracy—they should have both.

**Pluggability**: Every execution component (fill model, latency model, fee model, slippage model, venue routing) is swappable. Default configurations work out-of-the-box; advanced users can customize every detail.

**Reproducibility**: Deterministic execution with seedable randomness, complete event logs, and replayable execution traces. Every backtest should be perfectly reproducible months later.

**Simple User API**: Strategy code uses clean, concise order primitives (`market_buy()`, `limit_order()`, `schedule_twap()`). Complexity lives in engine configuration, not strategy code.

**Production Parity**: The same API works with live brokers with minimal (ideally zero) code changes. Strategies validated in backtest should deploy to production without rewriting.

**Auditability**: Complete order/execution audit trail with microsecond timestamps, market data snapshots at fill time, and execution quality reports. Meet regulatory requirements and enable deep post-trade analysis.

---

## Core Responsibilities

The execution layer handles:

1. **Order Acceptance & Validation**: Accept orders from strategies, validate parameters (size, price, symbol), enforce position limits and risk checks, and provide immediate accept/reject feedback.

2. **Order Lifecycle Management**: Maintain order state (pending → working → partial → filled → cancelled → rejected), handle modifications and cancellations, track parent-child order relationships (e.g., bracket orders), and provide real-time status updates via callbacks or async/await patterns.

3. **Fill Simulation**: Determine when and how orders fill based on market data, including price (execution price relative to market), size (full fills vs partials), and timing (immediate vs queued with latency). Multiple fill models available from simple (instant at mid) to complex (L2 order book matching).

4. **Transaction Cost Modeling**: Apply realistic costs including explicit fees (commissions, exchange fees, regulatory fees), implicit costs (bid-ask spread, slippage, market impact), and financing costs (margin interest, short borrow fees, overnight swaps for FX/crypto).

5. **Latency & Queuing**: Simulate order transmission latency (strategy → execution engine → venue), market data latency (exchange → execution engine), execution latency (order acceptance → fill), and queue priority for limit orders.

6. **Multi-Venue Routing**: Route orders to appropriate venues (simulated exchanges, dark pools, ECNs), model venue-specific characteristics (fees, rebates, latency, fill probability), and support smart order routing across venues.

7. **Position & Cash Updates**: Update positions and cash balances upon fills, calculate realized P&L, track average entry prices, and forward position updates to accounting module.

8. **Execution Reporting**: Generate per-order execution reports (complete timeline from submission to fill), aggregate execution metrics (fill rate, average slippage, execution quality), and provide data for regulatory reporting and internal audit.

9. **Event Callbacks**: Expose lifecycle events for strategies (order accepted, working, partially filled, filled, cancelled, rejected) and analytics modules (capture fill data for post-trade analysis).

---

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Risk Model                               │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  constrained_targets: List[Target]                     │     │
│  └────────────────────────────────────────────────────────┘     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Constrained Targets
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Execution Model                               │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  generate_orders(targets) → List[OrderSpec]            │     │
│  └────────────────────────────────────────────────────────┘     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ OrderSpec
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Execution Engine                           │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐   │
│  │  Order Manager   │  │  Fill Model      │  │  Cost Model  │   │
│  │  - State mgmt    │  │  - Instant       │  │  - Fees      │   │
│  │  - Validation    │  │  - L1 matching   │  │  - Slippage  │   │
│  │  - Persistence   │  │  - L2 matching   │  │  - Impact    │   │
│  └──────────────────┘  └──────────────────┘  └──────────────┘   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐   │
│  │ Latency Model    │  │  Venue Model     │  │ Audit Log    │   │
│  │  - Transmission  │  │  - Exchange sim  │  │  - Events    │   │
│  │  - Execution     │  │  - Dark pools    │  │  - Snapshots │   │
│  │  - Market data   │  │  - Smart routing │  │  - Metrics   │   │
│  └──────────────────┘  └──────────────────┘  └──────────────┘   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Fill Events
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Portfolio / Accounting                       │
│         Updates positions, cash, P&L upon fills                 │
└─────────────────────────────────────────────────────────────────┘
```

**Component Responsibilities**:

- **ExecutionEngine**: Top-level orchestrator exposing user-facing API, routing orders to appropriate models, coordinating between components.

- **OrderManager**: Maintains order state and history, handles order modifications and cancellations, persists order data, manages parent-child relationships.

- **FillModel**: Determines fill outcomes (price, size, timing) based on market data and order type. Pluggable implementations from simple to realistic.

- **CostModel**: Calculates transaction costs (commissions, spreads, slippage, impact). Separate from fill model to allow independent configuration.

- **LatencyModel**: Simulates time delays in order flow and market data. Critical for realistic HFT/short-term strategy testing.

- **VenueModel**: Represents trading venues with specific characteristics (fees, latency, liquidity). Enables multi-venue routing and venue selection strategies.

- **AuditLog**: Persists all execution events, market snapshots at fill time, and order metadata for replay and compliance.

---

## Order Types & Lifecycle

### Supported Order Types

#### Basic Orders

**Market Order**: Execute immediately at best available price. Guarantees fill (in liquid markets) but not price. Typical use: immediate entry/exit when speed matters more than price.

**Limit Order**: Execute only at specified price or better. Guarantees price (if filled) but not fill. Typical use: patient entry/exit, mean reversion strategies, capturing spreads.

**Stop Order**: Becomes market order when price reaches stop level. Used for stop-losses and breakout entries.

**Stop-Limit Order**: Becomes limit order when price reaches stop level. Combines price protection with fill control. Risk: may not fill if price gaps through limit.

#### Time-in-Force Qualifiers

**GTC (Good-Till-Cancelled)**: Order remains active until filled or explicitly cancelled. Default for most strategies.

**IOC (Immediate-or-Cancel)**: Fill immediately whatever quantity available, cancel remainder. Used when partial fills acceptable but don't want to leave order working.

**FOK (Fill-or-Kill)**: Fill entire quantity immediately or cancel entire order. All-or-nothing execution. Used when partial fills unacceptable.

**DAY**: Order expires at end of trading day. Standard for daily rebalancing strategies.

**MOO/MOC (Market-on-Open/Close)**: Execute at opening/closing auction. Used for end-of-day rebalancing and benchmark tracking.

#### Advanced Orders

**Iceberg Order**: Large order where only portion of size is visible to market. Prevents adverse selection and market impact. Useful for institutional-size executions.

**Pegged Order**: Price automatically adjusts to maintain relationship with market (e.g., "mid-point peg" always at mid between bid/ask). Used in market making and adaptive execution.

**Bracket Order**: Entry order with attached take-profit and stop-loss orders. All three orders linked—when entry fills, others activate; when one exit fills, other cancels. Common in discretionary and systematic swing trading.

**OCO (One-Cancels-Other)**: Group of orders where fill of one cancels others. Used for alternative entry points or layered exits.

**TWAP/VWAP Orders**: Time-Weighted or Volume-Weighted Average Price execution. Slices large order into smaller child orders over time. Minimizes market impact for large positions.

### Order Lifecycle States

```text
┌─────────────┐
│  PENDING    │  Order created but not yet sent
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  SUBMITTED  │  Sent to execution engine, awaiting acceptance
└──────┬──────┘
       │
       ├──────────────┐
       ▼              ▼
┌─────────────┐  ┌──────────┐
│  ACCEPTED   │  │ REJECTED │  Order validation failed
└──────┬──────┘  └──────────┘
       │
       ▼
┌─────────────┐
│  WORKING    │  Active in market, awaiting fill
└──────┬──────┘
       │
       ├──────────────┬──────────────┐
       ▼              ▼              ▼
┌─────────────┐  ┌──────────┐  ┌──────────┐
│PARTIALLY_FLD│  │  FILLED  │  │CANCELLED │
└──────┬──────┘  └──────────┘  └──────────┘
       │
       ▼
┌─────────────┐
│   FILLED    │
└─────────────┘
```

**State Transitions**:

- `PENDING → SUBMITTED`: Order sent to execution engine
- `SUBMITTED → ACCEPTED`: Order validated and accepted by venue
- `SUBMITTED → REJECTED`: Order rejected (invalid params, risk limit exceeded, insufficient buying power)
- `ACCEPTED → WORKING`: Order is active and can be filled
- `WORKING → PARTIALLY_FILLED`: Partial quantity filled, remainder still working
- `WORKING → FILLED`: Entire quantity filled
- `WORKING → CANCELLED`: Order cancelled by user or system (e.g., end of day)
- `PARTIALLY_FILLED → FILLED`: Remaining quantity filled
- `PARTIALLY_FILLED → CANCELLED`: Partial fill, remainder cancelled

### Order Modifications

**Price Modification**: Change limit price on working limit order. Some venues implement as cancel-replace (lose queue priority), others as modify (maintain priority).

**Quantity Modification**: Increase or decrease order size. Decreasing usually maintains queue priority; increasing treated as new order (loses priority).

**Order Replacement**: Cancel existing order and submit new one atomically. Guarantees one is always working but loses queue priority.

**Design Decision**: Order modifications are explicit operations that return status (success/failure). Failed modifications leave original order intact.

---

## Fill Models

Fill models determine **when** and **how** orders execute based on market data. Simulor provides multiple models trading off speed vs realism.

### InstantFillModel (Default - Fast)

**Behavior**: Orders fill immediately at simple price levels with no latency.

**Market Orders**: Fill at mid-price (bid + ask) / 2. Assumes infinitely liquid market and no spread cost.

**Limit Orders**: Fill immediately if limit price crosses spread (buy limit ≥ ask or sell limit ≤ bid). Otherwise, fill when future price touches limit.

**Characteristics**:

- ⚡ **Fastest**: Minimal computation, no order book processing
- ✅ **Deterministic**: Same data always produces same fills
- ❌ **Unrealistic costs**: Ignores spread, assumes perfect liquidity
- ✅ **Good for**: Rapid strategy prototyping, parameter sweeps, daily/hourly strategies

**Configuration**:

```python
engine = ExecutionEngine(
    fill_model=InstantFillModel(
        spread_cost=False,  # No spread cost (fill at mid)
        slippage_bps=0      # No slippage
    )
)
```

**Use Case**: Initial strategy development where you want fast iteration. Test logic and indicator combinations without realistic execution modeling. Transition to realistic models once strategy shows promise.

---

### SpreadFillModel (Default + Spread)

**Behavior**: Like InstantFillModel but accounts for bid-ask spread cost.

**Market Orders**: Fill at ask (for buys) or bid (for sells). Pays full spread cost.

**Limit Orders**: Fill when limit price crosses spread, paying spread cost.

**Characteristics**:

- ⚡ **Fast**: Minimal computation
- ✅ **Deterministic**: Consistent fills
- ✅ **Realistic spread costs**: Accounts for bid-ask spread
- ❌ **No queue modeling**: Assumes instant fills for marketable limits
- ✅ **Good for**: Most daily/hourly strategies where spread is dominant cost

**Configuration**:

```python
engine = ExecutionEngine(
    fill_model=SpreadFillModel(
        slippage_bps=2  # Additional 2 bps slippage beyond spread
    )
)
```

**Use Case**: Standard backtest model for strategies trading liquid securities at minute+ timeframes. Balances realism (spread costs) with speed (fast execution). Recommended default for most users.

---

### TradeTapeMatchModel (Realistic - Medium Speed)

**Behavior**: Match orders against historical trade ticks (actual executed trades from market data).

**Market Orders**: Fill at next trade tick price and size after order submission. If order size exceeds trade size, split across multiple ticks (paying different prices).

**Limit Orders**: Fill when trade tick price crosses limit, up to tick size. Large orders accumulate fills across multiple ticks.

**Characteristics**:

- 🐢 **Slower**: Must process every trade tick
- ✅ **Realistic**: Fills based on actual market trades
- ✅ **Realistic slippage**: Large orders walk the tape, paying progressively worse prices
- ❌ **Requires trade data**: Needs historical trade ticks, not just bars
- ✅ **Good for**: Minute-level strategies, validating execution quality

**Configuration**:

```python
engine = ExecutionEngine(
    fill_model=TradeTapeMatchModel(
        partial_fills=True,    # Allow partial fills across ticks
        max_participation=0.1  # Fill at most 10% of trade volume per tick
    )
)
```

**Use Case**: Final validation of strategies before live deployment. Reveals how execution quality degrades with position size. Essential for strategies trading less liquid securities or larger positions.

---

### L2MatchingModel (Most Realistic - Requires L2 Data)

**Status**: Implementation complete, but requires Level 2 order book data which may not be included in initial data release.

**Behavior**: Reconstruct full order book from Level 2 data and match orders against it with realistic queue priority and price-time priority rules.

**Market Orders**: Walk the order book, consuming liquidity at each price level until filled. Pay progressively worse prices as size increases.

**Limit Orders**: Place order in book at specified price level. Fill when aggressive orders hit your level, respecting FIFO queue position.

**Characteristics**:

- 🐌 **Slowest**: Reconstructs order book at every timestamp
- ✅ **Most realistic**: True queue position, market impact, partial fills
- ✅ **Queue priority modeling**: FIFO ordering at each price level
- ⚠️ **Requires L2 data**: Needs full order book snapshots (may not be available in initial release)
- ❌ **Complex**: More configuration and tuning required
- ✅ **Good for**: HFT/market making, large order execution, final validation

**Data Requirements**: This model requires Level 2 market data (order book depth). While the matching engine is implemented, L2 data feeds may not be included in the initial framework release. Check data availability before using this fill model.

**Configuration**:

```python
engine = ExecutionEngine(
    fill_model=L2MatchingModel(
        queue_position='back',  # Assume back of queue (conservative)
        # queue_position='random',  # Random position in queue
        # queue_position='front',   # Optimistic assumption
        price_priority=True,     # Respect price-time priority
        partial_fills=True       # Allow fills at multiple price levels
    )
)
```

**Use Case**: High-frequency strategies, market making, large institutional order execution simulation. Required when order queue position and intra-level dynamics matter. Overkill for most daily/swing strategies.

---

### ProbabilisticFillModel (For Sparse Data)

**Behavior**: Use probabilistic fill rules when trade/quote data is sparse or unavailable (e.g., illiquid stocks, OTC instruments, some international markets).

**Fill Probability**: Based on how far limit price is from mid:

- Limit at mid: 50% fill probability per bar
- Limit beyond best bid/offer: 80%+ fill probability
- Limit far from spread: <20% fill probability

**Partial Fills**: Probabilistically fill portion of order based on volume and liquidity profile.

**Characteristics**:

- ⚡ **Fast**: Probability calculations, no tick processing
- ❌ **Non-deterministic**: Requires seeded RNG for reproducibility
- ⚠️ **Approximate**: Statistical fills, not based on actual market activity
- ✅ **Good for**: Illiquid securities, missing data, sparse tick data

**Configuration**:

```python
engine = ExecutionEngine(
    fill_model=ProbabilisticFillModel(
        base_fill_rate=0.7,      # 70% of limit orders fill eventually
        volume_impact=True,       # Lower fill rate for large orders
        liquidity_profile='low',  # Adjust for security liquidity
        rng_seed=42              # Ensure reproducibility
    )
)
```

**Use Case**: Backtesting illiquid securities (small-cap stocks, emerging markets, OTC) where realistic tick data unavailable. Better than ignoring execution uncertainty, worse than models using actual data.

---

### Custom Fill Models

**User-Implemented**: Extend `FillModel` base class to implement custom fill logic.

**Example Use Cases**:

- Proprietary market impact models (e.g., Almgren-Chriss with custom parameters)
- Venue-specific fill algorithms (exchange simulators for specific markets)
- ML-based fill prediction (predict fill probability from market microstructure features)
- Integration with external execution simulators

**Interface**:

```python
class CustomFillModel(FillModel):
    def get_fill_price(self, order_spec: OrderSpec, market_event: MarketEvent) -> Decimal | None:
        """
        Determine fill price for an order given current market data.

        Args:
            order_spec: Order specification to fill
            market_event: Current market data for the instrument

        Returns:
            Fill price if order can be filled, None otherwise
        """
        # Custom logic here
        pass
```

**Design Decision**: Fill models are pluggable and independent. Users can implement arbitrarily complex fill logic without modifying core engine.

---

## Transaction Cost Modeling

Transaction costs significantly impact strategy performance. Simulor separates costs into explicit (fees) and implicit (spread, slippage, impact) components.

### Explicit Costs (Fees)

**Commission Models**:

- **Per-share**: $0.005 per share (typical US equities). `PerShareCommission(rate=0.005, min=1.0)`
- **Percentage**: 0.1% of trade value (typical FX/crypto). `PercentageCommission(rate=0.001)`
- **Tiered**: Different rates based on volume. `TieredCommission(tiers=[(0, 100k, 0.005), (100k, 1M, 0.003), ...])`
- **Fixed per trade**: $1 per trade (discount brokers). `FixedCommission(amount=1.0)`
- **Zero commission**: Many retail brokers. `ZeroCommission()`

**Exchange & Regulatory Fees**:

- **SEC fees**: $0.0000278 per dollar sold (US equities). Automatically applied for US stocks.
- **FINRA TAF**: $0.000166 per share sold, max $8.30 per trade.
- **Exchange fees**: Venue-specific (maker/taker fees, rebates for liquidity provision).

**Short Selling Costs**:

- **Borrow fees**: Annual rate on short position value (e.g., 0.5% - 20%+ for hard-to-borrow stocks). `ShortBorrowFee(rate=0.01, hard_to_borrow={'GME': 0.25})`
- **Rebate rates**: Interest earned on short sale proceeds (typically Fed funds - spread).

**Financing Costs**:

- **Margin interest**: Interest on borrowed funds for long positions. `MarginInterest(rate=0.06, threshold=0.5)` # 6% on amounts borrowed above 50% equity
- **Overnight swaps**: Financing for FX/CFD/crypto (swap points, funding rates). `OvernightSwap(rate=0.0001)` # 1 bp per day

**Configuration**:

```python
engine = ExecutionEngine(
    cost_model=CostModel(
        commission=PerShareCommission(rate=0.005, min=1.0),
        exchange_fees=True,         # Include SEC/FINRA fees
        short_borrow=ShortBorrowFee(rate=0.01),
        margin_interest=MarginInterest(rate=0.06),
        include_financing=True      # Apply overnight costs
    )
)
```

---

### Implicit Costs (Spread, Slippage, Impact)

**Bid-Ask Spread**: Cost of crossing the spread (buy at ask, sell at bid). Captured by fill models that use bid/ask prices rather than mid.

**Slippage**: Execution price worse than expected due to market movement or order size.

- **Fixed slippage**: Constant bps degradation. `FixedSlippage(bps=2)` # 2 bps worse than expected
- **Volume-based**: Slippage increases with order size. `VolumeSllippage(base_bps=1, size_factor=0.1)` # Larger orders pay more slippage
- **Volatility-scaled**: More slippage in volatile markets. `VolatilitySlippage(vol_multiplier=2)` # Slippage proportional to ATR

**Market Impact**: Price moves against you when placing large orders, with temporary (recovers) and permanent (doesn't recover) components.

**Temporary Impact**: Price moves while you're executing but recovers after. Models:

- **Square-root model**: Impact ∝ sqrt(order_size / volume). Common empirical finding.
- **Linear model**: Impact ∝ (order_size / volume). Simpler, more conservative.

**Permanent Impact**: Price stays worse after execution. Usually smaller than temporary. Important for large portfolio rebalances.

**Almgren-Chriss Model**: Sophisticated impact model used by institutions:

```python
impact_model = AlmgrenChrissImpact(
    temporary_impact=0.5,    # 50 bps per sqrt(% of volume)
    permanent_impact=0.1,    # 10 bps per % of volume
    volatility_scaling=True  # Scale impact by volatility
)
```

**Configuration**:

```python
cost_model = CostModel(
    spread_cost=True,        # Pay bid-ask spread
    slippage=VolatileSlippage(vol_multiplier=1.5),
    impact_model=AlmgrenChrissImpact(temporary=0.5, permanent=0.1)
)
```

**Design Decision**: Costs are modular and combinable. Apply commission + spread + slippage + impact independently. Each cost component is optional and configurable.

**Rationale**: Different strategies have different dominant costs. HFT pays spreads and fees; large institutional pays impact; retail swing trading pays commissions and slippage. Separating cost components lets users model their specific cost structure accurately.

---

## Cash Settlement & Lifecycle

Different asset types settle at different speeds, affecting when cash becomes available for trading. Simulor models realistic settlement periods to ensure accurate cash management and buying power calculations.

### Default Settlement Modes

**Important Configuration Choice**:

- **Default Backtest Mode: T+0 (Instant Settlement)** - For simplicity and rapid iteration, backtests default to instant settlement. Trades immediately affect cash balances, eliminating settlement-related complexity. Recommended for initial strategy development and parameter optimization.

- **Realistic Mode: T+2 Settlement (Opt-in)** - Enable realistic settlement via configuration to model actual trading constraints. This mode enforces proper settlement periods, buying power calculations, and violation tracking. Required for final strategy validation before live deployment.

```python
# Default: Instant settlement (T+0)
engine = Engine(strategies=[my_strategy])

# Realistic: T+2 settlement with violation tracking
engine = Engine(
    strategies=[my_strategy],
    settlement_mode='realistic',  # Enable T+2 settlement
    track_violations=True          # Warn on settlement violations
)
```

**Recommendation**: Use T+0 for development speed, switch to realistic mode for final validation.

### Settlement Periods

**Settlement Standards by Asset Class**:

- **Equities (US)**: T+2 (trade date + 2 business days) - standard since September 2017
- **Equities (International)**: T+1 to T+3 depending on market (China T+1, some European T+2)
- **Forex (Spot)**: T+0 (same-day) or T+2 (standard settlement)
- **Crypto**: T+0 (instant settlement on most exchanges)
- **Options**: T+1 (next business day)
- **Bonds**: T+1 to T+3 depending on type and market
- **Futures**: Daily mark-to-market with cash transfers each day

**Why Settlement Matters**:

- Cannot use proceeds from sale until settlement date
- Affects buying power calculations for new trades
- Pattern day trading rules depend on settled vs unsettled cash
- Violations can lead to trading restrictions
- Interest may accrue on unsettled balances

### Cash States

Portfolio tracks multiple cash states to accurately model settlement:

**Settled Cash**:

- Available for immediate use without restriction
- Can execute trades freely
- No risk of good faith violations

**Unsettled Cash**:

- Proceeds from recent sales awaiting settlement
- Availability depends on account type (cash vs margin)
- Becomes settled after settlement period elapses

**Reserved Cash**:

- Set aside for pending limit orders
- Not available for other trades until order fills or cancels
- Prevents over-commitment of capital

**Total Cash**:

- Sum of all cash states
- Used for P&L calculations but not all is tradeable
- Important for accurate portfolio valuation

### Settlement Queue

The system maintains a chronological queue of pending cash movements:

**Buy Trade Settlement** (T+2 example):

- Day 1 (Trade Date): Cash reserved, position created
- Day 2: Cash still reserved, awaiting settlement
- Day 3 (Settlement Date): Cash deducted from settled balance, reservation released

**Sell Trade Settlement** (T+2 example):

- Day 1 (Trade Date): Position closed, cash marked as pending
- Day 2: Cash pending settlement
- Day 3 (Settlement Date): Cash credited to settled balance, available for use

**Holiday Handling**:

- Settlement dates skip market holidays and weekends
- A Friday trade settles Tuesday (T+2 skips weekend)
- System uses exchange-specific holiday calendars

### Account Type Impact

**Cash Account**:

- Most conservative model
- Can only trade with settled cash
- Using unsettled proceeds then selling before settlement triggers good faith violation
- Three violations in 12 months = 90-day cash account trading restriction
- Suitable for long-term investors, retirement accounts

**Margin Account**:

- More flexible cash usage
- Can trade on unsettled proceeds within limits
- Unsettled cash counts toward buying power calculation
- Pattern day trader rules apply (4+ day trades in 5 days requires $25k minimum equity)
- Subject to Regulation T margin requirements
- Standard for active traders

**Portfolio Margin Account**:

- Risk-based margining, not position-based
- Most flexible cash usage rules
- Requires larger account size ($125k+ typically)
- Complex calculations based on portfolio risk
- Used by professional traders and institutions

### Buying Power Calculation

**Cash Account**:

```text
Buying Power = Settled Cash - Reserved Cash
```

Simple and conservative - only settled cash available.

**Margin Account (Reg T)**:

```text
Buying Power = (Settled Cash + Unsettled Cash + 0.5 × Long Market Value) × 2 - Current Positions Value
```

More complex - includes margin borrowing capacity.

**Portfolio Margin**:

```text
Buying Power = Net Liquidation Value - Margin Requirement (risk-based calculation)
```

Most sophisticated - based on portfolio stress testing.

### Violations & Warnings

**Good Faith Violation**:

- Buy stock with unsettled cash
- Sell that stock before cash settles
- Occurs in cash accounts only
- Example: Sell AAPL Monday, buy GOOGL Tuesday with proceeds, sell GOOGL Wednesday (violation - AAPL cash not settled until Thursday)

**Free Riding Violation**:

- Buy stock without sufficient settled funds
- Sell before purchase settles
- Occurs in cash accounts
- Example: Buy $10k MSFT with $5k settled cash, sell MSFT next day before settlement

**Pattern Day Trading**:

- Four or more day trades in 5 rolling business days
- Requires $25k minimum equity in margin accounts
- Day trade = buy and sell same security on same day
- Restriction lasts until equity restored to $25k+

The system tracks violations and warns when approaching limits, preventing accidental restrictions.

### Configuration

**Per-Symbol Settlement**:
Symbols automatically use correct settlement periods based on asset class and exchange. Settlement rules are pulled from symbol metadata - no manual configuration needed for standard instruments.

**Account Configuration Options**:

- Account type (cash, margin, portfolio margin)
- Settlement enforcement level (strict, lenient, disabled for backtesting)
- Violation tracking enabled/disabled
- Buying power calculation method
- Margin interest rate (for margin accounts)

**Backtest Settings**:

- **Realistic mode**: Full settlement queue modeling (default for accurate backtesting)
- **Fast-forward mode**: Treat all trades as T+0 for faster iteration when settlement doesn't matter
- **Historical accuracy**: Apply settlement rules as they existed on trade date (e.g., US was T+3 before Sept 2017)
- **Violation detection**: Warn or error on settlement violations during backtest

### Implementation Details

**Settlement Engine**:

- Processes settlement queue daily at market close
- Moves pending cash to settled status when settlement date reached
- Updates buying power calculations
- Triggers violation checks if applicable
- Maintains audit trail of all cash movements

**Point-in-Time Accuracy**:

- Historical settlement rules applied based on trade date
- US equities switched from T+3 to T+2 on September 5, 2017
- System automatically uses correct rules for historical backtests
- Ensures backtest results match what would have happened historically

**Performance Impact**:

- Settlement tracking adds minimal overhead (~1-2% slowdown)
- Can be disabled for strategies that don't care about intraday cash constraints
- Fast-forward mode (T+0) eliminates overhead entirely
- Recommended to enable for realistic strategy validation

**Integration with Risk Models**:

- Buying power checks occur before order submission
- Orders rejected if insufficient buying power
- Real-time buying power updates as orders fill
- Settlement queue factored into risk calculations

---

## Latency Modeling

Latency simulation is critical for realistic backtesting of short-term and HFT strategies. Simulor models three latency sources.

### Latency Types

**Order Transmission Latency**: Time from strategy decision to order reaching venue.

- **Network latency**: Physical transmission (1-100ms depending on colocation/distance).
- **Processing latency**: Order validation, risk checks (0.1-10ms).
- **Total typical**: 5-50ms for retail, 0.1-5ms for colocated HFT.

**Market Data Latency**: Time from exchange event to strategy receiving data.

- **Exchange dissemination**: Time to publish market data (0.01-1ms).
- **Network latency**: Transmission to data consumer (1-100ms).
- **Processing latency**: Decoding, normalization (0.1-5ms).
- **Total typical**: 10-100ms for retail, 0.1-10ms for colocated.

**Execution Latency**: Time from order arrival at venue to fill confirmation.

- **Queue time**: Time waiting for fill (0-infinity, depends on queue position).
- **Matching latency**: Exchange matching engine processing (0.01-1ms).
- **Total typical**: Immediate for market orders, variable for limits.

### Latency Models

**FixedLatency**: Constant delay for all orders.

```python
latency_model = FixedLatency(
    order_transmission_ms=10,
    market_data_ms=15,
    execution_ms=5
)
```

Simple, deterministic, good for coarse modeling.

**RandomLatency**: Uniformly distributed latency within range.

```python
latency_model = RandomLatency(
    order_transmission_ms=(5, 20),  # 5-20ms uniform
    market_data_ms=(10, 30),
    execution_ms=(1, 10),
    rng_seed=42  # Reproducible randomness
)
```

More realistic variability, still deterministic with seed.

**DistributionLatency**: Latency from specified probability distribution (normal, exponential, lognormal).

```python
latency_model = DistributionLatency(
    order_transmission=NormalDist(mean=10, std=3),
    market_data=LogNormalDist(mean=20, std=5),
    execution=ExponentialDist(mean=2),
    rng_seed=42
)
```

Most realistic, captures real-world latency distributions.

**PercentileLatency**: Model different latency conditions (median, p95, p99).

```python
latency_model = PercentileLatency(
    percentile='p50'  # or 'p95', 'p99'
)
```

Test strategy under typical (p50) vs stressed (p99) latency conditions.

**Configuration**:

```python
engine = ExecutionEngine(
    latency_model=DistributionLatency(
        order_transmission=NormalDist(mean=10, std=3),
        market_data=NormalDist(mean=15, std=5),
        execution=ExponentialDist(mean=2),
        rng_seed=42
    )
)
```

**Design Decision**: Latency is modeled separately from fill logic. Same fill model can be combined with different latency models.

**Rationale**: Latency matters enormously for HFT but is irrelevant for daily strategies. Separating latency from fills lets users add/remove latency modeling without changing fill logic. Also enables testing same strategy under different latency conditions (colocated vs retail latency).

---

## User-Facing API

### Security Positions API

Broker position snapshots are exposed through a single unified API:

```python
positions = broker.get_security_positions()
```

Each item in `positions` is a `SecurityPosition` with:

- `instrument`
- `currency`
- `quantity`
- `available_quantity`
- `cost_price`
- `current_price`
- computed properties: `contract_size`, `market_value`, `unrealized_pnl`

This API works for both stocks and derivatives. For stocks, `contract_size` is `1`.
For options, `contract_size` is typically `100`, so both `market_value` and
`unrealized_pnl` automatically scale by contract multiplier.

Example:

```python
for pos in broker.get_security_positions():
    print(
        pos.instrument.display_name,
        pos.quantity,
        pos.cost_price,
        pos.market_value,
        pos.unrealized_pnl,
    )
```

The execution engine provides API for ExecutionModel components to generate OrderSpec. Strategies don't call these methods directly - they work through the pluggable ExecutionModel.

### OrderSpec Generation

**ExecutionModel Methods** (called by Engine, not strategies):

```python
class ExecutionModel:
    def generate_orders(
        self,
        targets: List[Target],      # Targets from RiskModel
    ) -> List[OrderSpec]:
        """
        Convert targets into OrderSpec.

        Targets are not guaranteed to be merged per instrument, so
        normalizing the target stream into executable quantity
        transitions is the responsibility of the execution model.
        Current positions are read from the model context.

        Returns list of OrderSpec to achieve targets.
        """
        pass
```

**Simple OrderSpec Construction**:

```python
from decimal import Decimal

from simulor.types import Instrument, OrderSide, OrderSpec, OrderType, TimeInForce

aapl = Instrument.stock("AAPL", exchange="NASDAQ")

# Market orders
order_spec = OrderSpec(
    instrument=aapl,
    side=OrderSide.BUY,
    quantity=Decimal("100"),
    order_type=OrderType.MARKET,
    time_in_force=TimeInForce.DAY  # Market orders should use DAY or IOC
)

# Limit orders
order_spec = OrderSpec(
    instrument=aapl,
    side=OrderSide.BUY,
    quantity=Decimal("100"),
    order_type=OrderType.LIMIT,
    limit_price=Decimal("150.00"),
    time_in_force=TimeInForce.GTC  # Limit orders can use GTC
)
```

### Advanced Order Types

**Bracket Orders** (entry + take-profit + stop-loss):

```python
bracket = exec.bracket_order(
    symbol='AAPL',
    side='buy',
    size=100,
    entry_price=150.00,       # Limit entry
    take_profit=155.00,       # Take profit limit
    stop_loss=147.00          # Stop loss
)
# Returns BracketHandle with entry_order, take_profit_order, stop_loss_order
```

**OCO Orders** (one-cancels-other):

```python
aapl = Instrument.stock("AAPL", exchange="NASDAQ")

oco = exec.oco_order([
    OrderSpec(
        instrument=aapl,
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("149.00"),
        time_in_force=TimeInForce.GTC,
    ),
    OrderSpec(
        instrument=aapl,
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        order_type=OrderType.STOP,
        stop_price=Decimal("152.00"),
        time_in_force=TimeInForce.DAY,
    ),
])
# Breakout entry: buy if breaks above 152 OR buy if dips to 149
```

**Scheduled Execution** (TWAP/VWAP):

```python
# Time-Weighted Average Price (evenly split over time)
twap = exec.schedule_twap(
    symbol='AAPL',
    total_size=10000,
    start_time=datetime(2024, 1, 15, 9, 30),
    end_time=datetime(2024, 1, 15, 16, 0),
    num_slices=20,           # 500 shares per slice
    child_type='limit',      # Child orders as limits at mid
    randomize_timing=True    # Randomize slice timing (anti-gaming)
)

# Volume-Weighted Average Price (participate with market volume)
vwap = exec.schedule_vwap(
    symbol='AAPL',
    total_size=10000,
    start_time=datetime(2024, 1, 15, 9, 30),
    end_time=datetime(2024, 1, 15, 16, 0),
    participation_rate=0.1,  # Fill 10% of market volume
    price_limit=None         # No limit (participate at any price)
)
```

### Order Management

**Query Order Status**:

```python
order = exec.get_order(order_id='abc123')
print(f"Status: {order.status}")
print(f"Filled: {order.filled_qty} / {order.total_qty}")
print(f"Avg Fill Price: {order.avg_fill_price}")
print(f"Remaining: {order.remaining_qty}")
```

**Get Open Orders**:

```python
open_orders = exec.get_open_orders()
open_orders_for_symbol = exec.get_open_orders(symbol='AAPL')
```

**Cancel Orders**:

```python
# Cancel specific order
exec.cancel(order.id)
# or
order.cancel()

# Cancel all orders
exec.cancel_all()

# Cancel all orders for symbol
exec.cancel_all(symbol='AAPL')
```

**Modify Orders**:

```python
# Modify price
exec.modify_price(order.id, new_price=150.50)

# Modify quantity
exec.modify_quantity(order.id, new_size=150)

# Replace order (cancel + new)
new_order = exec.replace(order.id, new_price=150.50, new_size=150)
```

### Order Handles & Callbacks

**OrderHandle** (returned from order submission):

```python
order = exec.market_buy('AAPL', 100)

# Properties
print(order.id)               # Unique order ID
print(order.symbol)           # 'AAPL'
print(order.side)             # 'buy'
print(order.status)           # OrderStatus enum
print(order.filled_qty)       # Filled quantity
print(order.remaining_qty)    # Remaining quantity
print(order.avg_fill_price)   # Average fill price
print(order.commission)       # Total commission paid
print(order.created_at)       # Submission timestamp
print(order.updated_at)       # Last update timestamp

# Methods
order.cancel()
order.modify(price=151.00)
fills = order.get_fills()     # List of all fill events
events = order.get_events()   # Complete event history
```

**Event Callbacks** (react to order events):

```python
def on_fill(event: FillEvent):
    print(f"Filled {event.qty} @ {event.price}")

def on_cancelled(event: CancelEvent):
    print(f"Order cancelled: {event.reason}")

order = exec.market_buy('AAPL', 100)
order.on('fill', on_fill)
order.on('cancelled', on_cancelled)
order.on('rejected', on_rejected)
```

**Async/Await Pattern** (wait for order completion):

```python
async def trade():
    order = exec.market_buy('AAPL', 100)

    # Wait until filled (or cancelled/rejected)
    await order.wait_filled(timeout_ms=60000)

    print(f"Filled at {order.avg_fill_price}")
```

### Execution Reports

**Per-Order Report**:

```python
report = exec.get_execution_report(order.id)

print(report.order_id)
print(report.symbol)
print(report.requested_qty)
print(report.filled_qty)
print(report.avg_fill_price)
print(report.total_commission)
print(report.total_slippage_bps)
print(report.total_market_impact_bps)

# Complete timeline
for event in report.timeline:
    print(f"{event.timestamp}: {event.type} - {event.details}")
```

**Aggregate Execution Metrics**:

```python
metrics = exec.get_execution_metrics(
    start_date=datetime(2024, 1, 1),
    end_date=datetime(2024, 12, 31),
    symbol='AAPL'  # Optional: filter by symbol
)

print(f"Fill Rate: {metrics.fill_rate:.2%}")
print(f"Avg Slippage: {metrics.avg_slippage_bps:.2f} bps")
print(f"Avg Commission: ${metrics.avg_commission:.2f}")
print(f"Total Costs: ${metrics.total_costs}")
print(f"Market Impact: {metrics.avg_market_impact_bps:.2f} bps")

# Distribution statistics
print(f"Slippage p50: {metrics.slippage_p50_bps:.2f} bps")
print(f"Slippage p95: {metrics.slippage_p95_bps:.2f} bps")
print(f"Slippage p99: {metrics.slippage_p99_bps:.2f} bps")
```

---

## Multi-Venue Execution

Simulor supports routing orders to multiple simulated venues with different characteristics.

### Venue Modeling

**Venue Characteristics**:

Each venue can specify:

- **Fees**: Maker/taker fees, rebates, fixed fees
- **Latency**: Venue-specific execution latency distribution
- **Liquidity**: Available size at each price level
- **Fill probability**: Likelihood of fills for limits
- **Operating hours**: Venue-specific trading sessions

**Venue Types**:

- **Lit exchanges**: Public order books (NYSE, NASDAQ, etc.). Full transparency, displayed liquidity.
- **Dark pools**: Hidden liquidity, no pre-trade transparency. Better for large orders (less market impact).
- **ECNs**: Electronic communication networks with maker/taker fees. Often provide rebates for liquidity provision.

**Example Configuration**:

```python
venues = [
    Venue(
        name='NYSE',
        venue_type='lit_exchange',
        maker_fee_bps=-0.2,    # Rebate for providing liquidity
        taker_fee_bps=0.3,     # Pay for taking liquidity
        latency_ms=NormalDist(mean=5, std=2),
        fill_probability=0.9
    ),
    Venue(
        name='DarkPool_A',
        venue_type='dark_pool',
        maker_fee_bps=0.0,
        taker_fee_bps=0.0,     # No fees
        latency_ms=NormalDist(mean=10, std=3),
        fill_probability=0.6,  # Lower fill rate
        min_size=1000          # Minimum order size
    )
]

engine = ExecutionEngine(venues=venues)
```

### Smart Order Routing

**Routing Strategies**:

- **Best price**: Route to venue with best displayed price.
- **Lowest cost**: Route to venue with lowest total costs (fees + spread + impact).
- **Fastest fill**: Route to venue with highest fill probability or lowest latency.
- **Liquidity seeking**: Try dark pools first (lower impact), fall back to lit exchanges.
- **Multi-venue sweep**: Split order across multiple venues simultaneously.

**Example**:

```python
router = SmartOrderRouter(
    strategy='liquidity_seeking',  # Try dark pools first
    max_venues=3,                  # Use up to 3 venues
    rebalance_interval_ms=1000     # Re-evaluate routing every second
)

engine = ExecutionEngine(
    venues=venues,
    router=router
)

# Routing happens automatically
order = exec.market_buy('AAPL', 10000)
# Engine automatically routes to dark pools first, then lit exchanges
```

**Design Decision**: Venue routing is configurable but transparent to strategies. Strategies submit orders without specifying venues unless they want explicit control.

**Rationale**: Most strategies don't care about venue mechanics—they want best execution. Smart routing should be automatic. Advanced users (market making, venue arbitrage) can specify venues explicitly.

---

## Execution Diagnostics & Persistence

### Audit Trail & Event Logging

Simulor captures complete execution history for reproducibility, debugging, and compliance.

**Events Captured**:

- **Order events**: Submitted, accepted, rejected, modified, cancelled
- **Fill events**: Partial fills and complete fills with price, size, timestamp
- **Market snapshots**: Market data state at fill time (bid, ask, last, volume)
- **Cost breakdown**: Commission, slippage, impact separately logged
- **Modifications**: All order changes with before/after state

**Event Storage**:

```python
audit_log = AuditLog(
    storage='parquet',                    # or 'csv', 'sqlite', 'postgres'
    path='/data/backtests/run_20240115',
    include_market_snapshots=True,        # Store L1 data at fills
    include_order_book=False,             # Don't store full L2 (large)
    compression='snappy'
)

engine = ExecutionEngine(audit_log=audit_log)
```

**Querying Events**:

```python
# Get all events for order
events = audit_log.get_order_events(order_id='abc123')

# Get all fills within timeframe
fills = audit_log.get_fills(
    start=datetime(2024, 1, 15, 9, 30),
    end=datetime(2024, 1, 15, 16, 0),
    symbol='AAPL'
)

# Get market snapshot at specific fill
snapshot = audit_log.get_market_snapshot(fill_id='xyz789')
print(f"Bid: {snapshot.bid}, Ask: {snapshot.ask}")
```

### Execution Replay

**Deterministic Replay**: Re-run exact execution using stored events and RNG seeds.

```python
# Original run
engine = ExecutionEngine(
    fill_model=ProbabilisticFillModel(rng_seed=42),
    latency_model=RandomLatency(rng_seed=43),
    audit_log=audit_log
)
# ... run backtest ...

# Replay exact same execution
replay_engine = ExecutionEngine.from_audit_log(
    audit_log_path='/data/backtests/run_20240115'
)
# Replays identical fills, latencies, randomness
```

**Use Cases**:

- Debugging: Reproduce issues from production runs
- Compliance: Demonstrate exact execution to regulators
- Research: Compare strategy variations starting from same fills

### Performance Metrics

**Execution Quality Metrics**:

```python
metrics = exec.get_execution_quality_metrics()

# Fill statistics
print(f"Total orders: {metrics.total_orders}")
print(f"Fill rate: {metrics.fill_rate:.2%}")
print(f"Avg time to fill: {metrics.avg_time_to_fill_ms:.1f} ms")

# Cost statistics
print(f"Avg slippage: {metrics.avg_slippage_bps:.2f} bps")
print(f"Slippage std dev: {metrics.slippage_std_bps:.2f} bps")
print(f"Total commissions: ${metrics.total_commission:.2f}")
print(f"Total market impact: ${metrics.total_market_impact:.2f}")

# Price improvement (filled better than expected)
print(f"Price improvement rate: {metrics.price_improvement_rate:.2%}")
print(f"Avg price improvement: {metrics.avg_price_improvement_bps:.2f} bps")

# Distribution percentiles
print(f"Slippage p95: {metrics.slippage_p95_bps:.2f} bps")
print(f"Slippage p99: {metrics.slippage_p99_bps:.2f} bps")
```

**Comparison Reports** (compare execution across backtests):

```python
comparison = compare_execution_quality(
    run1='/data/backtests/run_20240115',
    run2='/data/backtests/run_20240116',
    metric='slippage'
)

print(comparison.summary())
# Shows which run had better execution quality and by how much
```

---

## Configuration Presets

Simulor provides pre-configured execution setups for common use cases.

### Fast Prototyping

```python
from simulor.execution import ExecutionConfig

engine = ExecutionEngine(
    config=ExecutionConfig.FAST_PROTOTYPE
)
# Instant fills, no costs, no latency
# For: Rapid strategy development, indicator testing
```

### Realistic Daily Strategy

```python
engine = ExecutionEngine(
    config=ExecutionConfig.DAILY_STRATEGY
)
# Spread costs, realistic commissions, minimal latency
# For: Daily/swing strategies, position trading
```

### Realistic Intraday Strategy

```python
engine = ExecutionEngine(
    config=ExecutionConfig.INTRADAY_STRATEGY
)
# Trade tape matching, slippage, latency, full costs
# For: Minute-level strategies, day trading
```

### HFT / Market Making

```python
engine = ExecutionEngine(
    config=ExecutionConfig.HFT_MARKET_MAKING
)
# L2 matching, queue priority, sub-ms latency, maker/taker fees
# For: High-frequency strategies, market making, latency arbitrage
```

### Large Institutional Orders

```python
engine = ExecutionEngine(
    config=ExecutionConfig.INSTITUTIONAL
)
# Market impact models, multi-venue routing, TWAP/VWAP, partial fills
# For: Large order execution, portfolio rebalancing
```

### Custom Configuration

```python
engine = ExecutionEngine(
    fill_model=SpreadFillModel(slippage_bps=2),
    cost_model=CostModel(
        commission=PerShareCommission(rate=0.005, min=1.0),
        short_borrow=ShortBorrowFee(rate=0.01)
    ),
    latency_model=FixedLatency(
        order_transmission_ms=10,
        market_data_ms=15
    ),
    audit_log=AuditLog(storage='parquet', path='/data/logs')
)
```

**Design Decision**: Presets are starting points, not constraints. Users can override any component or parameter.

**Rationale**: Good defaults accelerate development. Most users should start with a preset matching their strategy type, then customize as needed. Power users can configure everything from scratch.

---

## Production Deployment (Backtest → Live)

### Environment Abstraction

The same `ExecutionEngine` interface works across all environments:

**Backtest Mode**: Simulated fills using fill models and historical data.

**Paper Trading Mode**: Real-time data but simulated execution. Tests data pipeline and order logic without risk.

**Live Trading Mode**: Real broker API with actual order execution.

**Environment Selection**:

```python
# Backtest
engine = ExecutionEngine(
    mode='backtest',
    fill_model=SpreadFillModel(),
    data_provider=HistoricalDataProvider(...)
)

# Paper trading
engine = ExecutionEngine(
    mode='paper',
    fill_model=SpreadFillModel(),        # Simulated fills
    data_provider=LiveDataProvider(...)  # Real-time data
)

# Live trading
engine = ExecutionEngine(
    mode='live',
    broker=InteractiveBrokers(
        account_id='U1234567',
        api_key='...'
    ),
    data_provider=LiveDataProvider(...)
)
```

**Strategy Code** (identical across environments):

```python
# Strategy is a composition of pluggable models
strategy = Strategy(
    name="MyStrategy",
    universe=Static([Instrument.stock("AAPL", "NASDAQ")]),
    alpha=MyAlphaModel(),  # Contains signal generation logic
    construction=EqualWeight(),
    risk=PositionLimit(max_position=Decimal("0.1")),
    execution=Immediate()  # Converts targets to orders
)
# Same strategy object works in backtest/paper/live modes
```

**Design Decision**: Strategies are environment-agnostic. They emit orders through the same API regardless of execution environment. The engine handles environment-specific mechanics.

**Rationale**: If strategy code changes when moving to production, you invalidate backtest results and risk introducing bugs. Environment-agnostic strategies ensure backtest logic matches production logic exactly.

---

### Broker Integrations

**Supported Brokers** (planned):

- **Interactive Brokers**: Via IB Gateway / TWS API
- **Alpaca**: REST API and WebSocket
- **TD Ameritrade**: API integration (pre-Schwab merger)
- **Binance**: Cryptocurrency spot and futures
- **Custom brokers**: Implement `BrokerInterface` for proprietary connections

**Broker Interface**:

```python
class BrokerInterface:
    def submit_order(self, order_spec: OrderSpec) -> OrderHandle:
        """Submit order to live broker"""
        pass

    def cancel_order(self, order_id: str) -> bool:
        """Cancel order at broker"""
        pass

    def get_order_status(self, order_id: str) -> OrderStatus:
        """Query order status from broker"""
        pass

    def get_positions(self) -> List[Position]:
        """Get current positions from broker"""
        pass

    def get_account_info(self) -> AccountInfo:
        """Get account balance, buying power, etc."""
        pass
```

**Example (Interactive Brokers)**:

```python
from simulor.brokers import InteractiveBrokers

broker = InteractiveBrokers(
    host='127.0.0.1',
    port=7497,              # IB Gateway port
    client_id=1,
    account_id='U1234567'
)

engine = ExecutionEngine(
    mode='live',
    broker=broker
)

# Same API as backtest
order = exec.market_buy('AAPL', 100)
# Actual order sent to IB
```

### Safety & Risk Controls

**Pre-Trade Risk Checks** (prevent dangerous orders):

- **Position limits**: Max position size per symbol
- **Order size limits**: Max order size (shares/notional)
- **Concentration limits**: Max % of portfolio in single position
- **Buying power check**: Ensure sufficient capital
- **Short locate check**: Verify shares available to borrow

**Real-Time Risk Monitoring**:

- **Drawdown limits**: Halt trading if drawdown exceeds threshold
- **Loss limits**: Stop after losing max amount in period (day/week)
- **Profit targets**: Stop after hitting profit target (lock in gains)
- **Position monitoring**: Alert on large adverse moves

**Circuit Breakers**:

- **Kill switch**: Emergency halt all trading
- **Flatten positions**: Close all positions immediately
- **Pause strategy**: Temporarily stop new orders, let existing ones complete

**Configuration**:

```python
risk_manager = RiskManager(
    max_position_size={'AAPL': 1000, '__default__': 500},
    max_order_size=500,
    max_portfolio_concentration=0.2,  # Max 20% in any position
    max_daily_loss=5000,
    max_drawdown_pct=0.10,            # Kill switch at 10% drawdown
    check_buying_power=True
)

engine = ExecutionEngine(
    mode='live',
    broker=broker,
    risk_manager=risk_manager
)
```

**Design Decision**: Risk controls are separate from execution engine but integrate seamlessly. Orders pass through risk manager before execution.

**Rationale**: Risk management is critical for live trading but optional for backtest. Separation allows testing strategies without risk constraints, then applying constraints for production deployment.

---

## Design Principles Summary

1. **Realism Flexibility**: Fast deterministic models for prototyping, realistic models for validation. Users choose speed vs accuracy based on development stage.

2. **Pluggability**: Every component swappable (fill model, cost model, latency, venues). Customize any aspect without modifying core engine.

3. **Reproducibility**: Deterministic execution with seeded RNG, complete audit trails, exact replay capability. Every backtest reproducible forever.

4. **Simple API**: Strategies use clean methods (`market_buy()`, `limit_order()`). Complexity lives in engine configuration, not usage.

5. **Production Parity**: Identical API across backtest/paper/live. Validated backtest code deploys without changes.

6. **Auditability**: Complete event logs, execution reports, market snapshots. Meet regulatory requirements and enable deep analysis.

7. **Modularity**: Execution, costs, latency, routing are independent. Mix and match components to model specific trading environments.

---

## Future Extensions

### Advanced Fill Models

**Machine Learning Fill Prediction**: Train ML models on historical order data to predict fill probability, fill time, and execution price based on market microstructure features.

**Agent-Based Market Simulation**: Simulate market participants (market makers, HFTs, institutional flow) to generate realistic order flow and price impact.

**Genetic Algorithm Fill Optimization**: Evolve fill model parameters to match observed execution quality metrics from real trading.

### Execution Algorithms

**Adaptive TWAP/VWAP**: Child order algorithms that adjust slice size and timing based on real-time market conditions (volatility spikes, volume surges, price trends).

**Implementation Shortfall**: Minimize tracking error vs arrival price while managing market impact and timing risk.

**Percentage of Volume (POV)**: Participate at target percentage of market volume, adjusting execution speed with volume patterns.

**Liquidity-Seeking Algorithms**: Opportunistically capture liquidity from dark pools and mid-point crosses before accessing lit markets.

### Multi-Asset Execution

**Cross-Asset Order Routing**: Intelligently route related orders (e.g., stock vs futures, ETF vs components) to optimize total execution costs.

**Portfolio Transition Management**: Optimize execution of large portfolio rebalances considering correlations, liquidity, and market impact.

**Options Market Making**: Simulate options market making with delta hedging, inventory management, and skew trading.

### Real-Time Execution Analytics

**Live Execution Dashboard**: Real-time visualization of order flow, fill quality, cost attribution, slippage distribution.

**Execution Alerts**: Notifications when execution quality degrades (high slippage, low fill rates, unusual latency).

**Adaptive Cost Models**: Update cost model parameters in real-time based on observed execution quality.

### Advanced Venue Modeling

**Exchange Microstructure**: Model venue-specific rules (tick sizes, lot sizes, order types, priority rules, circuit breakers).

**Maker-Taker Economics**: Optimize order types and routing to maximize rebates or minimize fees.

**Dark Pool Gaming**: Simulate information leakage and adverse selection in dark pools.
