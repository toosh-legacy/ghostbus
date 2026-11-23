"""Operational DB (spec 12.1): SQLAlchemy 2.0 models with portable types (SQLite lite / Postgres full)."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .settings import get_settings


class Base(DeclarativeBase):
    pass


class FeedVersion(Base):
    __tablename__ = "feed_version"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Route(Base):
    __tablename__ = "route"
    route_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    feed_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    short_name: Mapped[str | None] = mapped_column(String(64))
    long_name: Mapped[str | None] = mapped_column(Text)


class Stop(Base):
    __tablename__ = "stop"
    stop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feed_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stop_name: Mapped[str] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom_wkb: Mapped[bytes | None] = mapped_column(LargeBinary)  # PostGIS geometry in full mode


class Shape(Base):
    __tablename__ = "shape"
    shape_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feed_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    length_m: Mapped[float] = mapped_column(Float)
    geom_wkb: Mapped[bytes | None] = mapped_column(LargeBinary)


class Trip(Base):
    __tablename__ = "trip"
    trip_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feed_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    route_id: Mapped[str] = mapped_column(String(32), index=True)
    service_id: Mapped[str] = mapped_column(String(128))
    direction_id: Mapped[int] = mapped_column(Integer)
    shape_id: Mapped[str | None] = mapped_column(String(64))


class StopTime(Base):
    __tablename__ = "stop_time"
    trip_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stop_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stop_id: Mapped[str] = mapped_column(String(64), index=True)
    arrival_time: Mapped[str] = mapped_column(String(8))
    departure_time: Mapped[str] = mapped_column(String(8))


class StopOnShape(Base):
    __tablename__ = "stop_on_shape"
    shape_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    stop_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    dist_m: Mapped[float] = mapped_column(Float)
    offset_m: Mapped[float] = mapped_column(Float)


class ScheduledTripRow(Base):
    __tablename__ = "scheduled_trip"
    service_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    trip_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    route_id: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[int] = mapped_column(Integer)
    first_departure_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VehicleLatest(Base):
    __tablename__ = "vehicle_latest"
    vehicle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trip_id: Mapped[str | None] = mapped_column(String(64))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    dist_m: Mapped[float | None] = mapped_column(Float)
    vehicle_ts: Mapped[float] = mapped_column(Float)
    match_status: Mapped[str | None] = mapped_column(String(32))


class ArrivalObserved(Base):
    __tablename__ = "arrival_observed"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(String(64), index=True)
    service_date: Mapped[str] = mapped_column(String(10))
    stop_id: Mapped[str] = mapped_column(String(64))
    arrival_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(8))


class GhostEvalRow(Base):
    __tablename__ = "ghost_eval"
    trip_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    def_version: Mapped[str] = mapped_column(String(32))
    prior_prob: Mapped[float | None] = mapped_column(Float)


class PredictionLog(Base):
    __tablename__ = "prediction_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(String(64), index=True)
    stop_id: Mapped[str] = mapped_column(String(64))
    made_at: Mapped[float] = mapped_column(Float)  # event time (UTC epoch s)
    p10: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(64))
    features: Mapped[dict] = mapped_column(JSON)
    observed_ts: Mapped[float | None] = mapped_column(Float)


class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash: Mapped[str] = mapped_column(String(128), unique=True)
    owner_label: Mapped[str] = mapped_column(String(128))
    quota: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DatasetManifest(Base):
    __tablename__ = "dataset_manifest"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    url: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    rows: Mapped[int | None] = mapped_column(Integer)
    downloaded_at: Mapped[str | None] = mapped_column(String(40))
    schema_ok: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str | None] = mapped_column(String(32))


class EvalRun(Base):
    __tablename__ = "eval_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    suite: Mapped[str] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(40))
    metrics: Mapped[dict] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(Boolean)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(Text)


def make_engine(url: str | None = None):
    url = url or get_settings().sqlalchemy_url
    eng = create_engine(url, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _wal(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return eng


@lru_cache
def engine():
    get_settings().var_dir.mkdir(parents=True, exist_ok=True)
    return make_engine()


def session_factory():
    return sessionmaker(engine(), expire_on_commit=False)
