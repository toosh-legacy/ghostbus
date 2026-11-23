"""Load static network + dataset manifest into the operational DB (idempotent upserts via delete+insert)."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from gbcore.db import (
    Base,
    DatasetManifest,
    FeedVersion,
    Route,
    Shape,
    Stop,
    StopOnShape,
    engine,
    session_factory,
)
from gbcore.settings import get_settings
from shapely import wkb
from shapely.geometry import LineString, Point
from sqlalchemy import delete

from .download import load_manifest


def run() -> dict:
    s = get_settings()
    eng = engine()
    Base.metadata.create_all(eng)  # no-op when Alembic already migrated
    d = s.lake_dir / "curated" / "static"
    man = load_manifest()["sources"]
    cur = man.get("gtfs_current", {})
    fv = (cur.get("sha256") or "current")[:16]
    routes = pd.read_parquet(d / "routes.parquet")
    stops = pd.read_parquet(d / "stops.parquet")
    shapes = pd.read_parquet(d / "shapes.parquet")
    sos = pd.read_parquet(d / "stop_on_shape.parquet")
    Session = session_factory()
    with Session.begin() as ses:
        for model in (FeedVersion, Route, Stop, Shape, StopOnShape, DatasetManifest):
            ses.execute(delete(model))
        ses.add(FeedVersion(id=fv, source_url=cur.get("url", ""), sha256=cur.get("sha256", ""), start_date=None,
                            end_date=None, loaded_at=datetime.now(UTC)))
        ses.add_all(Route(route_id=r.route_id, feed_version_id=fv, short_name=r.route_short_name,
                          long_name=r.route_long_name) for r in routes.itertuples())
        ses.add_all(Stop(stop_id=r.stop_id, feed_version_id=fv, stop_name=r.stop_name, lat=r.stop_lat, lon=r.stop_lon,
                         geom_wkb=wkb.dumps(Point(r.stop_lon, r.stop_lat))) for r in stops.itertuples())
        for sid, g in shapes.groupby("shape_id"):
            ses.add(Shape(shape_id=sid, feed_version_id=fv, length_m=float(g["dist_m"].iloc[-1]),
                          geom_wkb=wkb.dumps(LineString(g[["lon", "lat"]].to_numpy()))))
        ses.add_all(StopOnShape(shape_id=r.shape_id, stop_id=r.stop_id, stop_sequence=int(r.stop_sequence),
                                dist_m=float(r.dist_m), offset_m=float(r.offset_m))
                    for r in sos.drop_duplicates(["shape_id", "stop_id", "stop_sequence"]).itertuples())
        for name, v in man.items():
            ses.add(DatasetManifest(name=name, url=v.get("url"), sha256=v.get("sha256"),
                                    rows=v.get("rows") or v.get("rows_pilot"), downloaded_at=v.get("downloaded_at"),
                                    schema_ok=v.get("schema_ok", True), source=str(v.get("source", "real"))[:32]))
    return {"feed_version": fv, "routes": len(routes), "stops": len(stops), "shapes": shapes["shape_id"].nunique()}
