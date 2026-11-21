# Machine-learning research boundary

The ML module is offline research only. It uses chronological TimeSeriesSplit
validation and reports accuracy against a majority-class baseline. It does not
generate executable orders, connect to authenticated exchange endpoints, or
claim profitability.

Before any strategy could be considered beyond research, evaluation must include
fees, spread, slippage, realistic fills, survivorship/data-quality checks,
walk-forward tests, untouched holdout data, calibration, drawdown and regime
analysis. A model that fails to beat its baseline or is unstable must be rejected.
