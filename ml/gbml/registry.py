"""Model registry on MLflow (local tracking store: var/mlflow/mlflow.db, artifacts under var/mlflow/artifacts).

Each trained version is logged as an MLflow pyfunc model and registered as `ghostbus-<model>`; the version that
serves traffic carries the alias `champion`. Promotion rule: a new version gets the alias only if it does not regress
the primary metric (p50 MAE for Model A, Brier for Model B) by more than 1% against the current champion.
Version files (model.joblib, metrics, config, model card) are also written under ml/artifacts/<model>/<version>/,
and ml/artifacts/pinned.json mirrors the champion so serving still works if the MLflow store is unavailable.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import joblib
from gbcore.settings import REPO_ROOT, get_settings

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
ART = REPO_ROOT / "ml" / "artifacts"
PRIMARY = {"model_a": ("p50_mae", "lower"), "model_b": ("brier", "lower")}
REGRESSION_TOLERANCE = 0.01
ALIAS = "champion"


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return None


def mlflow_uri() -> str:
    d = get_settings().var_dir / "mlflow"
    d.mkdir(parents=True, exist_ok=True)
    return os.environ.get("MLFLOW_TRACKING_URI") or f"sqlite:///{(d / 'mlflow.db').as_posix()}"


def setup_mlflow(experiment: str):
    import mlflow

    mlflow.set_tracking_uri(mlflow_uri())
    art = (get_settings().var_dir / "mlflow" / "artifacts").as_uri()
    if mlflow.get_experiment_by_name(experiment) is None:
        mlflow.create_experiment(experiment, artifact_location=art)
    mlflow.set_experiment(experiment)
    return mlflow


try:
    import mlflow.pyfunc as _pyfunc

    class JoblibPyfunc(_pyfunc.PythonModel):
        """pyfunc wrapper so the registry serves the whole GhostBus model object (quantiles + conformal step)."""

        def load_context(self, context):
            self.obj = joblib.load(context.artifacts["model"])

        def predict(self, context, model_input, params=None):
            if hasattr(self.obj, "predict_proba"):
                return self.obj.predict_proba(model_input)
            return self.obj.predict(model_input)
except ImportError:  # pragma: no cover
    JoblibPyfunc = None  # type: ignore[assignment,misc]


class ModelRegistry:
    def __init__(self, root: Path = ART, use_mlflow: bool | None = None) -> None:
        self.root = root
        self.pinned_path = root / "pinned.json"
        # a registry rooted elsewhere (tests, scratch dirs) never touches the shared MLflow registry
        self.use_mlflow = (root == ART) if use_mlflow is None else use_mlflow

    def _client(self):
        from mlflow import MlflowClient

        return MlflowClient(tracking_uri=mlflow_uri())

    def pinned(self) -> dict:
        local = json.loads(self.pinned_path.read_text()) if self.pinned_path.exists() else {}
        if not self.use_mlflow:
            return local
        out = dict(local)
        try:
            c = self._client()
            for name in PRIMARY:
                try:
                    mv = c.get_model_version_by_alias(f"ghostbus-{name}", ALIAS)
                    out[name] = mv.tags.get("ghostbus_version", out.get(name))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        return out

    def save(self, name: str, version: str, obj, metrics: dict, config: dict, card: str) -> Path:
        d = self.root / name / version
        d.mkdir(parents=True, exist_ok=True)
        joblib.dump(obj, d / "model.joblib", compress=3)
        (d / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
        (d / "training_config.json").write_text(json.dumps({**config, "git_sha": git_sha(),
                                                             "saved_utc": datetime.now(UTC).isoformat()}, indent=2,
                                                            default=str))
        (d / "model_card.md").write_text(card, encoding="utf-8")
        if self.use_mlflow:
            self._register(name, version, d, metrics)
        return d

    def _register(self, name: str, version: str, d: Path, metrics: dict) -> None:
        import mlflow

        flat = {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))}
        active = mlflow.active_run()
        ctx = mlflow.start_run(run_name=f"{name}-{version}", nested=active is not None)
        with ctx:
            mlflow.log_metrics(flat)
            mlflow.log_artifact(str(d / "model_card.md"))
            mlflow.log_artifact(str(d / "training_config.json"))
            info = mlflow.pyfunc.log_model(name="model", python_model=JoblibPyfunc(),
                                           artifacts={"model": str(d / "model.joblib")},
                                           pip_requirements=["lightgbm", "mapie", "scikit-learn", "pandas", "numpy"],
                                           registered_model_name=f"ghostbus-{name}")
        c = self._client()
        mv = c.get_model_version(f"ghostbus-{name}", info.registered_model_version)
        c.set_model_version_tag(mv.name, mv.version, "ghostbus_version", version)
        c.set_model_version_tag(mv.name, mv.version, "git_sha", git_sha() or "")

    def load(self, name: str, version: str | None = None):
        version = version or self.pinned().get(name)
        if not version:
            return None, None
        d = self.root / name / version
        if (d / "model.joblib").exists():
            return joblib.load(d / "model.joblib"), version
        if self.use_mlflow:  # artifact only in the MLflow store (e.g. another machine)
            import mlflow

            c = self._client()
            for mv in c.search_model_versions(f"name='ghostbus-{name}'"):
                if mv.tags.get("ghostbus_version") == version:
                    path = mlflow.artifacts.download_artifacts(f"models:/{mv.name}/{mv.version}",
                                                               tracking_uri=mlflow_uri())
                    hits = list(Path(path).rglob("model.joblib"))
                    if hits:
                        return joblib.load(hits[0]), version
        return None, None

    def metrics(self, name: str, version: str) -> dict:
        return json.loads((self.root / name / version / "metrics.json").read_text())

    def should_promote(self, name: str, new_metrics: dict) -> tuple[bool, str]:
        cur = self.pinned().get(name)
        key, _ = PRIMARY[name]
        if not cur or not (self.root / name / cur / "metrics.json").exists():
            return True, "no champion yet"
        old = self.metrics(name, cur)[key]
        new = new_metrics[key]
        if new <= old * (1 + REGRESSION_TOLERANCE):
            return True, f"{key} {new:.4f} vs champion {old:.4f} (within 1%)"
        return False, f"{key} regressed: {new:.4f} vs champion {old:.4f} (> 1%)"

    def pin(self, name: str, version: str) -> None:
        p = json.loads(self.pinned_path.read_text()) if self.pinned_path.exists() else {}
        p[name] = version
        self.root.mkdir(parents=True, exist_ok=True)
        self.pinned_path.write_text(json.dumps(p, indent=2))
        if self.use_mlflow:
            c = self._client()
            for mv in c.search_model_versions(f"name='ghostbus-{name}'"):
                if mv.tags.get("ghostbus_version") == version:
                    c.set_registered_model_alias(mv.name, ALIAS, mv.version)
                    break
