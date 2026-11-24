# ML backtest report (latest)

Rolling-origin backtest, 3 folds, time-based splits only (train on earlier weeks, calibrate on the next week, test on the following 2 weeks).

## Model A — downstream delay (seconds)

| fold | test window | p50 MAE | schedule | propagation | hist. median | coverage raw | coverage (CQR) | width |
|---|---|---|---|---|---|---|---|---|
| 1 | 2025-11-20–2025-12-03 | 93.3 | 266.2 | 136.2 | 249.3 | 74.6% | 78.8% | 287 |
| 2 | 2025-12-04–2025-12-17 | 93.6 | 286.6 | 134.4 | 245.1 | 75.9% | 82.2% | 302 |
| 3 | 2025-12-18–2025-12-31 | 95.1 | 263.1 | 149.1 | 269.5 | 73.5% | 77.1% | 287 |

- **Mean p50 MAE 94.0 s** vs best baseline `propagation` 139.9 s → **32.8% better**.
- Mean RMSE 152.5 s; pinball p10/p50/p90 19.2/47.0/25.4.
- **80% interval coverage after conformal calibration: 79.4%** (target 78–82%; raw quantiles 74.7%); mean width 292 s.

Breakdowns by horizon and time band are in `ml/reports/backtest_results.parquet` (also the copilot view `v_backtest_results`).

## Model B — missed-trip probability

| method | Brier | ROC-AUC | PR-AUC | ECE |
|---|---|---|---|---|
| overall_rate | 0.01757 | 0.500 | 0.018 | 0.0068 |
| rolling_28d_route_hour | 0.01767 | 0.581 | 0.023 | 0.0088 |
| logreg_isotonic | 0.01761 | 0.595 | 0.023 | 0.0073 |
| lgbm_isotonic | 0.01758 | 0.562 | 0.025 | 0.0086 |

- Chosen: `lgbm_isotonic`; beats the rolling 28-day baseline on Brier: **True**.

Calibration curve (last fold):

| mean predicted | observed | n |
|---|---|---|
| 0.0210 | 0.0095 | 25561 |
| 0.0313 | 0.0108 | 3229 |
| 0.0468 | 0.0177 | 1409 |

## Promotion

- model_a: pinned v20261124-064901: p50_mae 94.0024 vs champion 95.6197 (within 1%)
- model_b: pinned v20261124-064901: brier 0.0176 vs champion 0.0175 (within 1%)
