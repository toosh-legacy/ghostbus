from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from gbcore.settings import get_settings
from gbml.data import split, time_folds
from gbml.online import join_predictions_to_arrivals, live_coverage, psi
from gbml.registry import ModelRegistry
from gbml.train import cqr_qhat

LAKE = get_settings().lake_dir / "curated"
needs_data = pytest.mark.skipif(not (LAKE / "arrdep").exists(), reason="run `make data` first")


def test_time_folds_are_strictly_ordered():
    dates = pd.Series(pd.date_range("2025-07-01", "2025-12-31"))
    folds = time_folds(dates)
    assert len(folds) == 3
    for f in folds:
        assert f["train_end"] < f["calib"][0] <= f["calib"][1] < f["test"][0] <= f["test"][1]
    assert folds[0]["test"][1] < folds[1]["test"][0] and folds[-1]["test"][1] == date(2025, 12, 31)
    df = pd.DataFrame({"service_date": dates})
    tr, ca, te = split(df, folds[0])
    assert tr.service_date.max() < ca.service_date.min() and ca.service_date.max() < te.service_date.min()


def test_cqr_reaches_target_coverage():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 100, 20000)
    lo, hi = np.full_like(y, -50.0), np.full_like(y, 50.0)  # badly under-covering raw quantiles
    q = cqr_qhat(y[:10000], lo[:10000], hi[:10000], alpha=0.2)
    cov = np.mean((y[10000:] >= lo[10000:] - q) & (y[10000:] <= hi[10000:] + q))
    assert 0.78 <= cov <= 0.82


def test_psi():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 1, 5000)
    assert psi(a, rng.normal(0, 1, 5000)) < 0.05
    assert psi(a, rng.normal(1.5, 1, 5000)) > 0.2


def test_registry_promotion_rule(tmp_path):
    reg = ModelRegistry(tmp_path)
    ok, _ = reg.should_promote("model_a", {"p50_mae": 100.0})
    assert ok
    reg.save("model_a", "v1", {"x": 1}, {"p50_mae": 100.0}, {}, "card")
    reg.pin("model_a", "v1")
    assert reg.should_promote("model_a", {"p50_mae": 100.9})[0]  # within 1%
    assert not reg.should_promote("model_a", {"p50_mae": 101.5})[0]  # regression > 1%
    obj, v = reg.load("model_a")
    assert obj == {"x": 1} and v == "v1"


def test_delayed_label_join_only_uses_later_outcomes():
    log = [{"trip_id": "t", "stop_id": "s", "made_at": 100.0, "p10": 150.0, "p50": 200.0, "p90": 260.0, "model_version": "m"},
           {"trip_id": "t", "stop_id": "s", "made_at": 300.0, "p10": 290.0, "p50": 300.0, "p90": 310.0, "model_version": "m"}]
    arr = [SimpleNamespace(trip_id="t", stop_id="s", arrival_ts=250.0, kind="stop")]
    j = join_predictions_to_arrivals(log, arr)
    assert len(j) == 1  # the prediction made after the arrival is excluded
    cov = live_coverage(log, arr)
    assert cov["n"] == 1 and cov["coverage"] == 1.0 and cov["p50_mae_s"] == 50.0


@needs_data
def test_model_a_features_never_use_future_data():
    from gbml.data import build_model_a_samples

    df = build_model_a_samples(sample_pct=2)
    assert len(df) > 1000
    has = df["recent_seg_t"].notna()
    assert (df.loc[has, "recent_seg_t"] < df.loc[has, "t_pred"]).all()
    assert (df["t_target"] > df["t_pred"]).all()  # targets are strictly in the future
    assert (df["horizon"] >= 1).all()


@needs_data
def test_model_b_rolling_rates_use_only_previous_days():
    from gbml.data import build_model_b_frame

    df = build_model_b_frame()
    r = df[df["route_id"] == "1"].groupby("service_date").agg(y=("y", "mean"), n=("y", "size"), m=("y", "sum"),
                                                               r7=("route_rate_7d", "first")).sort_index()
    d = r.index[20]
    prev = r.loc[(r.index < d) & (r.index >= d - pd.Timedelta(days=7))]
    assert r.loc[d, "r7"] == pytest.approx(prev["m"].sum() / prev["n"].sum())
    assert df[df["service_date"] == df["service_date"].min()]["route_rate_7d"].isna().all()


@pytest.mark.skipif(not (get_settings().lake_dir.parent / "ml" / "artifacts" / "pinned.json").exists(),
                    reason="run `make train` first")
def test_pinned_models_load_and_predict():
    from gbml.serving import load_pinned
    from gbstream.network import Network

    a, b = load_pinned()
    assert a is not None and b is not None and a.version.startswith("model_a")
    net = Network()
    pri = b.trip_priors(net, date(2026, 11, 24))
    assert pri and all(0 <= v <= 1 for v in pri.values())
    tid = next(iter(pri))
    p = net.pattern(tid)
    pred = a.with_fallback(None).predict(route_id=p.route_id, direction_id=p.direction_id, stop_id=p.stop_ids[10],
                                         sched_ts=1000.0, sched_local_hour=8, current_delay_s=120.0, live=True,
                                         pattern=p, stop_index=10, last_stop_index=3, now=900.0,
                                         service_date=date(2026, 11, 24))
    assert pred.p10_ts <= pred.p50_ts <= pred.p90_ts and pred.source == "model"
