# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""SageMaker inference server for this project's models.

SageMaker runs the training image with the ``serve`` argument to start a model
server that must answer:
  - ``GET  /ping``         -> 200 when the model is loaded (health check)
  - ``POST /invocations``  -> run prediction on the request body

SageMaker downloads and extracts ``model.tar.gz`` into ``/opt/ml/model`` before
starting the container, so the model is loaded from there. The server uses only
the Python standard library to avoid adding heavy web dependencies to the
(already large) training image.

Three model families are supported, resolved in this order:
  1. **Bring-your-own (BYO)** — the payload ships its own ``inference.py``
     (see CUSTOM_HANDLER_CANDIDATES). Used when a model was trained OUTSIDE this
     platform (e.g. an algorithm team's scikit-learn/XGBoost model) and the
     training task only transferred the files. Takes precedence so a BYO payload
     is never mistaken for an AutoGluon one.
  2. AutoGluon ``TimeSeriesPredictor`` (e.g. hourly sensor forecasting).
  3. AutoGluon ``TabularPredictor``  (e.g. startup-duration regression).

Invocation contract (JSON) — the same wire format for all three, so callers and
the platform's inference Lambda do not care which family is deployed:
  request:  {"instances": [ {...}, ... ]}
  response: {"predictions": [ {...}, ... ]}
A bare list body is also accepted as the instances array.

Per family, the instance/prediction shape is:
  - timeseries: {"item_id","timestamp","target"} -> {"item_id","timestamp","mean"}
  - tabular:    a feature row                    -> {"prediction": <float>}
  - custom:     whatever the payload's inference.py accepts/returns (normalised
                into the envelope above; see _predict_custom).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")

# Bring-your-own entry point, looked up relative to the extracted model dir.
# ``code/inference.py`` is the layout SageMaker's own framework containers use,
# so a payload written for those works here unchanged.
CUSTOM_HANDLER_CANDIDATES = ("inference.py", "code/inference.py")

_predictor: Any = None
_predictor_kind: str | None = None  # "timeseries" | "tabular" | "custom"
_custom_module: Any = None
_predictor_lock = threading.Lock()
_load_error: str | None = None


def _find_predictor_dir(root: str) -> str:
    """Locate the AutoGluon predictor directory inside the extracted model dir.

    The training job uploads the predictor under an inner ``models/`` folder, so
    accept either ``<root>`` or ``<root>/models`` (the one containing
    predictor.pkl)."""
    candidates = [root, os.path.join(root, "models")]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "predictor.pkl")):
            return path
    # Fall back to a recursive search for predictor.pkl.
    for current, _dirs, files in os.walk(root):
        if "predictor.pkl" in files:
            return current
    return root


def _load_custom_handler(root: str) -> Any | None:
    """Return the payload's own inference module, or None if this is not a BYO
    payload.

    Contract (a deliberate subset of SageMaker's framework convention, so an
    existing handler usually works unchanged):
      - ``model_fn(model_dir) -> model``          REQUIRED
      - ``predict_fn(data, model) -> predictions`` optional; without it we call
        ``model.predict(DataFrame(instances))``, the sklearn/XGBoost convention.
    ``input_fn`` / ``output_fn`` are intentionally NOT used: this server always
    speaks the project's JSON envelope (see the module docstring) so the platform
    can treat every endpoint identically.
    """
    for rel in CUSTOM_HANDLER_CANDIDATES:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        import importlib.util

        # Put the payload's own directory first on sys.path so that the
        # handler's relative imports (helper modules, feature code shipped
        # alongside it) and any data files it opens resolve correctly.
        module_dir = os.path.dirname(path) or root
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)
        spec = importlib.util.spec_from_file_location("byo_inference", path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "model_fn", None)):
            raise RuntimeError(
                f"{rel} was found but does not define a callable model_fn(model_dir); "
                "a bring-your-own payload must expose model_fn"
            )
        return module
    return None


def _load_predictor() -> Any:
    """Load the model, trying the bring-your-own payload first and then the two
    AutoGluon families (a saved predictor.pkl only loads with its matching
    class). ``_predictor_kind`` records which one won, and prediction is routed
    by it."""
    global _predictor, _predictor_kind, _custom_module, _load_error
    if _predictor is not None:
        return _predictor
    with _predictor_lock:
        if _predictor is not None:
            return _predictor
        # BYO takes precedence: a payload shipping inference.py is authoritative
        # about how to load itself, and must never fall through to AutoGluon.
        # A failure here is raised as-is rather than collected into `errors`,
        # because "their handler is broken" is a very different diagnosis from
        # "this isn't an AutoGluon predictor".
        module = _load_custom_handler(MODEL_DIR)
        if module is not None:
            model = module.model_fn(MODEL_DIR)
            if model is None:
                raise RuntimeError(
                    "inference.py model_fn(model_dir) returned None; it must "
                    "return the loaded model object"
                )
            _custom_module = module
            _predictor = model
            _predictor_kind = "custom"
            _load_error = None
            return _predictor
        predictor_dir = _find_predictor_dir(MODEL_DIR)
        errors = []
        try:
            from autogluon.timeseries import TimeSeriesPredictor

            _predictor = TimeSeriesPredictor.load(predictor_dir)
            _predictor_kind = "timeseries"
            _load_error = None
            return _predictor
        except Exception as exc:  # noqa: BLE001 - try the other family next
            errors.append(f"timeseries: {type(exc).__name__}: {exc}")
        try:
            from autogluon.tabular import TabularPredictor

            _predictor = TabularPredictor.load(predictor_dir)
            _predictor_kind = "tabular"
            _load_error = None
            return _predictor
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tabular: {type(exc).__name__}: {exc}")
        _load_error = "; ".join(errors)
        raise RuntimeError(f"could not load predictor from {predictor_dir}: {_load_error}")


def _predict(payload: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    instances = payload.get("instances") if isinstance(payload, dict) else payload
    if not isinstance(instances, list) or not instances:
        raise ValueError("request must contain a non-empty 'instances' list")

    predictor = _load_predictor()
    if _predictor_kind == "custom":
        return _predict_custom(predictor, instances)
    if _predictor_kind == "tabular":
        return _predict_tabular(predictor, pd.DataFrame(instances))

    from autogluon.timeseries import TimeSeriesDataFrame

    frame = pd.DataFrame(instances)
    if "item_id" not in frame or "timestamp" not in frame or "target" not in frame:
        raise ValueError("each instance needs item_id, timestamp and target")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    ts_df = TimeSeriesDataFrame.from_data_frame(
        frame, id_column="item_id", timestamp_column="timestamp"
    )

    try:
        forecast = predictor.predict(ts_df)
    except RuntimeError as exc:
        # AutoGluon swallows the sub-model's real exception and only reports
        # "Following models failed to predict". Re-run the failed model directly
        # to surface the true error (e.g. Chronos weight download / context).
        if "failed to predict" not in str(exc):
            raise
        # predictor.predict(model=name) still routes through the trainer's
        # get_model_pred_dict, which try/excepts each sub-model and re-raises the
        # same generic message — the real cause never surfaces. Load the model
        # OBJECT directly from the trainer and call its predict() so the true
        # exception (e.g. Chronos HF download / context length) is raised bare.
        import traceback as _tb

        details = []
        try:
            trainer = predictor._learner.load_trainer()
            model_names = trainer.get_model_names()
        except Exception:  # noqa: BLE001 - diagnostic
            trainer = None
            model_names = []
        for name in model_names:
            try:
                model = trainer.load_model(name)
                model.predict(ts_df)
            except Exception:  # noqa: BLE001 - diagnostic
                details.append(f"model {name}: {_tb.format_exc()}")
        raise RuntimeError(
            str(exc) + "\n\n--- per-model failures ---\n" + "\n".join(details)
        ) from exc
    # forecast is a TimeSeriesDataFrame indexed by (item_id, timestamp).
    reset = forecast.reset_index()
    point_col = "0.5" if "0.5" in reset.columns else "mean"
    predictions = [
        {
            "item_id": str(row["item_id"]),
            "timestamp": str(row["timestamp"]),
            "mean": float(row[point_col]),
        }
        for _, row in reset.iterrows()
    ]
    return {"predictions": predictions}


def _predict_custom(model: Any, instances: list[Any]) -> dict[str, Any]:
    """Bring-your-own inference: hand the instances to the payload's predict_fn
    when it provides one, else fall back to ``model.predict(DataFrame)``.

    The payload is trusted to know its own input shape, so `instances` is passed
    through verbatim (a list of whatever the caller sent) rather than being
    coerced. Only the RESULT is normalised, so every endpoint answers with the
    project's {"predictions": [...]} envelope regardless of what the handler
    returns."""
    predict_fn = getattr(_custom_module, "predict_fn", None)
    if callable(predict_fn):
        result = predict_fn(instances, model)
    else:
        import pandas as pd

        result = model.predict(pd.DataFrame(instances))
    return _normalise_custom_result(result)


def _normalise_custom_result(result: Any) -> dict[str, Any]:
    """Coerce whatever a BYO handler returned into {"predictions": [ ... ]}.

    - a dict is trusted and returned as-is (the handler already built the
      envelope, or deliberately returns a richer payload);
    - anything iterable (list, numpy array, pandas Series) becomes one entry per
      element: dict elements pass through, scalars become {"prediction": value};
    - a bare scalar becomes a single-element list.
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, (str, bytes)) or not hasattr(result, "__iter__"):
        values: list[Any] = [result]
    else:
        values = list(result)
    predictions: list[Any] = []
    for value in values:
        if isinstance(value, dict):
            predictions.append(value)
            continue
        try:
            predictions.append({"prediction": float(value)})
        except (TypeError, ValueError):
            # Non-numeric output (e.g. a class label) — keep it verbatim.
            predictions.append({"prediction": value})
    return {"predictions": predictions}


def _predict_tabular(predictor: Any, frame: "Any") -> dict[str, Any]:
    """Tabular-regression inference (e.g. startup-duration). Each instance
    is a feature row; return one scalar prediction per row.

    The caller must send EVERY feature column the model was trained on.
    AutoGluon's feature generator does NOT tolerate missing columns — it raises
    ``KeyError: "<n> required columns are missing ..."``, which surfaces as a 400
    from /invocations. Extra columns are ignored. The authoritative column list
    for a given model is the ``features_used`` field of its
    evaluation_report.json."""
    predictions = predictor.predict(frame)
    values = [float(v) for v in list(predictions)]
    return {"predictions": [{"prediction": v} for v in values]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - required name
        if self.path.rstrip("/") == "/ping":
            try:
                _load_predictor()
                self._send(200, {"status": "healthy"})
            except Exception:
                self._send(503, {"status": "unhealthy", "error": _load_error})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - required name
        if self.path.rstrip("/") != "/invocations":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
            self._send(200, _predict(payload))
        except Exception as exc:
            # Print the full traceback to stderr so it lands in the endpoint's
            # CloudWatch logs (the short message alone hides the real cause, e.g.
            # AutoGluon wrapping a Chronos sub-model error). Also surface a
            # trimmed traceback tail in the response for quick diagnosis.
            import traceback

            tb = traceback.format_exc()
            print(f"[serve] prediction failed:\n{tb}", flush=True)
            self._send(
                400,
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": tb[-1500:],
                },
            )

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        return


def main() -> int:
    port = int(os.environ.get("SAGEMAKER_BIND_TO_PORT", "8080"))
    # Warm the model so the first /ping reflects real load status; tolerate
    # failure so SageMaker sees an explicit unhealthy ping instead of a crash.
    try:
        _load_predictor()
    except Exception as exc:  # pragma: no cover
        print(f"[serve] model load failed at startup: {exc}", file=sys.stderr)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[serve] inference server listening on :{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
