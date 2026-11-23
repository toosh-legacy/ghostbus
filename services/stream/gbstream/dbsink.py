"""Operational-DB sink: mirrors live outputs into SQLAlchemy tables (SQLite lite / Postgres full).

- vehicle_latest  : upsert of the latest position per vehicle
- arrival_observed: append of newly detected arrivals
- ghost_eval      : latest evaluation per (trip, service_date); full history stays in the lake
- prediction_log  : appended predictions; observed_ts filled in by the delayed-label join
"""

from __future__ import annotations

from datetime import UTC, datetime

from gbcore.db import (
    ArrivalObserved,
    Base,
    GhostEvalRow,
    PredictionLog,
    VehicleLatest,
    engine,
    session_factory,
)
from gbcore.timeutil import service_date_for
from sqlalchemy import delete, select, update

from .processor import Processor


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


class DbSink:
    def __init__(self, session_factory_=None) -> None:
        if session_factory_ is None:
            Base.metadata.create_all(engine())
            session_factory_ = session_factory()
        self.Session = session_factory_
        self._arr_i = 0
        self._pred_i = 0

    def flush(self, proc: Processor, prediction_log: list[dict] | None = None) -> dict:
        new_arr = proc.arrivals[self._arr_i:]
        self._arr_i = len(proc.arrivals)
        preds = (prediction_log or [])[self._pred_i:]
        self._pred_i = len(prediction_log or [])
        latest_ghost: dict[tuple[str, str], object] = {}
        for g in proc.ghost_evals[-5000:]:
            latest_ghost[(g.trip_id, g.service_date)] = g
        with self.Session.begin() as s:
            for v in proc.state.vehicles():
                s.merge(VehicleLatest(vehicle_id=v["vehicle_id"], trip_id=v.get("trip_id"), lat=v["lat"], lon=v["lon"],
                                      dist_m=v.get("dist_m"), vehicle_ts=v["vehicle_ts"],
                                      match_status=v.get("match_status")))
            s.add_all(ArrivalObserved(trip_id=a.trip_id, service_date=service_date_for(_dt(a.arrival_ts)).isoformat(),
                                      stop_id=a.stop_id, arrival_utc=_dt(a.arrival_ts), kind=a.kind,
                                      confidence=a.confidence, source=a.source) for a in new_arr)
            for (tid, sd), g in latest_ghost.items():
                s.merge(GhostEvalRow(trip_id=tid, service_date=sd, evaluated_at=_dt(g.evaluated_at), state=g.state,
                                     reason=g.reason, def_version=g.def_version, prior_prob=g.prior_prob))
            s.add_all(PredictionLog(trip_id=p["trip_id"], stop_id=p["stop_id"], made_at=p["made_at"], p10=p["p10"],
                                    p50=p["p50"], p90=p["p90"], model_version=p["model_version"],
                                    features={k: (float(v) if isinstance(v, (int, float)) else str(v))
                                              for k, v in (p.get("features") or {}).items() if v is not None})
                      for p in preds)
            # delayed-label join: fill observed arrival times for predictions made before the arrival
            for a in new_arr:
                if a.kind == "departure":
                    continue
                s.execute(update(PredictionLog)
                          .where(PredictionLog.trip_id == a.trip_id, PredictionLog.stop_id == a.stop_id,
                                 PredictionLog.observed_ts.is_(None), PredictionLog.made_at < a.arrival_ts)
                          .values(observed_ts=a.arrival_ts))
        return {"arrivals": len(new_arr), "ghosts": len(latest_ghost), "predictions": len(preds)}

    def clear(self) -> None:
        with self.Session.begin() as s:
            for m in (VehicleLatest, ArrivalObserved, GhostEvalRow, PredictionLog):
                s.execute(delete(m))

    def counts(self) -> dict:
        with self.Session() as s:
            return {m.__tablename__: len(s.execute(select(m)).all())
                    for m in (VehicleLatest, ArrivalObserved, GhostEvalRow, PredictionLog)}
