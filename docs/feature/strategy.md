# Strategy

The strategy module is responsible for defining trading logic, processing market signals, and generating trading decisions. It provides a unified framework for implementing algorithmic strategies that work consistently across backtesting, paper trading, and live execution environments.

## Table of Contents

- [Core Architecture](#core-architecture)
- [Core Strategy Features](#core-strategy-features)
  - [Event-Driven Architecture](#event-driven-architecture)
  - [MarketEvent Structure](#marketevent-structure)
  - [Data Delivery Model: Subscription-Based Filtering](#data-delivery-model-subscription-based-filtering)
  - [Strategy Protocol (No Forced Inheritance)](#strategy-protocol-no-forced-inheritance)
  - [Signal Generation Framework](#signal-generation-framework)
  - [Machine Learning Integration](#machine-learning-integration)
  - [AI Agent Strategies (Experimental)](#ai-agent-strategies-experimental)
  - [Environment Consistency (Backtest/Paper/Live)](#environment-consistency-backtestpaperlive)
  - [Multi-Strategy Portfolio](#multi-strategy-portfolio)
  - [Warm-Up Period Management](#warm-up-period-management)
- [Advanced Strategy Features](#advanced-strategy-features)
  - [Indicator Library Integration](#indicator-library-integration)
  - [Scheduled Events & Time-Based Logic](#scheduled-events--time-based-logic)
  - [Parameter Optimization & Hyperparameter Tuning](#parameter-optimization--hyperparameter-tuning)
  - [Strategy State Management](#strategy-state-management)
- [Strategy Analysis & Debugging](#strategy-analysis--debugging)
  - [Signal Analysis & Attribution](#signal-analysis--attribution)
  - [Strategy Logging & Observability](#strategy-logging--observability)
- [Strategy Design Patterns](#strategy-design-patterns)
- [Strategy Best Practices](#strategy-best-practices)
- [Design Principles Summary](#design-principles-summary)
- [UniverseSelectionModel](#universeselectionmodel)
- [Future Extensions](#future-extensions)

---

## Core Architecture

Simulor uses an **event-driven architecture** with **pluggable components**:

```text
┌─────────────────────────────────────────────────────────────┐
│                      Backtest Engine                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Data Provider│→ │   Strategy   │→ │ Execution Eng│     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│         ↓                 ↓                  ↓              │
│  ┌──────────────────────────────────────────────────┐      │
│  │            Portfolio / Risk Manager              │      │
│  └──────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

**Design Philosophy**: "Simple, Pluggable, Institutional-Grade"

- **Simple**: Core framework stays minimal - just event-driven skeleton
- **Pluggable**: Every component replaceable (data, execution, portfolio, risk)
- **Institutional**: Handles large-scale, multi-resolution, multi-symbol workflows

**Component Responsibilities**:

- **DataProvider**: Delivers filtered market data based on subscriptions
- **Strategy**: Reacts to events, generates signals, emits orders
- **ExecutionEngine**: Handles order routing, fills, slippage
- **Portfolio**: Tracks positions, cash, P&L
- **RiskManager**: Enforces risk limits (optional, user-provided)

---

## Core Strategy Features

This eliminates repetitive feature engineering code and ensures consistency across backtests and live trading.

### Event-Driven Architecture

Simulor strategies operate on an event-driven model where strategies react to market data events.

**Market Data Events**:

Market data events are the primary triggers for strategy logic. The framework supports multiple types of market data with varying granularities:

- **Tick events**: Process individual TradeTick and QuoteTick data for high-frequency strategies that need to react to every trade or quote update. Tick-level data provides the finest granularity but requires careful handling due to volume.

- **Bar events**: Process aggregated TradeBar and QuoteBar data representing time-based aggregations (OHLCV - Open, High, Low, Close, Volume). Bars are the most common data format for systematic strategies.

- **Resolution flexibility**: Subscribe to minute, hourly, or daily resolutions based on strategy needs. Different resolutions enable different trading styles - minute bars for intraday strategies, daily bars for swing trading, etc.

- **Multi-symbol processing**: Handle multiple securities simultaneously without blocking. When data arrives for multiple symbols, each triggers its own `on_data()` call, allowing strategies to maintain separate logic per symbol.

**Strategy Lifecycle Events**:

Beyond market data, strategies have lifecycle hooks for initialization and cleanup:

- **On Data**: Main event handler triggered by market data updates. This is where trading logic lives - analyzing data, generating signals, making decisions, and emitting orders. Called once per data event (per bar, per symbol, per resolution).

- **Warm-up Complete**: Notification when indicator warm-up period finishes and strategy is ready to trade. Strategies can check `is_warming_up` flag to avoid trading during initialization.

**Order Emission Pattern**:

How strategies communicate trading decisions to the execution engine:

- **Target Position Emission**: Strategies emit desired target positions (what they want to hold), not direct orders. When market conditions trigger a signal, the strategy communicates the desired portfolio state.

- **ExecutionModel Translation (single source of orders)**: The pluggable ExecutionModel component translates target positions into OrderSpec. This is the **only** place in the pipeline where OrderSpec are created. This separation allows the same strategy to use different execution algorithms (immediate market orders, TWAP, iceberg orders) without changing strategy code.

- **Event-driven execution**: When market conditions trigger a signal, target positions are calculated and passed to ExecutionModel without waiting for other events or batch processing. This enables rapid response to market movements.

- **Immediate feedback**: ExecutionModel provides immediate status about generated OrderSpec and their lifecycle (accepted/rejected/filled), enabling conditional logic based on execution results.

**Design Decision**: Strategies emit target positions through the pipeline (UniverseSelectionModel → AlphaModel → PortfolioConstructionModel → RiskModel → ExecutionModel), not by calling order methods directly. The ExecutionModel handles all order generation.

**Rationale**: Separating "what to hold" (targets) from "how to achieve it" (orders) enables:

- Pluggable execution algorithms without strategy changes
- Backtesting with different execution models for validation
- Production deployment with broker-specific execution logic
- Consistent strategy behavior across backtest/paper/live environments

---

### MarketEvent Structure

Each `on_data()` call receives a `MarketEvent` containing both trigger information (what caused this event) and context access (query all available data).

**Trigger Information** (what caused this event):

- `symbol: str` - Symbol that triggered this event (e.g., "AAPL"). This tells you which security's data arrived and caused this callback.

- `resolution: Resolution` - Resolution that triggered (MINUTE, DAILY, etc.). Useful when strategy subscribes to multiple resolutions - you can filter to only process certain resolutions.

- `current_bar: Bar` - The specific bar that triggered this call, containing OHLCV data (open, high, low, close, volume) plus timestamp. This is the "new" data that just arrived.

- `timestamp: datetime` - Event timestamp indicating when this data is effective. Critical for ensuring point-in-time correctness (no look-ahead bias).

**Context Access Methods** (query all cached data):

- `get_bar(symbol, resolution) -> Bar | None` - Get most recent bar for given symbol and resolution. Returns None if no data available yet. Useful for checking related securities or different resolutions.

- `get_bars(symbol, resolution, count) -> List[Bar]` - Get historical bars for indicator calculations. Returns list of bars ordered chronologically (oldest to newest), up to `count` bars. If insufficient data, returns what's available.

- `has_bar(symbol, resolution) -> bool` - Check if data exists for symbol/resolution before querying. Prevents None-checking logic when you just want to know if data is ready.

This design provides:

✅ **Trigger clarity**: Know exactly what caused this event (which symbol, which resolution, which bar)

✅ **Full context**: Access all resolutions and symbols, not just the trigger

✅ **Multi-resolution support**: Daily strategy can check minute data for intraday patterns

✅ **Multi-symbol support**: Trade SPY based on QQQ momentum, or run pairs trading

**Multi-Resolution Pattern**: Strategies can trade on minute bars while filtering based on daily trend. When a minute bar triggers `on_data()`, the strategy can query daily bars through `get_bars()` to check if the long-term trend is favorable before executing minute-level entries. This enables combining timeframes without complex data synchronization - the framework keeps all resolutions cached and available.

**Multi-Symbol Pattern**: When trading one symbol based on another (like trading SPY based on QQQ momentum), the strategy can query QQQ data via `get_bars("QQQ", ...)` even when the trigger was SPY. This eliminates the need for manual data synchronization or complex event coordination.

---

### Data Delivery Model: Subscription-Based Filtering

Simulor uses **subscription-based filtering** where the Engine's Subscription/Filter Layer delivers only the data requested by strategies. This architectural choice prioritizes memory efficiency and scalability.

**How It Works**:

1. Strategy calls `subscribe('AAPL', MINUTE)` and `subscribe('AAPL', DAILY)` during initialization
2. Engine registers these subscriptions with the Subscription/Filter Layer
3. Data Layer generates all market data (tick, minute, hourly, daily for all symbols)
4. Subscription/Filter Layer filters to only AAPL minute and daily data
5. Filtered MarketEvent objects are delivered to the strategy

**Why subscription-based filtering?**

✅ **Memory efficiency**: Don't load unnecessary data (critical for 1000+ symbol universes). If you only trade on daily bars, why load minute data for all symbols? Subscription filtering eliminates this waste at the Engine level.

✅ **Explicit dependencies**: Clear what data strategy needs. Reading subscriptions tells you exactly what data dependencies exist, making strategies easier to understand and optimize.

✅ **Institutional scale**: Filter at Engine layer, not in strategy code. When running 10,000 symbols, loading all resolutions for all symbols becomes prohibitive. The Subscription/Filter Layer keeps memory manageable.

**Design Decision**: Per-bar events with filtered delivery (not bundled timeslice).

**Rationale**: Institutional strategies handle thousands of symbols across multiple resolutions. Bundling all resolutions for all symbols into single timeslice doesn't scale - you'd need gigabytes of RAM for large universes. Filtered delivery keeps memory efficient (O(subscribed) not O(all)) while maintaining full context access through `get_bar()` and `get_bars()`. The slight inconvenience of explicit subscriptions is outweighed by massive scalability gains.

---

### Strategy Protocol (No Forced Inheritance)

Simulor uses **protocol-based interfaces** (duck typing), not inheritance.

**Minimal Requirements**: Only `on_data(MarketEvent)` is required. The strategy must implement this method to receive market data events.

**No Base Class**: Strategies don't inherit from `BaseStrategy` or similar. They just implement the protocol (duck typing). Python's type hints can validate compliance without runtime inheritance overhead.

**Why protocol-based?**

✅ **Flexibility**: No forced inheritance, implement only what you need. Want custom initialization? Add whatever methods you need.

✅ **Type safety**: Python type hints validate interface compliance at development time. IDEs can catch missing methods without requiring inheritance.

✅ **Simplicity**: Minimal boilerplate. A complete strategy can be as simple as a class with one method (`on_data`).

✅ **Composition over inheritance**: Easier to compose strategies from multiple components without complex inheritance hierarchies.

**Contract**: The Engine calls `on_data(MarketEvent)` for each market event. The strategy analyzes data and updates its internal state, which the PortfolioConstructionModel uses to calculate target positions. That's the entire contract - simple and clear.

---

### Signal Generation Framework

Unified signal system supporting multiple signal sources:

#### Signal Types

- **ML Predictions**: Machine learning model outputs (classification, regression, ranking)
- **Technical Indicators**: Momentum, mean reversion, volatility-based signals
- **Fundamental Signals**: Financial ratio analysis, earnings quality, valuation metrics
- **Sentiment Signals**: News sentiment, social media analysis, analyst ratings
- **Market Microstructure**: Order flow imbalance, spread analysis, volume patterns
- **Composite Signals**: Combinations of multiple signal sources

**Signal Structure**:
Each signal contains:

- **Direction**: Forecast direction, either long, short, or flat
- **Strength**: Forecast magnitude from 0.0 (no conviction) to 1.0 (maximum conviction)
- **Confidence**: Probability score from 0.0 to 1.0
- **Timestamp**: Exact time of signal generation
- **Metadata**: Additional context (model version, indicator parameters, feature importance)

Signals are immutable artifacts. A model may emit several signals for the same
instrument; aggregating them is the responsibility of portfolio construction,
not of the framework.

**Design Decision**: All signal types produce a standardized output format with normalized strength and confidence scores. This enables cross-signal comparison and combination.

**Rationale**: Strategies often combine multiple signals. A unified interface allows easy integration of ML models, technical indicators, and alternative data without changing strategy logic.

---

### Machine Learning Integration

First-class support for ML-driven strategies:

#### ML Model Integration

- **Pre-trained models**: Load scikit-learn, XGBoost, LightGBM, PyTorch, TensorFlow models
- **Feature extraction**: Automatic feature engineering from historical market data
- **Model versioning**: Track which model version generated each signal
- **Feature importance**: Capture and log feature contributions for analysis
- **Prediction caching**: Store predictions for reproducibility and performance

#### ML Signal Generation

- **Classification**: Binary (buy/sell) or multi-class (buy/hold/sell) predictions
- **Regression**: Predict returns, volatility, or price targets
- **Ranking**: Relative strength across universe of securities
- **Probability scores**: Convert to confidence levels for position sizing
- **Ensemble methods**: Combine multiple models with weighted voting

**Design Decision**: ML models are treated as signal generators that plug into the strategy framework. The strategy layer handles feature extraction, prediction timing, and signal normalization.

**Rationale**: Modern quant strategies increasingly rely on ML. The framework should make ML integration straightforward while maintaining backtesting integrity (no look-ahead bias, proper train/test splits).

---

### AI Agent Strategies (Experimental)

Support for LLM-powered trading agents with clear limitations:

#### AI Agent Capabilities

- **Natural language reasoning**: LLM analyzes market conditions and generates trading decisions
- **Multi-agent systems**: Multiple specialized agents (technical analyst, risk manager, sentiment analyst) collaborate
- **Consensus voting**: Agents vote on trading decisions with weighted confidence
- **Reasoning transparency**: Capture agent explanations for audit trail

#### AI Agent Constraints

- **Non-determinism warning**: LLM outputs vary across runs unless temperature=0
- **Reproducibility caching**: Cache all LLM responses for exact backtest reproduction
- **Latency limitations**: 500ms+ API response time limits to daily/hourly strategies only
- **Cost tracking**: Monitor API token usage and set budget limits
- **Regulatory considerations**: Some jurisdictions restrict AI-autonomous trading

**Design Decision**: AI agents are supported as an experimental feature with explicit reproducibility mechanisms (response caching) but clear warnings about limitations.

**Rationale**: AI agents represent cutting-edge research but have practical limitations (cost, latency, non-determinism). We enable experimentation while being honest about constraints and not promising magic solutions.

**⚠️ Production Warning**: AI agent strategies are best suited for research and signal validation. For live trading, use AI-generated signals as inputs to deterministic strategy logic rather than fully autonomous AI decision-making.

---

### Environment Consistency (Backtest/Paper/Live)

Strategy code works unchanged across all execution environments:

#### Unified Interface

- **Same data structure**: MarketEvent objects identical in backtest and live
- **Same API**: History access, signal generation, target calculation unchanged
- **Environment-agnostic**: Strategy doesn't know if it's running in backtest or live
- **Target-based output**: Strategies calculate target positions, ExecutionModel translates to OrderSpec

**Environment Differences** (handled by execution layer, not strategy):

- **Backtest**: Simulated fills based on historical data
- **Paper trading**: Real-time data but simulated execution
- **Live trading**: Real broker API with actual order execution

**Design Decision**: Strategies emit target positions through the pipeline (AlphaModel → PortfolioConstructionModel → RiskModel → ExecutionModel). The ExecutionModel generates OrderSpec appropriate for the environment.

**Rationale**: If strategy code requires changes when moving from backtest to live, you risk introducing bugs and invalidating backtest results. Strategies express desired portfolio state (targets); the ExecutionModel translates them appropriately for each environment (simulated fills vs broker API calls).

---

### Multi-Strategy Portfolio

Run multiple strategies simultaneously with capital allocation:

#### Portfolio Composition

- **Weight allocation**: Assign percentage of capital to each sub-strategy
- **Independent execution**: Each strategy operates independently with its own logic
- **Order aggregation**: Combine orders from multiple strategies for same symbol
- **Conflict resolution**: Handle cases where strategies emit conflicting order types
- **Dynamic rebalancing**: Adjust strategy weights based on performance

#### Portfolio Management

- **Capital allocation**: Distribute available capital across strategies
- **Risk budgeting**: Allocate risk (volatility) limits per strategy
- **Correlation accounting**: Consider strategy correlation in allocation
- **Performance attribution**: Track returns and risk by strategy
- **Strategy rotation**: Enable/disable strategies based on market regime

**Design Decision**: Multi-strategy portfolios are implemented as composite strategies that buffer and aggregate orders from sub-strategies, applying weight scaling and conflict resolution before submission to the execution engine.

**Rationale**: Professional traders run multiple strategies to diversify alpha sources and reduce strategy-specific risk. Portfolio-level order aggregation enables capital efficiency (combine orders for same symbol) and systematic risk control.

---

### Warm-Up Period Management

Ensure indicators have sufficient historical data before trading:

#### Warm-Up Configuration

- **Time-based**: Specify calendar days needed (e.g., 200 days for SMA-200)
- **Bar-based**: Specify number of bars at specific resolution (e.g., 200 daily bars)
- **Resolution-specific**: Different warm-up per data resolution
- **Automatic data pre-loading**: Engine loads required historical data before strategy start

#### Warm-Up Behavior

- **No trading during warm-up**: Strategy receives data but doesn't emit orders
- **Indicator initialization**: Populate moving averages, volatility estimates, ML features
- **History accumulation**: Build sufficient data history for lookback calculations
- **Warm-up completion event**: Notify strategy when ready to trade

**Design Decision**: Warm-up is declarative - strategies specify requirements, engine handles data loading. During warm-up, `is_warming_up` flag prevents order emission.

**Rationale**: Many indicators (SMA-200, Bollinger Bands, ATR) require historical data. Trading with incomplete indicators produces incorrect signals. Automatic warm-up ensures indicators are ready before first trade.

---

## Advanced Strategy Features

### Indicator Library Integration

Built-in support for common technical indicators for strategies that use technical analysis:

#### Momentum Indicators

- **Moving averages**: SMA (Simple Moving Average), EMA (Exponential Moving Average), WMA (Weighted Moving Average), VWAP (Volume-Weighted Average Price). These smooth price data to identify trends and generate crossover signals.

- **Oscillators**: RSI (Relative Strength Index), Stochastic, Williams %R. Bounded indicators that identify overbought/oversold conditions and potential reversal points.

- **Trend**: MACD (Moving Average Convergence Divergence), ADX (Average Directional Index), Parabolic SAR. Indicators that measure trend strength and direction.

- **Volume**: OBV (On-Balance Volume), Chaikin Money Flow, Volume Profile. Volume-based indicators that confirm price movements and identify accumulation/distribution.

#### Volatility Indicators

- **Bollinger Bands**: Price envelopes positioned at standard deviations above and below a moving average. Expand during high volatility, contract during low volatility. Used for mean reversion and breakout strategies.

- **ATR (Average True Range)**: Measures market volatility by calculating the average range between high and low prices. Essential for position sizing and stop-loss placement.

- **Keltner Channels**: Volatility-based price channels using ATR instead of standard deviation. Alternative to Bollinger Bands with different volatility characteristics.

- **Historical volatility**: Realized volatility calculation from historical price movements. Used for comparing to implied volatility and vol trading strategies.

#### Custom Indicators

- **User-defined**: Implement custom technical indicators using historical bar data. The framework provides access to OHLCV data through `get_bars()`, enabling custom calculations.

- **Multi-timeframe**: Calculate indicators across different resolutions (e.g., RSI on both daily and hourly bars). Access multiple resolutions simultaneously through MarketData context methods.

- **Composite indicators**: Combine multiple indicators with custom logic (e.g., weighted combination of momentum and volatility signals). Create sophisticated signal generators from building blocks.

**Design Decision**: Indicators are helper utilities that strategies can use, not core strategy components. Strategies can use built-in indicators or implement custom calculations.

**Rationale**: Technical indicators are common building blocks for quantitative strategies. Providing high-quality implementations saves development time and ensures correctness (proper handling of NaN values, edge cases with insufficient data, rolling window calculations). However, they remain optional tools - strategies focused on ML or fundamental analysis don't need to use them.

---

### Scheduled Events & Time-Based Logic

Execute logic at specific times regardless of market data arrival. Many strategies need time-based logic that shouldn't depend on when market data happens to arrive.

#### Scheduling Options

- **Daily schedules**: Run logic at specific time each day (e.g., 9:31 AM market open). Useful for strategies that need to act at specific intraday times regardless of data arrival patterns.

- **Weekly schedules**: Run on specific days of week (e.g., Monday morning for "weekend effect" strategies, Friday afternoon for week-end position cleanup).

- **Monthly schedules**: Run on specific dates (e.g., first trading day of month for monthly rebalancing, earnings season patterns).

- **Interval schedules**: Run every N minutes/hours for periodic checks (e.g., check risk exposure every hour, rebalance every 4 hours).

- **One-time events**: Schedule future one-time execution (e.g., "exit this position in 2 days" for time-based exits, "check earnings date" for event-driven logic).

#### Common Use Cases

- **Daily rebalancing**: Adjust positions to target weights each day at close. Market-on-close orders need timing independent of last bar arrival.

- **Universe updates**: Refresh symbol selection weekly or monthly. Dynamic universes change periodically, not on market data events.

- **Risk checks**: Monitor portfolio metrics at regular intervals. Risk limits should be checked on schedule, not only when data arrives.

- **Position cleanup**: Close positions before weekend/holidays to avoid gap risk. Time-based logic ensures positions are closed regardless of market activity.

- **Reporting**: Generate performance snapshots periodically for monitoring and alerting. Regular reporting cadence independent of market events.

**Design Decision**: Scheduled events are separate from market data events. Strategies can register time-based callbacks that execute independently of data flow.

**Rationale**: Many strategies have time-dependent logic (daily rebalance at close, Monday morning trades, end-of-month positioning) that shouldn't depend on data arrival timing. If your strategy needs to act at 4 PM market close, it should fire at 4 PM, not "whenever the last bar of the day happens to arrive". Explicit scheduling decouples time-based logic from data-driven logic, making strategies more predictable and easier to reason about.

---

### Parameter Optimization & Hyperparameter Tuning

Systematic parameter exploration for strategy improvement through automated search and validation:

#### Optimization Methods

- **Grid search**: Exhaustive search over parameter combinations
- **Random search**: Random sampling of parameter space
- **Bayesian optimization**: Smart search using probabilistic models
- **Genetic algorithms**: Evolutionary optimization approach
- **Walk-forward optimization**: Out-of-sample validation during optimization

#### Optimization Targets

- **Return metrics**: Total return, CAGR, absolute return
- **Risk-adjusted metrics**: Sharpe ratio, Sortino ratio, Calmar ratio
- **Risk metrics**: Maximum drawdown, VaR, CVaR
- **Custom objectives**: User-defined fitness functions
- **Multi-objective**: Optimize for multiple criteria simultaneously

#### Overfitting Prevention

- **Train/test splits**: Separate in-sample and out-of-sample periods
- **Cross-validation**: K-fold cross-validation on time series
- **Walk-forward windows**: Rolling optimization and testing windows
- **Parameter stability**: Penalize parameters that vary widely across periods
- **Complexity penalties**: Favor simpler strategies with fewer parameters

**Design Decision**: Optimization is a backtest engine capability that runs strategies with different parameters, not a strategy feature itself.

**Rationale**: Parameter optimization requires running backtests many times with different settings. This is infrastructure-level functionality, though strategies should be designed with parameterization in mind.

---

### Strategy State Management

Maintain strategy state across market events:

#### State Types

- **Indicator state**: Current indicator values (moving averages, oscillators)
- **Position context**: Historical entry prices, holding periods, P&L
- **Signal history**: Previous signals for pattern recognition
- **Custom state**: User-defined strategy variables and counters

#### State Persistence

- **Checkpoint saving**: Serialize strategy state at intervals
- **Resume capability**: Restart strategy from saved state
- **State versioning**: Track state changes over time
- **Debugging support**: Inspect state at any point in backtest

**Design Decision**: Strategies maintain internal state variables. The framework provides optional state persistence for long-running strategies and debugging.

**Rationale**: Strategies often need to remember past decisions, track position history, or maintain indicator state. Built-in state management ensures consistency and enables pause/resume functionality.

---

## Strategy Analysis & Debugging

### Signal Analysis & Attribution

Understand signal quality and contribution:

#### Signal Metrics

- **Hit rate**: Percentage of profitable signals
- **Signal strength correlation**: How signal strength relates to subsequent returns
- **Confidence calibration**: Whether confidence scores match actual probabilities
- **Signal decay**: How long signals remain predictive
- **Cross-signal correlation**: Redundancy between different signals

#### Attribution Reporting

- **Signal contribution**: Which signals drove profitable trades
- **Feature importance**: For ML signals, which features mattered most
- **Time-series analysis**: Signal performance over time
- **Regime analysis**: Signal performance in different market conditions

**Design Decision**: Signal metadata (strength, confidence, source) is captured and logged, enabling detailed post-backtest analysis of signal quality.

**Rationale**: Understanding why a strategy works (or doesn't) requires decomposing performance into signal contributions. Rich signal metadata enables this analysis without mixing strategy concerns with execution/analytics concerns.

---

### Strategy Logging & Observability

Track strategy behavior for debugging and analysis:

#### Logging Capabilities

- **Decision logging**: Why strategy made each decision
- **Signal logging**: All generated signals with metadata
- **State snapshots**: Periodic dumps of strategy internal state
- **Error logging**: Exceptions, warnings, data quality issues
- **Custom logging**: User-defined log messages at any point

#### Debugging Tools

- **Replay capability**: Re-run strategy with same data and state
- **Breakpoint support**: Pause strategy at specific conditions
- **State inspection**: Examine all internal variables at any timestamp
- **Data validation**: Verify input data quality during execution
- **Performance profiling**: Identify computational bottlenecks

**Design Decision**: Logging is structured and queryable, not just text dumps. Each log entry includes timestamp, strategy state, and context.

**Rationale**: Backtest debugging requires understanding what strategy knew at each decision point. Rich logging enables troubleshooting, regulatory compliance, and strategy improvement.

---

## Strategy Design Patterns

### Common Strategy Archetypes

#### Trend Following

- **Momentum strategies**: Buy winners, sell losers
- **Breakout strategies**: Enter on new highs/lows
- **Moving average crossovers**: Golden cross / death cross patterns
- **Trend strength filters**: ADX, R-squared trend indicators

#### Mean Reversion

- **Statistical arbitrage**: Pairs trading, basket trading
- **Bollinger Band reversals**: Buy oversold, sell overbought
- **Z-score mean reversion**: Standard deviation-based entries
- **Correlation trading**: Exploit temporary divergences

#### Market Making

- **Spread capture**: Profit from bid-ask spread
- **Inventory management**: Balance long/short positions
- **Adverse selection avoidance**: Detect informed flow
- **Optimal quoting**: Dynamic bid/ask placement

#### Factor Investing

- **Value factors**: P/E, P/B, dividend yield
- **Quality factors**: ROE, profit margins, earnings stability
- **Momentum factors**: Price momentum, earnings momentum
- **Multi-factor models**: Combine factors with optimization

#### Volatility Trading

- **Volatility mean reversion**: Sell high IV, buy low IV
- **VIX strategies**: Trade volatility indices
- **Straddles/strangles**: Options volatility strategies
- **Gamma scalping**: Delta-hedged volatility extraction

**Design Decision**: Simulor doesn't enforce specific strategy patterns but provides tools to implement any approach.

**Rationale**: Different market conditions favor different strategies. The framework should be flexible enough to support diverse trading philosophies.

---

## Strategy Best Practices

### Design Principles

**Modularity**: Separate signal generation, position sizing, and risk management into distinct components.

**Testability**: Design strategies to be easily backtested with different parameters and data sets.

**Transparency**: Log decisions and maintain audit trails for regulatory compliance and debugging.

**Robustness**: Handle missing data, outliers, and market anomalies gracefully without crashing.

**Efficiency**: Optimize computational performance for large universes and high-frequency data.

### Common Pitfalls to Avoid

**Overfitting**: Avoid excessive parameters or optimization on limited data - strategies should generalize.

**Look-ahead bias**: Ensure strategy only uses information available at decision time (point-in-time data).

**Data snooping**: Avoid testing multiple strategies on same data without proper validation methodology.

**Ignoring costs**: Account for commissions, slippage, market impact, and financing costs.

**Regime dependency**: Test strategies across different market conditions (bull, bear, sideways, high/low volatility).

**Survivorship bias**: Include delisted and bankrupt securities in backtest universe.

### Production Readiness

**Error handling**: Catch and handle all exceptions without crashing the strategy.

**Resource management**: Monitor memory usage, CPU utilization, and API rate limits.

**Graceful degradation**: Continue operating with reduced functionality if non-critical components fail.

**Monitoring alerts**: Set up notifications for unexpected behavior or performance degradation.

**Documentation**: Maintain clear documentation of strategy logic, parameters, and assumptions.

---

## Design Principles Summary

Simulor's strategy module follows these core principles:

1. **Event-Driven Foundation**: Strategies react to events via `on_data()`, not declarative DSL. The framework delivers market data events and strategies respond with immediate order emission. This creates a natural, low-latency flow from data to decision to execution.

2. **Pluggable Components**: UniverseSelectionModel, AlphaModel, PortfolioConstructionModel, RiskModel, ExecutionModel all replaceable. Every major component can be swapped out - use different universe filters, alpha models, portfolio constructors, or execution engines without changing other components. This enables customization for specific needs.

3. **Filtered Data Delivery**: Strategies receive only subscribed resolutions (memory-efficient). Rather than bundling all data for all symbols and resolutions, strategies explicitly subscribe to what they need. This enables handling 10,000+ symbol universes without excessive memory consumption.

4. **Protocol-Based Interface**: No forced inheritance, just implement protocol methods. Components duck-type the required interface without inheriting from base classes. This provides flexibility and reduces coupling.

5. **Target Position Emission**: Strategies emit target positions through the component pipeline. When signals trigger, strategies communicate desired portfolio state via AlphaModel → PortfolioConstructionModel → RiskModel → ExecutionModel. The ExecutionModel generates appropriate OrderSpec.

6. **Environment-Agnostic**: Same strategy code for backtest/paper/live. Strategies don't know whether they're running in simulation or production. The execution engine handles environment-specific mechanics, enabling validated backtest code to run live without modification.

7. **Universe as Component**: UniverseSelectionModel is a first-class pluggable component with point-in-time accuracy and survivorship bias prevention built-in. Static lists, dynamic filters, or custom logic all use the same interface.

**Philosophy**: Keep the framework simple and focused. Let users build complexity where they need it through pluggable components and custom logic. The core framework provides event-driven structure and data delivery - everything else is optional or user-implemented.

---

## UniverseSelectionModel

Simulor includes `UniverseSelectionModel` as a pluggable component for dynamic universe selection.

### Design Philosophy

Universe selection is a first-class component in the strategy pipeline:

- **Pluggable**: Swap between static lists, dynamic filters, or custom logic
- **Point-in-time**: Automatically handles survivorship bias with historical accuracy
- **Efficient**: Coarse-fine filtering for large universes (10,000+ symbols)
- **Rebalanceable**: Update universe on custom schedules (daily, weekly, monthly)

### Built-in Implementations

**Static**: Fixed list of symbols for strategies trading known instruments (blue chips, specific sector, benchmark constituents).

**Top**: Top N securities by metric (market cap, volume, momentum). Useful for strategies that always trade the largest/most liquid stocks. Rebalances periodically to maintain top N ranking.

**Liquid**: Filter by liquidity criteria (minimum volume, price, maximum spread). Ensures all traded symbols meet minimum liquidity requirements. Essential for strategies with large position sizes.

**Fundamental**: Filter by fundamental metrics (market cap, P/E ratio, ROE, debt-to-equity). Enables strategies focused on quality stocks or value investing. Combines multiple fundamental criteria.

**CoarseFine**: Two-stage filtering for efficiency. Fast coarse filter on price/volume narrows candidates, then expensive fundamental filter on the reduced set. Critical for handling large universes (10,000+ symbols) efficiently.

### Custom UniverseSelectionModel

Users can implement custom selection logic for specialized strategies like sector rotation (rotate to best performing sector), pairs trading (select correlated pairs), statistical arbitrage (find mean-reverting pairs), or regime-based universes (different symbols in different market regimes).

### Point-in-Time Universe

The framework ensures point-in-time accuracy:

- **Survivorship bias prevention**: Includes delisted/bankrupt securities that existed at each historical timestamp
- **Historical index composition**: S&P 500 on 2020-01-01 contains exactly the 500 stocks in the index at that date, not current constituents
- **Corporate actions**: Handles mergers, spinoffs, ticker changes automatically
- **Data availability**: Only trades symbols with available data at each timestamp, preventing look-ahead bias

### Performance Considerations

For large universes (10,000+ symbols):

**Use CoarseFine filtering**: Fast coarse filter (price/volume) before expensive fine filter (fundamentals). Reduces computational cost by 20x or more.

**Cache universe results**: Don't recompute on every bar, use rebalance schedules (daily, weekly, monthly). Most strategies don't need to update universe every bar.

**Limit fine filter scope**: Coarse filter should reduce universe to <1000 symbols before fine filtering. Fundamental data queries are expensive.

**Use vectorized operations**: Batch fundamental data queries instead of per-symbol lookups. Database/API calls should fetch data for multiple symbols simultaneously.

**Efficiency Pattern**: 10,000 symbols → Coarse filter (fast) → 500 symbols → Fine filter (expensive) → 50 symbols. This is 20x faster than running fine filter on all 10,000 symbols.

---

## Future Extensions

### Time-Travel Debugging

Simulor will support capturing complete strategy state at every timestamp during backtesting, enabling powerful debugging capabilities that go far beyond traditional print-statement logging.

**Planned Features**:

- **Complete state capture**: All signals, positions, orders, market data at each timestamp. Create a complete, queryable history of strategy execution that can be explored interactively after the backtest completes.

- **Interactive timeline navigation**: Jump to any point and inspect full state. Instantly navigate to any timestamp, step forward/backward bar-by-bar, search for specific events (e.g., "when did signal cross threshold?").

- **What-if analysis**: Fork timeline and replay with different decisions. Override a past decision and replay forward to see outcome. Compare timelines to see how different decisions affected performance. Answers questions like "what if I had exited 2 hours earlier?"

- **Conditional breakpoints**: Pause when specific conditions occur (signal crosses threshold, position P&L exceeds limit, drawdown reaches level). Inspect full state when breakpoint triggers.

- **Visual timeline interface**: Interactive charts with clickable timeline, signal strength heatmaps, trade markers, expandable detail panels, side-by-side timeline comparison.

**Implementation Efficiency**:

- Structural sharing (like Git commits) - only store deltas
- Hot/cold storage - recent in memory, older compressed to disk
- Lazy loading - reconstruct state on-demand

**Design Decision**: Time-travel debugging trades storage space for debugging power. This is optional and enabled only when needed.

**Rationale**: Understanding why a strategy made specific decisions is critical. Traditional logging requires predicting what to log. Time-travel captures everything, letting you investigate any question post-hoc. The "what-if" capability enables rapid exploration of strategy variations without full backtests.

---

### Incremental Backtesting

Simulor will support caching intermediate results to dramatically accelerate strategy iteration by only recomputing what changed.

**Planned Features**:

- **Dependency graph analysis**: Identify which components depend on which parameters. Automatically build a DAG of all computational steps and track data lineage.

- **Intelligent caching**: Only recompute what changed. When you change one parameter (e.g., RSI period), reuse cached universe selection, ML predictions, and fundamental signals. Only recompute RSI and downstream components.

- **Parameter sweeps**: 10x faster optimization through cache reuse. Shared computation pooling (components used by multiple parameter combinations computed once), parallel execution across CPU cores, smart scheduling to maximize cache hits.

- **Automatic invalidation**: Detect code/data changes and invalidate stale caches. Framework detects source code changes, data file modifications (via hashing), model updates, ensuring cache correctness while maximizing reuse.

**Example Speedup**: When changing RSI period from 14 to 20, the framework reuses cached universe selection, ML predictions, and fundamental signals (unchanged), only recomputing RSI calculations and downstream components. A 100-combination grid search that would take 8 hours can complete in under 2 hours.

**Design Decision**: Incremental backtesting is transparent - strategies don't need to be cache-aware. Framework handles caching automatically based on dependency analysis.

**Rationale**: Fast iteration is crucial for strategy development. Waiting hours for parameter tweaks kills productivity. Caching is complex to implement correctly (invalidation, consistency, storage) - the framework handles this so developers don't have to. The speedup enables exploring 10x more parameter combinations in the same time, leading to better strategies.

---

### Reinforcement Learning Strategies

- **RL agent integration**: Support for Q-learning, PPO, A3C agents
- **Custom reward functions**: Define trading objectives as RL rewards
- **Environment simulation**: Market environment for RL training
- **Policy deployment**: Deploy trained RL policies as strategies

### Multi-Asset Strategies

- **Cross-asset signals**: Use equity signals for options/futures trading
- **Hedging strategies**: Automatic hedge construction across asset classes
- **Spread trading**: Trade relative value across related instruments
- **Currency hedging**: Automatic FX hedging for international portfolios

### Advanced ML Techniques

- **Online learning**: Models that update with new data
- **Transfer learning**: Apply models trained on one asset to others
- **Ensemble methods**: Combine multiple ML models systematically
- **AutoML integration**: Automated model selection and tuning
