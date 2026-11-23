# Model card — Model A (downstream delay with intervals) v20261123-214334

- Task: at the moment a bus is observed at timepoint k, predict its delay at later timepoint j (1–5 timepoints ahead).
- Data: MBTA Bus Arrival Departure Times 2025 (CC0), Jul–Dec 2025, 10 pilot routes; ~12% of trips sampled.
- Model: scikit-learn HistGradientBoostingRegressor, quantile loss at 0.1/0.5/0.9; conformalized quantile regression
  on the week before each test window (target 80% coverage).
- Features (known at prediction time only): delay_k, delay_chg, route_code, direction_id, tp_j_code, horizon, sched_travel_s, hour, dow, holiday, recent_seg_delta, headway_k, hist_med_j.
- Leakage guard: recent-segment feature is an ASOF join on segments completed strictly before the prediction time;
  historical medians are fit on the training slice only (tests in tests/test_ml.py).
- Backtest (3 rolling-origin folds): p50 MAE 95.8 s vs best baseline (propagation)
  140.1 s (31.6% better);
  80% interval coverage 79.3% (raw 76.6%), mean width 296 s.
- Limitations: trained on 2025 data, served on 2026 feeds; timepoints only; live features may be missing (the
  model was trained with 20% of live features blanked to stay robust).
