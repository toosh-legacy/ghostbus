"""`make train` / `make backtest`: Model A (LightGBM quantile regression + MAPIE CQR) and Model B (LightGBM or
logistic regression, isotonic-calibrated).

Optuna tunes LightGBM on the first fold's train/calibration split (time-ordered, no test data). Rolling-origin
backtest with 3 folds (train on earlier weeks, calibrate on the next week, test on the next 2). Fixed seeds. Every
run is tracked in MLflow (params, per-fold metrics, model cards) and each version is registered in the MLflow model
registry; the champion alias moves only if the primary metric does not regress by more than 1%.
Writes ml/reports/latest.md, latest.json and backtest_results.parquet.
"""

from __future__ import annotations

import argparse
import json
import os
import time

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd
from gbcore.settings import REPO_ROOT
from mapie.regression import ConformalizedQuantileRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.preprocessing import OneHotEncoder

from .data import (
    CATEGORICAL_A,
    FEATURES_A,
    add_hist_median,
    build_model_a_samples,
    build_model_b_frame,
    encode_categories,
    split,
    time_folds,
)
from .models import CalibratedClassifierB, QuantileModelA
from .registry import ModelRegistry

SEED = 42
REPORTS = REPO_ROOT / "ml" / "reports"
QUANTILES = (0.1, 0.5, 0.9)
ALPHA = 0.2
MAX_TRAIN_ROWS_A = 400_000
TUNE_ROWS = 120_000
N_TRIALS = int(os.environ.get("GB_OPTUNA_TRIALS", "20"))
optuna.logging.set_verbosity(optuna.logging.WARNING)
BASE_A = {"num_leaves": 63, "learning_rate": 0.08, "min_child_samples": 50, "n_estimators": 300,
          "colsample_bytree": 0.9, "reg_lambda": 1.0}
BASE_B = {"num_leaves": 31, "learning_rate": 0.05, "min_child_samples": 200, "n_estimators": 250,
          "colsample_bytree": 0.9, "reg_lambda": 5.0}
TUNING: dict = {}


def pinball(y, q, tau):
    d = y - q
    return float(np.mean(np.maximum(tau * d, (tau - 1) * d)))


def band_of(hour: pd.Series) -> pd.Series:
    from gbcore.timeutil import time_band

    return hour.map(time_band)


def _cat_idx() -> list[int]:
    return [i for i, c in enumerate(FEATURES_A) if c in CATEGORICAL_A]


def lgb_quantile(q: float, params: dict) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(objective="quantile", alpha=q, random_state=SEED, verbose=-1, n_jobs=4, **params)


def fit_quantiles(Xtr: pd.DataFrame, ytr: np.ndarray, params: dict | None = None) -> dict:
    X = Xtr[FEATURES_A].to_numpy(dtype=float)
    out = {}
    for q in QUANTILES:
        m = lgb_quantile(q, params or BASE_A)
        m.fit(X, ytr, categorical_feature=_cat_idx())
        out[q] = m
    return out


def tune_model_a(tr: pd.DataFrame, ca: pd.DataFrame, log=print) -> dict:
    """Optuna (TPE, seeded) on the p50 pinball loss of the calibration week; fold-0 data only."""
    trs = tr.sample(min(len(tr), TUNE_ROWS), random_state=SEED)
    Xt, yt = trs[FEATURES_A].to_numpy(dtype=float), trs["y"].to_numpy()
    Xc, yc = ca[FEATURES_A].to_numpy(dtype=float), ca["y"].to_numpy()

    def objective(trial: optuna.Trial) -> float:
        params = {"num_leaves": trial.suggest_int("num_leaves", 15, 255, log=True),
                  "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
                  "min_child_samples": trial.suggest_int("min_child_samples", 20, 400, log=True),
                  "n_estimators": trial.suggest_int("n_estimators", 100, 600, step=50),
                  "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                  "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 20, log=True)}
        m = lgb_quantile(0.5, params).fit(Xt, yt, categorical_feature=_cat_idx())
        return pinball(yc, m.predict(Xc), 0.5)

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.enqueue_trial(BASE_A)
    study.optimize(objective, n_trials=N_TRIALS)
    log(f"[model A] optuna best p50 pinball {study.best_value:.2f} after {len(study.trials)} trials: {study.best_params}")
    return {**study.best_params, "_best_value": study.best_value, "_n_trials": len(study.trials)}


def tune_model_b(Xt: pd.DataFrame, yt: np.ndarray, Xc: pd.DataFrame, yc: np.ndarray, log=print) -> dict:
    """Optuna on calibration-week log loss for the LightGBM missed-trip classifier (fold-0 data only)."""
    from sklearn.metrics import log_loss

    def objective(trial: optuna.Trial) -> float:
        params = {"num_leaves": trial.suggest_int("num_leaves", 7, 63, log=True),
                  "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
                  "min_child_samples": trial.suggest_int("min_child_samples", 50, 2000, log=True),
                  "n_estimators": trial.suggest_int("n_estimators", 100, 600, step=50),
                  "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                  "reg_lambda": trial.suggest_float("reg_lambda", 1e-2, 50, log=True)}
        m = lgb.LGBMClassifier(objective="binary", random_state=SEED, verbose=-1, n_jobs=4, **params)
        m.fit(Xt, yt, categorical_feature=[0])
        return log_loss(yc, m.predict_proba(Xc)[:, 1], labels=[0, 1])

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.enqueue_trial(BASE_B)
    study.optimize(objective, n_trials=N_TRIALS)
    log(f"[model B] optuna best log loss {study.best_value:.5f} after {len(study.trials)} trials: {study.best_params}")
    return {**study.best_params, "_best_value": study.best_value, "_n_trials": len(study.trials)}


def cqr_qhat(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, alpha: float = ALPHA) -> float:
    scores = np.maximum(lo - y, y - hi)
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))


def _blank_some(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Serving robustness: live features (recent segment, headway) are sometimes unavailable."""
    df = df.copy()
    for c in ("recent_seg_delta", "headway_k", "delay_chg"):
        m = rng.random(len(df)) < 0.2
        df.loc[m, c] = np.nan
    return df


def train_model_a(samples: pd.DataFrame | None = None, log=print) -> tuple[dict, QuantileModelA, list[dict]]:
    rng = np.random.default_rng(SEED)
    df = samples if samples is not None else build_model_a_samples()
    folds = time_folds(df["service_date"])
    rows: list[dict] = []
    fold_summ = []
    model = None
    params: dict | None = None
    for fold in folds:
        tr, ca, te = split(df, fold)
        tr, ca, te = add_hist_median(tr, ca, te)
        tr, cats = encode_categories(tr)
        ca, _ = encode_categories(ca, cats)
        te, _ = encode_categories(te, cats)
        if params is None:
            tuned = tune_model_a(tr, ca, log)
            params = {k: v for k, v in tuned.items() if not k.startswith("_")}
            TUNING["model_a"] = tuned
        trs = tr.sample(min(len(tr), MAX_TRAIN_ROWS_A), random_state=SEED)
        trs = _blank_some(trs, rng)
        t0 = time.time()
        models = fit_quantiles(trs, trs["y"].to_numpy(), params)
        Xc = ca[FEATURES_A].to_numpy(dtype=float)
        mapie = ConformalizedQuantileRegressor([models[0.1], models[0.9], models[0.5]], confidence_level=1 - ALPHA,
                                               prefit=True).conformalize(Xc, ca["y"].to_numpy())
        cal_lo = np.minimum(models[0.1].predict(Xc), models[0.5].predict(Xc))
        cal_hi = np.maximum(models[0.9].predict(Xc), models[0.5].predict(Xc))
        qhat = cqr_qhat(ca["y"].to_numpy(), cal_lo, cal_hi)
        model = QuantileModelA(models, qhat, cats, mapie=mapie)
        pred = model.predict(te)
        y = te["y"].to_numpy()
        base = {"schedule": np.zeros(len(te)), "propagation": te["delay_k"].to_numpy(dtype=float),
                "historical_median": te["hist_med_j"].to_numpy(dtype=float)}
        te = te.assign(p10=pred["p10"].to_numpy(), p50=pred["p50"].to_numpy(), p90=pred["p90"].to_numpy(),
                       raw_p10=pred["raw_p10"].to_numpy(), raw_p90=pred["raw_p90"].to_numpy(),
                       band=band_of(te["hour"]))
        s = {"fold": fold["fold"], "train_end": str(fold["train_end"]), "test": [str(x) for x in fold["test"]],
             "n_train": len(trs), "n_test": len(te), "qhat_s": qhat, "fit_s": round(time.time() - t0, 1),
             "p50_mae": float(np.mean(np.abs(y - te["p50"]))), "p50_rmse": float(np.sqrt(np.mean((y - te["p50"]) ** 2))),
             "coverage_raw": float(np.mean((y >= te["raw_p10"]) & (y <= te["raw_p90"]))),
             "coverage": float(np.mean((y >= te["p10"]) & (y <= te["p90"]))),
             "width_s": float(np.mean(te["p90"] - te["p10"])),
             "pinball_p10": pinball(y, te["p10"].to_numpy(), 0.1), "pinball_p50": pinball(y, te["p50"].to_numpy(), 0.5),
             "pinball_p90": pinball(y, te["p90"].to_numpy(), 0.9)}
        for b, v in base.items():
            s[f"{b}_mae"] = float(np.mean(np.abs(y - v)))
            s[f"{b}_rmse"] = float(np.sqrt(np.mean((y - v) ** 2)))
        fold_summ.append(s)
        log(f"[model A] fold {fold['fold']}: {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()})}")
        for keyname, keycol in (("horizon", "horizon"), ("time_band", "band")):
            for kv, g in te.groupby(keycol):
                yy = g["y"].to_numpy()
                for meth, col in (("model_p50", "p50"), ("schedule", None), ("propagation", "delay_k"),
                                  ("historical_median", "hist_med_j")):
                    v = np.zeros(len(g)) if col is None else g[col].to_numpy(dtype=float)
                    rows.append({"model": "model_a", "fold": fold["fold"], "method": meth, "horizon": f"{keyname}={kv}",
                                 "metric": "mae", "value": float(np.mean(np.abs(yy - v)))})
                rows.append({"model": "model_a", "fold": fold["fold"], "method": "model_interval",
                             "horizon": f"{keyname}={kv}", "metric": "coverage",
                             "value": float(np.mean((yy >= g["p10"]) & (yy <= g["p90"])))})
                rows.append({"model": "model_a", "fold": fold["fold"], "method": "model_interval",
                             "horizon": f"{keyname}={kv}", "metric": "width_s", "value": float(np.mean(g["p90"] - g["p10"]))})
    summ = {k: float(np.mean([f[k] for f in fold_summ])) for k in fold_summ[0] if isinstance(fold_summ[0][k], float)}
    best_base = min(("schedule", "propagation", "historical_median"), key=lambda b: summ[f"{b}_mae"])
    summ["best_baseline"] = best_base
    summ["improvement_vs_best_baseline_pct"] = 100 * (1 - summ["p50_mae"] / summ[f"{best_base}_mae"])
    summ["folds"] = fold_summ
    summ["params"] = params
    # hist medians of the last fold's training slice are used for serving
    tr_last, _, _ = split(df, folds[-1])
    model.ref_delay_sample = tr_last["delay_k"].sample(min(20000, len(tr_last)), random_state=SEED).to_numpy()  # PSI reference
    model.hist_median = {"by_hour": tr_last.groupby(["route_id", "tp_j", "hour_j"])["y"].median().to_dict(),
                         "by_tp": tr_last.groupby(["route_id", "tp_j"])["y"].median().to_dict()}
    return summ, model, rows


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -1, 2
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    e = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            e += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(e)


def train_model_b(frame: pd.DataFrame | None = None, log=print) -> tuple[dict, CalibratedClassifierB, list[dict]]:
    df = frame if frame is not None else build_model_b_frame()
    folds = time_folds(df["service_date"])
    rows, fold_summ = [], []
    best_model = None
    for fold in folds:
        tr, ca, te = split(df, fold)
        overall = float(tr["y"].mean())
        cats = {r: i for i, r in enumerate(sorted(df["route_id"].unique()))}
        fill = {"route_rate_7d": overall, "route_rate_28d": overall, "hour_rate_28d": overall}
        res = {}
        cands = {}
        # logistic regression (one-hot) with class weights
        lr_proto = CalibratedClassifierB("logreg", None, None, cats, fill)  # type: ignore[arg-type]
        Xtr = lr_proto._X(tr)
        enc = OneHotEncoder(handle_unknown="ignore").fit(Xtr[["route_code", "direction_id", "hour", "dow"]])
        lr_proto.enc = enc
        from scipy.sparse import hstack

        Xs = hstack([enc.transform(Xtr[["route_code", "direction_id", "hour", "dow"]]),
                     Xtr[["holiday", "route_rate_7d", "route_rate_28d", "hour_rate_28d"]].to_numpy() * 10,
                     Xtr[["trips_that_day"]].to_numpy() / 300.0]).tocsr()
        lr = LogisticRegression(class_weight="balanced", max_iter=500, C=0.5, random_state=SEED).fit(Xs, tr["y"])
        lr_proto.model = lr
        lr_proto.iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(lr_proto.raw(ca), ca["y"])
        cands["logreg_isotonic"] = lr_proto
        if "model_b" not in TUNING:
            TUNING["model_b"] = tune_model_b(lr_proto._X(tr), tr["y"].to_numpy(), lr_proto._X(ca), ca["y"].to_numpy(),
                                             log)
        pb = {k: v for k, v in TUNING["model_b"].items() if not k.startswith("_")}
        gb = lgb.LGBMClassifier(objective="binary", random_state=SEED, verbose=-1, n_jobs=4, **pb)
        gb.fit(lr_proto._X(tr), tr["y"], categorical_feature=[0])
        gbm = CalibratedClassifierB("lgbm", gb, None, cats, fill)  # type: ignore[arg-type]
        gbm.iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(gbm.raw(ca), ca["y"])
        cands["lgbm_isotonic"] = gbm
        y = te["y"].to_numpy()
        preds = {"overall_rate": np.full(len(te), overall),
                 "rolling_28d_route_hour": te["hour_rate_28d"].fillna(overall).to_numpy()}
        for k, m in cands.items():
            preds[k] = m.predict_proba(te)
        for k, p in preds.items():
            res[k] = {"brier": float(brier_score_loss(y, p)), "roc_auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
                      "pr_auc": float(average_precision_score(y, p)), "ece": ece(y, p)}
            for mname, v in res[k].items():
                rows.append({"model": "model_b", "fold": fold["fold"], "method": k, "horizon": "all", "metric": mname,
                             "value": v})
        # calibration curve for the best candidate
        fold_summ.append({"fold": fold["fold"], "test": [str(x) for x in fold["test"]], "n_test": len(te),
                          "base_rate_test": float(y.mean()), "results": res})
        log(f"[model B] fold {fold['fold']}: " + json.dumps({k: {m: round(v, 4) for m, v in r.items() if v is not None}
                                                             for k, r in res.items()}))
        best_model = cands
    # choose by mean Brier across folds
    meth = ["overall_rate", "rolling_28d_route_hour", "logreg_isotonic", "lgbm_isotonic"]
    mean = {m: {k: float(np.mean([f["results"][m][k] for f in fold_summ])) for k in ("brier", "roc_auc", "pr_auc", "ece")}
            for m in meth}
    chosen = min(("logreg_isotonic", "lgbm_isotonic"), key=lambda m: mean[m]["brier"])
    model = best_model[chosen]
    # calibration curve on last fold
    _, _, te = split(df, folds[-1])
    p = model.predict_proba(te)
    q = pd.qcut(p, 10, duplicates="drop")
    curve = pd.DataFrame({"p": p, "y": te["y"].to_numpy(), "bin": q}).groupby("bin", observed=True).agg(
        mean_pred=("p", "mean"), observed=("y", "mean"), n=("y", "size")).reset_index(drop=True)
    summ = {"chosen": chosen, "mean": mean, "brier": mean[chosen]["brier"], "folds": fold_summ,
            "beats_rolling_baseline_brier": mean[chosen]["brier"] < mean["rolling_28d_route_hour"]["brier"],
            "calibration_curve_last_fold": curve.to_dict("records")}
    # serving needs the latest rolling rates
    last_day = df["service_date"].max()
    model.latest_rates = {  # type: ignore[attr-defined]
        "as_of": str(last_day.date()),
        "route_7d": df[df.service_date > last_day - pd.Timedelta(days=7)].groupby("route_id")["y"].mean().to_dict(),
        "route_28d": df[df.service_date > last_day - pd.Timedelta(days=28)].groupby("route_id")["y"].mean().to_dict(),
        "band_28d": df[df.service_date > last_day - pd.Timedelta(days=28)].groupby(["route_id", "time_band"])["y"].mean().to_dict(),
    }
    return summ, model, rows


def card_a(summ: dict, version: str) -> str:
    return f"""# Model card — Model A (downstream delay with intervals) {version}

- Task: at the moment a bus is observed at timepoint k, predict its delay at later timepoint j (1–5 timepoints ahead).
- Data: MBTA Bus Arrival Departure Times 2025 (CC0), Jul–Dec 2025, 10 pilot routes; ~12% of trips sampled.
- Model: LightGBM quantile regression at 0.1/0.5/0.9 (hyperparameters tuned with Optuna on fold-0 train/calibration
  data: {summ.get('params')}); MAPIE conformalized quantile regression on the week before each test window (target
  80% coverage).
- Features (known at prediction time only): {', '.join(FEATURES_A)}.
- Leakage guard: recent-segment feature is an ASOF join on segments completed strictly before the prediction time;
  historical medians are fit on the training slice only (tests in tests/test_ml.py).
- Backtest (3 rolling-origin folds): p50 MAE {summ['p50_mae']:.1f} s vs best baseline ({summ['best_baseline']})
  {summ[summ['best_baseline'] + '_mae']:.1f} s ({summ['improvement_vs_best_baseline_pct']:.1f}% better);
  80% interval coverage {summ['coverage']:.1%} (raw {summ['coverage_raw']:.1%}), mean width {summ['width_s']:.0f} s.
- Limitations: trained on 2025 data, served on 2026 feeds; timepoints only; live features may be missing (the
  model was trained with 20% of live features blanked to stay robust).
"""


def card_b(summ: dict, version: str) -> str:
    m = summ["mean"]
    return f"""# Model card — Model B (missed-trip probability) {version}

- Task: probability that a scheduled pilot-route trip is missed (ghost-def v1.1 historical definition), using only
  information available by 4:00 AM on the service date.
- Labels: D2 half-trips with no observed timepoint events; route-days below 70% completeness excluded (unknown).
- Candidates: logistic regression (one-hot, class weights) and LightGBM (Optuna-tuned on fold-0 calibration log
  loss), each with isotonic calibration on the week before the test window. Chosen: {summ['chosen']}.
- Backtest mean over 3 folds: Brier {m[summ['chosen']]['brier']:.5f} vs rolling 28-day baseline
  {m['rolling_28d_route_hour']['brier']:.5f}; ROC-AUC {m[summ['chosen']]['roc_auc']:.3f}; PR-AUC {m[summ['chosen']]['pr_auc']:.3f}
  (base rate ≈ {np.mean([f['base_rate_test'] for f in summ['folds']]):.3f}); ECE {m[summ['chosen']]['ece']:.4f}.
- Limitations: missed trips are rare (~2%); rolling rates for serving are as of the last training date.
"""


def write_report(a: dict, b: dict, promo: dict) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    L = ["# ML backtest report (latest)", "", "Rolling-origin backtest, 3 folds, time-based splits only "
         "(train on earlier weeks, calibrate on the next week, test on the following 2 weeks).", "",
         "## Model A — downstream delay (seconds)", "",
         "| fold | test window | p50 MAE | schedule | propagation | hist. median | coverage raw | coverage (CQR) | width |",
         "|---|---|---|---|---|---|---|---|---|"]
    for f in a["folds"]:
        L.append(f"| {f['fold']} | {f['test'][0]}–{f['test'][1]} | {f['p50_mae']:.1f} | {f['schedule_mae']:.1f} | "
                 f"{f['propagation_mae']:.1f} | {f['historical_median_mae']:.1f} | {f['coverage_raw']:.1%} | "
                 f"{f['coverage']:.1%} | {f['width_s']:.0f} |")
    L += ["", f"- **Mean p50 MAE {a['p50_mae']:.1f} s** vs best baseline `{a['best_baseline']}` "
          f"{a[a['best_baseline'] + '_mae']:.1f} s → **{a['improvement_vs_best_baseline_pct']:.1f}% better**.",
          f"- Mean RMSE {a['p50_rmse']:.1f} s; pinball p10/p50/p90 {a['pinball_p10']:.1f}/{a['pinball_p50']:.1f}/{a['pinball_p90']:.1f}.",
          f"- **80% interval coverage after conformal calibration: {a['coverage']:.1%}** (target 78–82%; raw quantiles "
          f"{a['coverage_raw']:.1%}); mean width {a['width_s']:.0f} s.", "",
          "Breakdowns by horizon and time band are in `ml/reports/backtest_results.parquet` (also the copilot view "
          "`v_backtest_results`).", "", "## Model B — missed-trip probability", "",
          "| method | Brier | ROC-AUC | PR-AUC | ECE |", "|---|---|---|---|---|"]
    for k, v in b["mean"].items():
        L.append(f"| {k} | {v['brier']:.5f} | {v['roc_auc']:.3f} | {v['pr_auc']:.3f} | {v['ece']:.4f} |")
    L += ["", f"- Chosen: `{b['chosen']}`; beats the rolling 28-day baseline on Brier: **{b['beats_rolling_baseline_brier']}**.",
          "", "Calibration curve (last fold):", "", "| mean predicted | observed | n |", "|---|---|---|"]
    L += [f"| {r['mean_pred']:.4f} | {r['observed']:.4f} | {r['n']} |" for r in b["calibration_curve_last_fold"]]
    L += ["", "## Promotion", ""] + [f"- {k}: {v}" for k, v in promo.items()]
    (REPORTS / "latest.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (REPORTS / "latest.json").write_text(json.dumps({"model_a": {k: v for k, v in a.items() if k != "folds"},
                                                     "model_b": {k: v for k, v in b.items() if k not in ("folds",)},
                                                     "promotion": promo}, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report-only", action="store_true", help="print the latest report")
    a = p.parse_args(argv)
    if a.report_only and (REPORTS / "latest.md").exists():
        print((REPORTS / "latest.md").read_text(encoding="utf-8"))
        return 0
    t0 = time.time()
    from datetime import UTC, datetime

    from .registry import setup_mlflow

    mlflow = setup_mlflow("ghostbus-training")
    reg = ModelRegistry()
    mlflow.start_run(run_name=datetime.now(UTC).strftime("train-%Y%m%d-%H%M%S"))
    mlflow.log_params({"seed": SEED, "folds": 3, "alpha": ALPHA, "max_train_rows_a": MAX_TRAIN_ROWS_A,
                       "optuna_trials": N_TRIALS})
    sa, ma, rows_a = train_model_a()
    sb, mb, rows_b = train_model_b()
    for f in sa["folds"]:
        mlflow.log_metrics({f"a_fold{f['fold']}_p50_mae": f["p50_mae"], f"a_fold{f['fold']}_coverage": f["coverage"],
                            f"a_fold{f['fold']}_width_s": f["width_s"]})
    for f in sb["folds"]:
        mlflow.log_metrics({f"b_fold{f['fold']}_brier_{k}": v["brier"] for k, v in f["results"].items()})
    mlflow.log_params({f"a_{k}": v for k, v in (sa.get("params") or {}).items()})
    mlflow.log_params({f"b_{k}": v for k, v in TUNING.get("model_b", {}).items() if not k.startswith("_")})

    ver = datetime.now(UTC).strftime("v%Y%m%d-%H%M%S")
    ma.version, mb.version = f"model_a-{ver}", f"model_b-{ver}"
    cfg = {"seed": SEED, "months": "2025-07..2025-12", "folds": 3, "sample_pct_model_a": 12}
    from gbdata.download import load_manifest

    cfg["data_hashes"] = {k: v.get("sha256") for k, v in load_manifest()["sources"].items() if k.startswith("arrdep")}
    promo = {}
    for name, summ, model, card in (("model_a", sa, ma, card_a(sa, ver)), ("model_b", sb, mb, card_b(sb, ver))):
        metrics = {k: v for k, v in summ.items() if not isinstance(v, (list, dict)) or k == "mean"}
        reg.save(name, ver, model, metrics, cfg, card)
        ok, why = reg.should_promote(name, metrics)
        if ok:
            reg.pin(name, ver)
        promo[name] = f"{'pinned' if ok else 'NOT pinned'} {ver}: {why}"
    pd.DataFrame(rows_a + rows_b).to_parquet(REPORTS / "backtest_results.parquet", index=False)
    write_report(sa, sb, promo)
    mlflow.log_metrics({"a_p50_mae": sa["p50_mae"], "a_coverage": sa["coverage"], "b_brier": sb["brier"]})
    mlflow.log_artifact(str(REPORTS / "latest.md"))
    mlflow.set_tags(promo)
    mlflow.end_run()
    print(f"done in {time.time() - t0:.0f}s", promo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
