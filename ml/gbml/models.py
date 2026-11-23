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
    """p10/p50/p90 gradient boosting + conformal (CQR) adjustment."""

    models: dict
    qhat: float
    cats: dict
    hist_median: dict = field(default_factory=dict)
    version: str = ""

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X[FEATURES_A]
        lo = self.models[0.1].predict(X)
        mid = self.models[0.5].predict(X)
        hi = self.models[0.9].predict(X)
        lo, hi = np.minimum(lo, mid), np.maximum(hi, mid)
        return pd.DataFrame({"p10": lo - self.qhat, "p50": mid, "p90": hi + self.qhat, "raw_p10": lo, "raw_p90": hi})


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


