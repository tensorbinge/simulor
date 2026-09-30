"""BacktestResult dataclass with eager metric computation.

The BacktestResult is the main output of a backtest run. It accepts time-series
data and trade history, then eagerly computes ALL metrics in __post_init__.

This design ensures:
- Consistent metric calculations across all analyses
- Immediate feedback on backtest quality
- Simplified debugging (all data computed upfront)
- No lazy evaluation complexity

Metrics are computed using calendar-day methodology with institutional accuracy.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from simulor.analytics import metrics as calc
from simulor.analytics.returns import (
    calculate_cagr,
    calculate_daily_returns,
    calculate_total_return,
    resample_to_calendar_days,
)

if TYPE_CHECKING:
    import plotly.graph_objects as go

    from simulor.portfolio.recorder import TimeSeriesRecorder
    from simulor.types import Fill

__all__ = ["BacktestResult", "StrategyMetrics"]


@dataclass
class StrategyMetrics:
    """Per-strategy performance metrics.

    Computed eagerly for each strategy in a multi-strategy backtest.
    """

    name: str
    total_return: Decimal
    annualized_return: Decimal
    cagr: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    max_drawdown: Decimal
    volatility: Decimal
    num_trades: int


@dataclass
class BacktestResult:
    """Comprehensive backtest results with eager metric computation.

    This class accepts raw backtest outputs (time-series recorders, trades, etc.)
    and eagerly computes all performance metrics in __post_init__.

    All timestamps are converted to UTC and equity is resampled to calendar days
    for proper handling of mixed-asset portfolios (stocks + crypto).

    Attributes:
        Inputs (set by user):
        - global_recorder: Global portfolio time-series
        - strategy_recorders: Per-strategy time-series recorders
        - trades: All executed trades
        - initial_capital: Starting capital
        - start_date: Backtest start timestamp
        - end_date: Backtest end timestamp
        - benchmark_returns: Optional benchmark time series

        Computed metrics (set in __post_init__):
        - total_return, annualized_return, cagr
        - sharpe_ratio, sortino_ratio, calmar_ratio
        - max_drawdown, max_drawdown_duration, recovery_time
        - average_drawdown, underwater_periods
        - volatility, downside_deviation, best_day, worst_day
        - win_rate, profit_factor, expectancy, avg_win, avg_loss, num_trades
        - alpha, beta, correlation, information_ratio, tracking_error (if benchmark)
        - strategy_metrics: Per-strategy performance breakdown
    """

    # Input data (required)
    global_recorder: TimeSeriesRecorder
    trades: list[Fill]
    initial_capital: Decimal
    start_date: datetime
    end_date: datetime

    # Optional inputs
    strategy_recorders: dict[str, TimeSeriesRecorder] = field(default_factory=dict)
    benchmark_returns: list[tuple[datetime, float]] | None = None

    # Computed metrics (initialized in __post_init__)
    # Returns
    final_capital: Decimal = Decimal("0")
    total_return: Decimal = Decimal("0")
    annualized_return: Decimal = Decimal("0")
    cagr: Decimal = Decimal("0")

    # Risk-adjusted
    sharpe_ratio: Decimal = Decimal("0")
    sortino_ratio: Decimal = Decimal("0")
    calmar_ratio: Decimal = Decimal("0")

    # Drawdowns
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_duration: int = 0
    recovery_time: int | None = None
    average_drawdown: Decimal = Decimal("0")
    underwater_periods: list[dict[str, Any]] = field(default_factory=list)

    # Risk
    volatility: Decimal = Decimal("0")
    downside_deviation: Decimal = Decimal("0")
    best_day: Decimal = Decimal("0")
    worst_day: Decimal = Decimal("0")

    # Trade stats
    win_rate: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    expectancy: Decimal = Decimal("0")
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")
    num_trades: int = 0

    # Benchmark metrics (computed if benchmark provided)
    alpha: Decimal | None = None
    beta: Decimal | None = None
    correlation: Decimal | None = None
    information_ratio: Decimal | None = None
    tracking_error: Decimal | None = None

    # Per-strategy metrics
    strategy_metrics: dict[str, StrategyMetrics] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Eagerly compute all metrics from input data.

        This method:
        1. Converts all timestamps to UTC
        2. Resamples equity to calendar days
        3. Calculates daily returns
        4. Computes all performance metrics
        5. Processes per-strategy metrics
        6. If benchmark provided: aligns dates and computes relative metrics
        """
        # Get equity series from global recorder
        equity_series = self.global_recorder.get_equity_series()

        if not equity_series:
            warnings.warn("No equity data recorded - all metrics will be zero", stacklevel=2)
            return

        timestamps, equity_values = zip(*equity_series, strict=False)
        timestamps_list = list(timestamps)
        equity_values_list = list(equity_values)

        # Resample to calendar days for proper mixed-portfolio handling
        timestamps_daily, equity_daily = resample_to_calendar_days(timestamps_list, equity_values_list)

        # Calculate daily returns
        daily_returns = calculate_daily_returns(equity_daily)

        # Number of calendar days
        num_days = (self.end_date - self.start_date).days

        # ====================================================================
        # Compute all metrics
        # ====================================================================

        # Returns
        self.final_capital = equity_values_list[-1]
        self.total_return = Decimal(str(calculate_total_return(self.initial_capital, equity_values_list[-1])))
        self.annualized_return = Decimal(
            str(calc.calculate_annualized_return(self.initial_capital, equity_values_list[-1], num_days))
        )
        self.cagr = Decimal(str(calculate_cagr(self.initial_capital, equity_values_list[-1], num_days)))

        # Risk-adjusted
        self.sharpe_ratio = Decimal(str(calc.calculate_sharpe_ratio(daily_returns)))
        self.sortino_ratio = Decimal(str(calc.calculate_sortino_ratio(daily_returns)))

        # Drawdowns
        drawdown_series = calc.calculate_drawdown_series(equity_daily)
        max_dd_info = calc.calculate_max_drawdown(equity_daily, timestamps_daily)

        self.max_drawdown = Decimal(str(max_dd_info["pct"]))
        self.max_drawdown_duration = max_dd_info["duration_days"]
        self.recovery_time = calc.calculate_recovery_time(max_dd_info)
        self.average_drawdown = Decimal(str(calc.calculate_average_drawdown(drawdown_series)))
        self.underwater_periods = calc.calculate_underwater_periods(equity_daily, timestamps_daily)

        # Calmar ratio (needs max drawdown)
        self.calmar_ratio = Decimal(str(calc.calculate_calmar_ratio(self.annualized_return, self.max_drawdown)))

        # Risk measures
        self.volatility = Decimal(str(calc.calculate_volatility(daily_returns)))
        self.downside_deviation = Decimal(str(calc.calculate_downside_deviation(daily_returns)))
        self.best_day = Decimal(str(calc.calculate_best_day(daily_returns)))
        self.worst_day = Decimal(str(calc.calculate_worst_day(daily_returns)))

        # Trade statistics
        self.num_trades = calc.calculate_trade_count(self.trades)
        self.win_rate = Decimal(str(calc.calculate_win_rate(self.trades)))
        self.profit_factor = Decimal(str(calc.calculate_profit_factor(self.trades)))
        self.expectancy = Decimal(str(calc.calculate_expectancy(self.trades)))
        self.avg_win = Decimal(str(calc.calculate_avg_win(self.trades)))
        self.avg_loss = Decimal(str(calc.calculate_avg_loss(self.trades)))

        # ====================================================================
        # Benchmark metrics (if provided)
        # ====================================================================

        if self.benchmark_returns:
            self._compute_benchmark_metrics(daily_returns, timestamps_daily)

        # ====================================================================
        # Per-strategy metrics
        # ====================================================================

        for strategy_name, recorder in self.strategy_recorders.items():
            self.strategy_metrics[strategy_name] = self._compute_strategy_metrics(strategy_name, recorder)

    def _compute_benchmark_metrics(
        self, portfolio_returns: list[Decimal], portfolio_timestamps: list[datetime]
    ) -> None:
        """Compute alpha, beta, correlation, IR, and tracking error vs benchmark.

        Args:
            portfolio_returns: Daily returns of portfolio
            portfolio_timestamps: Timestamps of portfolio returns
        """
        if not self.benchmark_returns:
            return

        # Align benchmark returns with portfolio dates (convert to Decimal)
        bench_map = {ts.date(): ret for ts, ret in self.benchmark_returns}

        aligned_portfolio: list[Decimal] = []
        aligned_benchmark: list[Decimal] = []

        for i, ts in enumerate(portfolio_timestamps[1:]):  # Skip first (no return)
            date = ts.date()
            if date in bench_map:
                aligned_portfolio.append(Decimal(str(portfolio_returns[i])))
                aligned_benchmark.append(Decimal(str(bench_map[date])))

        if len(aligned_portfolio) < 30:
            warnings.warn(
                f"Insufficient overlapping data for benchmark analysis: "
                f"{len(aligned_portfolio)} days. Need at least 30.",
                stacklevel=2,
            )
            return

        # Use Decimal math for statistics
        n = len(aligned_portfolio)
        n_dec = Decimal(n)

        mean_port = sum(aligned_portfolio) / n_dec
        mean_bench = sum(aligned_benchmark) / n_dec

        covariance = sum((aligned_portfolio[i] - mean_port) * (aligned_benchmark[i] - mean_bench) for i in range(n)) / (
            n_dec - Decimal(1)
        )

        bench_variance = sum((r - mean_bench) ** 2 for r in aligned_benchmark) / (n_dec - Decimal(1))

        if bench_variance > Decimal(0):
            self.beta = covariance / bench_variance
        else:
            self.beta = Decimal("0")

        # Alpha: simplified
        self.alpha = mean_port - (self.beta * mean_bench) if self.beta else Decimal("0")

        # Correlation
        port_var = sum((r - mean_port) ** 2 for r in aligned_portfolio) / (n_dec - Decimal(1))
        port_std = port_var.sqrt()
        bench_std = bench_variance.sqrt()

        if port_std > Decimal(0) and bench_std > Decimal(0):
            self.correlation = covariance / (port_std * bench_std)
        else:
            self.correlation = Decimal("0")

        # Tracking error: Std dev of (portfolio - benchmark)
        active_returns = [aligned_portfolio[i] - aligned_benchmark[i] for i in range(n)]
        mean_active = sum(active_returns) / n_dec
        tracking_variance = sum((r - mean_active) ** 2 for r in active_returns) / (n_dec - Decimal(1))
        self.tracking_error = tracking_variance.sqrt() * Decimal(365).sqrt()  # Annualized

        # Information ratio: Alpha / Tracking Error
        if self.tracking_error and self.tracking_error > Decimal(0):
            self.information_ratio = self.alpha / self.tracking_error
        else:
            self.information_ratio = Decimal("0")

    def _compute_strategy_metrics(self, strategy_name: str, recorder: TimeSeriesRecorder) -> StrategyMetrics:
        """Compute metrics for a single strategy.

        Args:
            strategy_name: Name of the strategy
            recorder: TimeSeriesRecorder for this strategy

        Returns:
            StrategyMetrics instance
        """
        equity_series = recorder.get_equity_series()

        if not equity_series:
            # Return empty metrics
            return StrategyMetrics(
                name=strategy_name,
                total_return=Decimal("0"),
                annualized_return=Decimal("0"),
                cagr=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                sortino_ratio=Decimal("0"),
                calmar_ratio=Decimal("0"),
                max_drawdown=Decimal("0"),
                volatility=Decimal("0"),
                num_trades=0,
            )

        timestamps, equity_values = zip(*equity_series, strict=False)
        timestamps_list = list(timestamps)
        equity_values_list = list(equity_values)

        # Resample to calendar days
        timestamps_daily, equity_daily = resample_to_calendar_days(timestamps_list, equity_values_list)

        # Calculate returns
        daily_returns = calculate_daily_returns(equity_daily)
        num_days = (timestamps_list[-1] - timestamps_list[0]).days

        # Compute metrics
        total_ret = Decimal(str(calculate_total_return(equity_values_list[0], equity_values_list[-1])))
        annual_ret = Decimal(
            str(calc.calculate_annualized_return(equity_values_list[0], equity_values_list[-1], num_days))
        )
        cagr_val = Decimal(str(calculate_cagr(equity_values_list[0], equity_values_list[-1], num_days)))

        sharpe = Decimal(str(calc.calculate_sharpe_ratio(daily_returns)))
        sortino = Decimal(str(calc.calculate_sortino_ratio(daily_returns)))

        max_dd_info = calc.calculate_max_drawdown(equity_daily, timestamps_daily)
        max_dd = Decimal(str(max_dd_info["pct"]))

        calmar = Decimal(str(calc.calculate_calmar_ratio(annual_ret, max_dd)))
        vol = Decimal(str(calc.calculate_volatility(daily_returns)))

        # Count trades for this strategy (simplified - would need strategy-specific trades)
        num_trades = 0  # TODO: Filter trades by strategy

        return StrategyMetrics(
            name=strategy_name,
            total_return=total_ret,
            annualized_return=annual_ret,
            cagr=cagr_val,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_drawdown=max_dd,
            volatility=vol,
            num_trades=num_trades,
        )

    def summary(self) -> str:
        """Generate text summary of key metrics.

        Returns:
            Multi-line string with formatted metrics
        """
        lines = [
            "=" * 60,
            "BACKTEST RESULTS SUMMARY",
            "=" * 60,
            f"Period: {self.start_date.date()} to {self.end_date.date()}",
            f"Initial Capital: ${self.initial_capital:,.2f}",
            f"Final Capital:   ${self.final_capital:,.2f}",
            "",
            "RETURNS",
            f"  Total Return:       {self.total_return:>8.2%}",
            f"  Annualized Return:  {self.annualized_return:>8.2%}",
            f"  CAGR:               {self.cagr:>8.2%}",
            "",
            "RISK-ADJUSTED",
            f"  Sharpe Ratio:       {self.sharpe_ratio:>8.2f}",
            f"  Sortino Ratio:      {self.sortino_ratio:>8.2f}",
            f"  Calmar Ratio:       {self.calmar_ratio:>8.2f}",
            "",
            "DRAWDOWNS",
            f"  Maximum Drawdown:   {self.max_drawdown:>8.2%}",
            f"  Max DD Duration:    {self.max_drawdown_duration:>8} days",
            f"  Recovery Time:      {self.recovery_time if self.recovery_time else 'Not recovered':>8}",
            f"  Average Drawdown:   {self.average_drawdown:>8.2%}",
            "",
            "RISK",
            f"  Volatility:         {self.volatility:>8.2%}",
            f"  Downside Deviation: {self.downside_deviation:>8.2%}",
            f"  Best Day:           {self.best_day:>8.2%}",
            f"  Worst Day:          {self.worst_day:>8.2%}",
            "",
            "TRADES",
            f"  Number of Trades:   {self.num_trades:>8}",
            f"  Win Rate:           {self.win_rate:>8.2%}",
            f"  Profit Factor:      {self.profit_factor:>8.2f}",
            f"  Expectancy:         ${self.expectancy:>8.2f}",
            f"  Average Win:        ${self.avg_win:>8.2f}",
            f"  Average Loss:       ${self.avg_loss:>8.2f}",
        ]

        if self.alpha is not None:
            lines.extend(
                [
                    "",
                    "BENCHMARK",
                    f"  Alpha:              {self.alpha:>8.4f}",
                    f"  Beta:               {self.beta:>8.2f}",
                    f"  Correlation:        {self.correlation:>8.2f}",
                    f"  Information Ratio:  {self.information_ratio:>8.2f}",
                    f"  Tracking Error:     {self.tracking_error:>8.2%}",
                ]
            )

        lines.append("=" * 60)

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize all metrics to dictionary.

        Returns:
            Dictionary with all computed metrics
        """
        result = {
            "period": {
                "start": self.start_date.isoformat(),
                "end": self.end_date.isoformat(),
                "days": (self.end_date - self.start_date).days,
            },
            "capital": {
                "initial": float(self.initial_capital),
                "final": float(self.final_capital),
            },
            "returns": {
                "total": float(self.total_return),
                "annualized": float(self.annualized_return),
                "cagr": float(self.cagr),
            },
            "risk_adjusted": {
                "sharpe_ratio": float(self.sharpe_ratio),
                "sortino_ratio": float(self.sortino_ratio),
                "calmar_ratio": float(self.calmar_ratio),
            },
            "drawdowns": {
                "max_drawdown": float(self.max_drawdown),
                "max_drawdown_duration": self.max_drawdown_duration,
                "recovery_time": self.recovery_time,
                "average_drawdown": float(self.average_drawdown),
                "underwater_periods": len(self.underwater_periods),
            },
            "risk": {
                "volatility": float(self.volatility),
                "downside_deviation": float(self.downside_deviation),
                "best_day": float(self.best_day),
                "worst_day": float(self.worst_day),
            },
            "trades": {
                "count": self.num_trades,
                "win_rate": float(self.win_rate),
                "profit_factor": float(self.profit_factor),
                "expectancy": float(self.expectancy),
                "avg_win": float(self.avg_win),
                "avg_loss": float(self.avg_loss),
            },
        }

        if self.alpha is not None:
            result["benchmark"] = {
                "alpha": float(self.alpha),
                "beta": self.beta,
                "correlation": float(self.correlation) if self.correlation is not None else None,
                "information_ratio": float(self.information_ratio) if self.information_ratio is not None else None,
                "tracking_error": float(self.tracking_error) if self.tracking_error is not None else None,
            }

        return result

    def plot_equity_curve(self, title: str = "Portfolio Equity Curve") -> go.Figure:
        """Plot interactive equity curve with optional benchmark overlay.

        Args:
            title: Chart title

        Returns:
            Plotly Figure
        """
        from simulor.analytics.visualization import plot_equity_curve

        equity_series = self.global_recorder.get_equity_series()
        timestamps, equity_values = zip(*equity_series, strict=False)

        # TODO: Add benchmark support when implemented
        return plot_equity_curve(
            timestamps=list(timestamps),
            equity_values=list(equity_values),
            title=title,
        )

    def plot_drawdown(self, title: str = "Drawdown Chart") -> go.Figure:
        """Plot drawdown chart with underwater period shading.

        Args:
            title: Chart title

        Returns:
            Plotly Figure
        """
        from simulor.analytics.visualization import plot_drawdown_chart

        equity_series = self.global_recorder.get_equity_series()
        timestamps, equity_values = zip(*equity_series, strict=False)

        # Resample to calendar days
        timestamps_daily, equity_daily = resample_to_calendar_days(list(timestamps), list(equity_values))

        # Calculate drawdown series
        drawdown_series = calc.calculate_drawdown_series(equity_daily)
        max_dd_info = calc.calculate_max_drawdown(equity_daily, timestamps_daily)

        return plot_drawdown_chart(
            timestamps=timestamps_daily,
            drawdown_pcts=drawdown_series,
            max_dd_info=dict(max_dd_info),
            title=title,
        )

    def plot_monthly_returns(self, title: str = "Monthly Returns Heatmap") -> go.Figure:
        """Plot monthly returns heatmap.

        Args:
            title: Chart title

        Returns:
            Plotly Figure
        """
        from simulor.analytics.visualization import plot_monthly_returns_heatmap

        equity_series = self.global_recorder.get_equity_series()
        timestamps, equity_values = zip(*equity_series, strict=False)

        # Resample to calendar days
        timestamps_daily, equity_daily = resample_to_calendar_days(list(timestamps), list(equity_values))

        # Calculate daily returns
        daily_returns = calculate_daily_returns(equity_daily)

        return plot_monthly_returns_heatmap(
            daily_returns=daily_returns,
            timestamps=timestamps_daily,
            title=title,
        )

    def plot_returns_distribution(self, title: str = "Returns Distribution") -> go.Figure:
        """Plot returns distribution histogram with normal curve.

        Args:
            title: Chart title

        Returns:
            Plotly Figure
        """
        from simulor.analytics.visualization import plot_returns_distribution

        equity_series = self.global_recorder.get_equity_series()
        timestamps, equity_values = zip(*equity_series, strict=False)

        # Resample to calendar days
        timestamps_daily, equity_daily = resample_to_calendar_days(list(timestamps), list(equity_values))

        # Calculate daily returns
        daily_returns = calculate_daily_returns(equity_daily)

        return plot_returns_distribution(
            returns=daily_returns,
            title=title,
        )
