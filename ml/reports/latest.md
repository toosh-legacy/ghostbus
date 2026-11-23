# ML backtest report (latest)

Rolling-origin backtest, 3 folds, time-based splits only (train on earlier weeks, calibrate on the next week, test on the following 2 weeks).

## Model A — downstream delay (seconds)

| fold | test window | p50 MAE | schedule | propagation | hist. median | coverage raw | coverage (CQR) | width |
|---|---|---|---|---|---|---|---|---|
| 1 | 2025-11-20–2025-12-03 | 95.1 | 266.2 | 136.2 | 249.3 | 76.4% | 78.6% | 288 |
| 2 | 2025-12-04–2025-12-17 | 95.2 | 286.6 | 134.4 | 245.1 | 77.5% | 82.2% | 308 |
| 3 | 2025-12-18–2025-12-31 | 96.6 | 263.1 | 149.1 | 269.5 | 75.8% | 77.7% | 295 |

- **Mean p50 MAE 95.6 s** vs best baseline `propagation` 139.9 s → **31.6% better**.
- Mean RMSE 154.7 s; pinball p10/p50/p90 19.4/47.8/25.6.
- **80% interval coverage after conformal calibration: 79.5%** (target 78–82%; raw quantiles 76.6%); mean width 297 s.

Breakdowns by horizon and time band are in `ml/reports/backtest_results.parquet` (also the copilot view `v_backtest_results`).

## Model B — missed-trip probability

| method | Brier | ROC-AUC | PR-AUC | ECE |
|---|---|---|---|---|
| overall_rate | 0.01757 | 0.500 | 0.018 | 0.0068 |
| rolling_28d_route_hour | 0.01767 | 0.581 | 0.023 | 0.0088 |
| logreg_isotonic | 0.01761 | 0.595 | 0.023 | 0.0073 |
| hgb_isotonic | 0.01748 | 0.581 | 0.034 | 0.0067 |

- Chosen: `hgb_isotonic`; beats the rolling 28-day baseline on Brier: **True**.

Calibration curve (last fold):

| mean predicted | observed | n |
|---|---|---|
| 0.0023 | 0.0033 | 3364 |
| 0.0142 | 0.0101 | 8546 |
| 0.0180 | 0.0110 | 1090 |
| 0.0237 | 0.0082 | 2669 |
| 0.0335 | 0.0113 | 13987 |
| 0.0627 | 0.0276 | 543 |

## Promotion

- model_a: pinned v20261123-220923: p50_mae 95.6197 vs pinned 95.6628 (within 1%)
- model_b: pinned v20261123-220923: brier 0.0175 vs pinned 0.0176 (within 1%)
