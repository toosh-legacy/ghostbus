# Model card — Model B (missed-trip probability) v20261124-064901

- Task: probability that a scheduled pilot-route trip is missed (ghost-def v1.1 historical definition), using only
  information available by 4:00 AM on the service date.
- Labels: D2 half-trips with no observed timepoint events; route-days below 70% completeness excluded (unknown).
- Candidates: logistic regression (one-hot, class weights) and LightGBM (Optuna-tuned on fold-0 calibration log
  loss), each with isotonic calibration on the week before the test window. Chosen: lgbm_isotonic.
- Backtest mean over 3 folds: Brier 0.01758 vs rolling 28-day baseline
  0.01767; ROC-AUC 0.562; PR-AUC 0.025
  (base rate ≈ 0.018); ECE 0.0086.
- Limitations: missed trips are rare (~2%); rolling rates for serving are as of the last training date.
