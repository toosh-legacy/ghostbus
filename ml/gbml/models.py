"""Model classes (kept in their own module so pickled artifacts load from any entry point)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.preprocessing import OneHotEncoder

from .data import FEATURES_A, FEATURES_B


@dataclass
class QuantileModelA:
    """LightGBM quantile regressors (p10/p50/p90) + MAPIE conformalized quantile regression (80% intervals).

    `mapie` is a prefit mapie.regression.ConformalizedQuantileRegressor over [p10, p90, p50] models, conformalized
    on the calibration week; `qhat` is its symmetric correction (seconds), kept for reports."""

    models: dict
    qhat: float
    cats: dict
    hist_median: dict = field(default_factory=dict)
    version: str = ""
    mapie: object | None = None

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        Xa = X[FEATURES_A].to_numpy(dtype=float)
        lo = self.models[0.1].predict(Xa)
        mid = self.models[0.5].predict(Xa)
        hi = self.models[0.9].predict(Xa)
        lo, hi = np.minimum(lo, mid), np.maximum(hi, mid)
        if self.mapie is not None:
            _, pis = self.mapie.predict_interval(Xa, symmetric_correction=True)
            p10, p90 = np.minimum(pis[:, 0, 0], mid), np.maximum(pis[:, 1, 0], mid)
        else:
            p10, p90 = lo - self.qhat, hi + self.qhat
        return pd.DataFrame({"p10": p10, "p50": mid, "p90": p90, "raw_p10": lo, "raw_p90": hi})


@dataclass
class CalibratedClassifierB:
    kind: str
    model: object
    iso: IsotonicRegression
    cats: dict
    fill: dict
    enc: OneHotEncoder | None = None
    version: str = ""

    def _X(self, df: pd.DataFrame) -> pd.DataFrame:
        X = df.copy()
        X["route_code"] = X["route_id"].map(self.cats).fillna(-1).astype(int)
        for c, v in self.fill.items():
            X[c] = X[c].fillna(v)
        return X[FEATURES_B]

    def raw(self, df: pd.DataFrame) -> np.ndarray:
        X = self._X(df)
        if self.enc is not None:
            Xc = self.enc.transform(X[["route_code", "direction_id", "hour", "dow"]])
            from scipy.sparse import hstack

            X = hstack([Xc, X[["holiday", "route_rate_7d", "route_rate_28d", "hour_rate_28d"]].to_numpy() * 10,
                        X[["trips_that_day"]].to_numpy() / 300.0]).tocsr()
        return self.model.predict_proba(X)[:, 1]

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        return self.iso.predict(self.raw(df))


