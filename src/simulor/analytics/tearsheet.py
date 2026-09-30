"""HTML tearsheet generator for backtest results.

This module provides a comprehensive HTML tearsheet that embeds:
- Summary metrics grid
- Interactive Plotly charts
- Trade list (optional)
- Strategy breakdown (for multi-strategy)

The output is a self-contained HTML file suitable for sharing/archiving.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulor.analytics.result import BacktestResult

__all__ = ["Tearsheet"]


class Tearsheet:
    """Generate a self-contained HTML tearsheet for backtest results.

    The tearsheet includes:
    - Header with backtest metadata
    - Comprehensive metrics grid
    - Interactive Plotly charts (equity curve, drawdown, monthly returns, returns distribution)
    - Optional trade list
    - Strategy breakdown for multi-strategy portfolios

    Example:
        >>> result = engine.run()
        >>> tearsheet = Tearsheet(result)
        >>> tearsheet.save("backtest_report.html")
    """

    def __init__(self, result: BacktestResult) -> None:
        """Initialize tearsheet generator.

        Args:
            result: BacktestResult instance
        """
        self.result = result

    def _render_header(self) -> str:
        """Render header section with metadata."""
        start_date = self.result.start_date.strftime("%Y-%m-%d %H:%M:%S %Z")
        end_date = self.result.end_date.strftime("%Y-%m-%d %H:%M:%S %Z")
        generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        # Calculate duration and final equity from recorder
        duration_days = (self.result.end_date - self.result.start_date).days
        equity_series = self.result.global_recorder.get_equity_series()
        final_equity = equity_series[-1][1] if equity_series else Decimal("0.0")

        return f"""
        <div class="header">
            <h1>Backtest Tearsheet</h1>
            <div class="metadata">
                <div><strong>Period:</strong> {start_date} -> {end_date}</div>
                <div><strong>Duration:</strong> {duration_days} days</div>
                <div><strong>Initial Capital:</strong> ${self.result.initial_capital:,.2f}</div>
                <div><strong>Final Equity:</strong> ${final_equity:,.2f}</div>
                <div><strong>Generated:</strong> {generated_at}</div>
            </div>
        </div>
        """

    def _render_metrics_grid(self) -> str:
        """Render comprehensive metrics grid."""
        r = self.result
        metrics_html = """
        <div class="metrics-section">
            <h2>Performance Metrics</h2>
            <table class="metrics-table">
        """

        # Returns section
        metrics_html += """
                <tr class="section-header"><td colspan="2">Returns</td></tr>
        """
        metrics = [
            ("Total Return", f"{r.total_return:.2%}"),
            ("CAGR", f"{r.cagr:.2%}"),
            ("Best Day", f"{r.best_day:.2%}"),
            ("Worst Day", f"{r.worst_day:.2%}"),
        ]
        for label, value in metrics:
            metrics_html += f"<tr><td>{label}</td><td>{value}</td></tr>\n"

        # Risk section
        metrics_html += """
                <tr class="section-header"><td colspan="2">Risk</td></tr>
        """
        metrics = [
            ("Volatility (ann.)", f"{r.volatility:.2%}"),
            ("Sharpe Ratio", f"{r.sharpe_ratio:.3f}"),
            ("Sortino Ratio", f"{r.sortino_ratio:.3f}"),
            ("Calmar Ratio", f"{r.calmar_ratio:.3f}"),
            ("Max Drawdown", f"{r.max_drawdown:.2%}"),
            ("Max DD Duration", f"{r.max_drawdown_duration} days"),
        ]
        for label, value in metrics:
            metrics_html += f"<tr><td>{label}</td><td>{value}</td></tr>\n"

        # Trading Activity
        metrics_html += """
                <tr class="section-header"><td colspan="2">Trading Activity</td></tr>
        """
        metrics = [
            ("Total Trades", f"{r.num_trades:,}"),
            ("Win Rate", f"{r.win_rate:.2%}"),
            ("Profit Factor", f"{r.profit_factor:.2f}"),
            ("Avg Win", f"${r.avg_win:,.2f}"),
            ("Avg Loss", f"${r.avg_loss:,.2f}"),
            ("Expectancy", f"${r.expectancy:,.2f}"),
        ]
        for label, value in metrics:
            metrics_html += f"<tr><td>{label}</td><td>{value}</td></tr>\n"

        metrics_html += """
            </table>
        </div>
        """
        return metrics_html

    def _embed_charts(self) -> str:
        """Embed interactive Plotly charts."""
        import plotly

        charts_html = '<div class="charts-section">\n'

        # Equity curve
        try:
            fig = self.result.plot_equity_curve()
            chart_div = plotly.io.to_html(
                fig,
                include_plotlyjs=False,
                div_id="equity-chart",
                config={"displayModeBar": True, "displaylogo": False},
            )
            charts_html += f'<div class="chart-container">{chart_div}</div>\n'
        except Exception as e:
            charts_html += f'<div class="chart-error">Equity Chart Error: {e}</div>\n'

        # Drawdown
        try:
            fig = self.result.plot_drawdown()
            chart_div = plotly.io.to_html(
                fig,
                include_plotlyjs=False,
                div_id="drawdown-chart",
                config={"displayModeBar": True, "displaylogo": False},
            )
            charts_html += f'<div class="chart-container">{chart_div}</div>\n'
        except Exception as e:
            charts_html += f'<div class="chart-error">Drawdown Chart Error: {e}</div>\n'

        # Monthly returns heatmap
        try:
            fig = self.result.plot_monthly_returns()
            chart_div = plotly.io.to_html(
                fig,
                include_plotlyjs=False,
                div_id="monthly-chart",
                config={"displayModeBar": True, "displaylogo": False},
            )
            charts_html += f'<div class="chart-container">{chart_div}</div>\n'
        except Exception as e:
            charts_html += f'<div class="chart-error">Monthly Returns Chart Error: {e}</div>\n'

        # Returns distribution
        try:
            fig = self.result.plot_returns_distribution()
            chart_div = plotly.io.to_html(
                fig,
                include_plotlyjs=False,
                div_id="distribution-chart",
                config={"displayModeBar": True, "displaylogo": False},
            )
            charts_html += f'<div class="chart-container">{chart_div}</div>\n'
        except Exception as e:
            charts_html += f'<div class="chart-error">Distribution Chart Error: {e}</div>\n'

        charts_html += "</div>\n"
        return charts_html

    def _render_strategy_breakdown(self) -> str:
        """Render per-strategy metrics if multi-strategy."""
        if not self.result.strategy_metrics:
            return ""

        html = """
        <div class="strategy-section">
            <h2>Strategy Breakdown</h2>
            <table class="strategy-table">
                <thead>
                    <tr>
                        <th>Strategy</th>
                        <th>Total Return</th>
                        <th>Sharpe</th>
                        <th>Max DD</th>
                        <th>CAGR</th>
                        <th>Volatility</th>
                        <th>Trades</th>
                    </tr>
                </thead>
                <tbody>
        """

        for strategy_id, metrics in self.result.strategy_metrics.items():
            html += f"""
                    <tr>
                        <td>{strategy_id}</td>
                        <td>{metrics.total_return:.2%}</td>
                        <td>{metrics.sharpe_ratio:.3f}</td>
                        <td>{metrics.max_drawdown:.2%}</td>
                        <td>{metrics.cagr:.2%}</td>
                        <td>{metrics.volatility:.2%}</td>
                        <td>{metrics.num_trades:,}</td>
                    </tr>
            """

        html += """
                </tbody>
            </table>
        </div>
        """
        return html

    def _get_styles(self) -> str:
        """Return CSS styles for tearsheet."""
        return """
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: #0d1117;
                color: #c9d1d9;
                padding: 20px;
                line-height: 1.6;
            }

            .container {
                max-width: 1400px;
                margin: 0 auto;
            }

            .header {
                background: #161b22;
                padding: 30px;
                border-radius: 8px;
                margin-bottom: 30px;
                border: 1px solid #30363d;
            }

            .header h1 {
                color: #58a6ff;
                font-size: 2rem;
                margin-bottom: 20px;
            }

            .metadata {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 15px;
                font-size: 0.95rem;
            }

            .metadata div {
                padding: 8px;
                background: #0d1117;
                border-radius: 4px;
            }

            .metrics-section, .strategy-section {
                background: #161b22;
                padding: 30px;
                border-radius: 8px;
                margin-bottom: 30px;
                border: 1px solid #30363d;
            }

            h2 {
                color: #58a6ff;
                margin-bottom: 20px;
                font-size: 1.5rem;
            }

            .metrics-table {
                width: 100%;
                border-collapse: collapse;
            }

            .metrics-table td {
                padding: 10px 15px;
                border-bottom: 1px solid #21262d;
            }

            .metrics-table td:first-child {
                font-weight: 500;
                color: #8b949e;
            }

            .metrics-table td:last-child {
                text-align: right;
                font-family: 'Courier New', monospace;
                color: #c9d1d9;
            }

            .metrics-table .section-header td {
                background: #0d1117;
                color: #58a6ff;
                font-weight: bold;
                padding: 15px;
                font-size: 1.1rem;
                border-top: 2px solid #30363d;
            }

            .charts-section {
                display: grid;
                gap: 30px;
            }

            .chart-container {
                background: #161b22;
                padding: 20px;
                border-radius: 8px;
                border: 1px solid #30363d;
            }

            .chart-error {
                background: #3d1f1f;
                color: #f85149;
                padding: 20px;
                border-radius: 8px;
                border: 1px solid #f85149;
                margin-bottom: 20px;
            }

            .strategy-table {
                width: 100%;
                border-collapse: collapse;
            }

            .strategy-table thead {
                background: #0d1117;
            }

            .strategy-table th {
                padding: 12px 15px;
                text-align: left;
                color: #58a6ff;
                font-weight: 600;
                border-bottom: 2px solid #30363d;
            }

            .strategy-table td {
                padding: 10px 15px;
                border-bottom: 1px solid #21262d;
                font-family: 'Courier New', monospace;
            }

            .strategy-table td:first-child {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto;
                font-weight: 500;
                color: #8b949e;
            }

            .strategy-table tbody tr:hover {
                background: #0d1117;
            }
        </style>
        """

    def save(self, filepath: str | Path, include_plotlyjs: str = "cdn") -> None:
        """Generate and save the HTML tearsheet.

        Args:
            filepath: Output path for HTML file
            include_plotlyjs: How to include plotly.js:
                - 'cdn': Link to CDN (recommended, smallest file size)
                - 'inline': Embed full plotly.js (~3MB, works offline)

        Example:
            >>> tearsheet.save("reports/backtest_2024.html")
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Build HTML
        html_parts = [
            "<!DOCTYPE html>",
            "<html lang='en'>",
            "<head>",
            "<meta charset='UTF-8'>",
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
            "<title>Backtest Tearsheet</title>",
        ]

        # Include Plotly.js
        if include_plotlyjs == "cdn":
            html_parts.append('<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>')
        elif include_plotlyjs == "inline":
            import plotly

            plotly_js = plotly.offline.get_plotlyjs()
            html_parts.append(f"<script>{plotly_js}</script>")

        html_parts.append(self._get_styles())
        html_parts.append("</head>")
        html_parts.append("<body>")
        html_parts.append("<div class='container'>")
        html_parts.append(self._render_header())
        html_parts.append(self._render_metrics_grid())
        html_parts.append(self._embed_charts())
        html_parts.append(self._render_strategy_breakdown())
        html_parts.append("</div>")
        html_parts.append("</body>")
        html_parts.append("</html>")

        html_content = "\n".join(html_parts)

        # Write to file
        filepath.write_text(html_content, encoding="utf-8")
