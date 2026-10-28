# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import csv
import io
import json
from typing import Any

import boto3
from botocore.exceptions import ClientError


class InferenceInvocationError(RuntimeError):
    """Raised when a synchronous inference request fails."""


class _MissingObject(InferenceInvocationError):
    """Internal: the key holds no object, so it may name a directory.

    Kept distinct from a parse failure — falling back to directory expansion on
    a malformed JSON file would replace its syntax error with a misleading
    "directory contains no files".
    """


# S3 error codes meaning "that key holds no object".
_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "NotFound", "404"})


# SageMaker real-time endpoints cap the request/response payload at ~6 MB, so
# bound how many rows we send in one synchronous invocation.
MAX_INSTANCES = 5000

# Hard ceiling enforced by SageMaker InvokeEndpoint. Checked locally so an
# oversized batch reports its own size instead of a generic ClientError.
MAX_PAYLOAD_BYTES = 6 * 1024 * 1024

# When data_location is a directory, bound how many objects we pull so a
# mistakenly broad prefix cannot make the Lambda list/read indefinitely.
MAX_INPUT_FILES = 500

# Extensions read when expanding a directory. Everything else (README.md,
# .DS_Store, .parquet, ...) is skipped rather than fed to the CSV parser, which
# would otherwise turn binary junk into garbage instances.
DIRECTORY_FILE_EXTENSIONS = (".json", ".csv")


def resolve_s3_uri(location: str, bucket_name: str) -> str:
    """Resolve a data_location to a full s3:// URI. A bare prefix like
    ``data/predict.csv`` is resolved against the platform data bucket; an
    ``s3://`` URI is returned as-is."""
    loc = location.strip()
    if loc.startswith("s3://"):
        return loc
    return f"s3://{bucket_name}/{loc.lstrip('/')}"


def _split_s3_uri(uri: str) -> tuple[str, str]:
    without_scheme = uri[len("s3://"):]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def _coerce_number(value: str) -> float | int | None:
    """Return an int/float for a numeric string, else None. Used to turn CSV
    string cells into numbers so the model gets a numeric target column."""
    text = value.strip()
    if not text:
        return None
    try:
        if ("." not in text) and ("e" not in text.lower()):
            return int(text)
        return float(text)
    except ValueError:
        return None


class SageMakerInferenceAdapter:
    """Runs SYNCHRONOUS inference against a real-time SageMaker endpoint.

    The deployed endpoint (see deployment/sagemaker_adapter.py + serve.py) is a
    real-time endpoint exposing POST /invocations that accepts
    ``{"instances": [{item_id, timestamp, target}, ...]}`` and returns
    ``{"predictions": [...]}``. It does NOT support InvokeEndpointAsync, so we
    read the prediction CSV from S3, invoke the endpoint synchronously, and
    write the predictions back to the output location."""

    def __init__(
        self,
        runtime_client: Any | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self._runtime = runtime_client or boto3.client("sagemaker-runtime")
        self._s3 = s3_client or boto3.client("s3")

    def _list_directory_keys(self, bucket: str, prefix: str) -> list[str]:
        """List readable object keys directly under (and below) ``prefix``.

        Directory placeholders (keys ending in ``/``), empty objects and
        unsupported extensions are dropped. Keys are sorted so a directory of
        per-machine or per-day files produces a deterministic instance order.
        """
        keys: list[str] = []
        skipped: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            try:
                page = self._s3.list_objects_v2(**kwargs)
            except ClientError as exc:
                raise InferenceInvocationError(
                    f"could not list prediction input s3://{bucket}/{prefix}: {exc}"
                ) from exc
            for item in page.get("Contents") or []:
                key = item.get("Key") or ""
                if not key or key.endswith("/") or not item.get("Size"):
                    continue
                if key.lower().endswith(DIRECTORY_FILE_EXTENSIONS):
                    keys.append(key)
                else:
                    skipped.append(key)
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
            if not token:
                break

        if not keys:
            detail = (
                f"; skipped unsupported files: {sorted(skipped)[:10]}"
                if skipped
                else ""
            )
            raise InferenceInvocationError(
                f"prediction input directory s3://{bucket}/{prefix} contains no "
                f"{' or '.join(DIRECTORY_FILE_EXTENSIONS)} files{detail}"
            )
        keys.sort()
        if len(keys) > MAX_INPUT_FILES:
            raise InferenceInvocationError(
                f"prediction input directory s3://{bucket}/{prefix} has {len(keys)} "
                f"files, above the {MAX_INPUT_FILES} limit; narrow the prefix or "
                f"merge the files"
            )
        return keys

    def _read_rows(self, bucket: str, key: str) -> list[dict[str, Any]]:
        """Read one object and parse it into rows. Format is picked by the
        object's extension (.json vs anything else), per object — so a directory
        may freely mix .json and .csv files."""
        uri = f"s3://{bucket}/{key}"
        try:
            body = self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        except ClientError as exc:
            code = str((exc.response or {}).get("Error", {}).get("Code", ""))
            if code in _MISSING_OBJECT_CODES:
                raise _MissingObject(f"no object at {uri}") from exc
            raise InferenceInvocationError(
                f"could not read prediction input {uri}: {exc}"
            ) from exc
        text = body.decode("utf-8-sig")
        if key.lower().endswith(".json"):
            return self._parse_json_rows(text, uri)
        return list(csv.DictReader(io.StringIO(text)))

    def _read_many(self, bucket: str, keys: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in keys:
            rows.extend(self._read_rows(bucket, key))
            if len(rows) >= MAX_INSTANCES:
                break        # _rows_to_instances caps anyway; stop fetching early
        return rows

    def _read_instances(
        self, input_uri: str, target_column: str = "target"
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Read prediction rows from the S3 input, supporting CSV and JSON, and
        accepting either a single object or a directory.

        ``input_uri`` may be:
          - a single object  (``.../predict.json``)
          - a directory      (``.../batch/`` — every .json/.csv underneath is
            read and their rows concatenated in sorted key order)

        A location without a trailing ``/`` is tried as an object first and
        falls back to directory expansion when no such object exists, so callers
        need not know which one they have.

        Rows from all files funnel through the SAME shaping logic
        (_rows_to_instances) exactly once, so the time-series vs tabular
        decision is made consistently for the whole batch rather than per file.

        JSON accepted shapes (each row is an object of column -> value):
          - {"instances": [ {..}, {..} ]}
          - [ {..}, {..} ]
          - {..}                      (a single row)

        Returns ``(instances, source_keys)``; source_keys records what was
        actually read so a directory run stays traceable.
        """
        bucket, key = _split_s3_uri(input_uri)
        if not bucket or not key:
            raise InferenceInvocationError(f"invalid input location: {input_uri}")

        if key.endswith("/"):
            keys = self._list_directory_keys(bucket, key)
            rows = self._read_many(bucket, keys)
        else:
            try:
                rows, keys = self._read_rows(bucket, key), [key]
            except _MissingObject:
                # Callers routinely omit the trailing slash on a folder.
                keys = self._list_directory_keys(bucket, key + "/")
                rows = self._read_many(bucket, keys)

        instances = self._rows_to_instances(rows, target_column)
        if not instances:
            source = input_uri if len(keys) == 1 else f"{input_uri} ({len(keys)} 个文件)"
            raise InferenceInvocationError(f"prediction input {source} has no rows")
        return instances, keys

    @staticmethod
    def _parse_json_rows(text: str, input_uri: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise InferenceInvocationError(
                f"prediction input {input_uri} is not valid JSON: {exc}"
            ) from exc
        if isinstance(data, dict) and isinstance(data.get("instances"), list):
            rows = data["instances"]
        elif isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = [data]
        else:
            raise InferenceInvocationError(
                f"prediction input {input_uri} JSON must be an object, a list of "
                'objects, or {"instances": [...]}'
            )
        if not all(isinstance(r, dict) for r in rows):
            raise InferenceInvocationError(
                f"prediction input {input_uri} JSON rows must be objects"
            )
        return rows

    @staticmethod
    def _rows_to_instances(
        rows: list[dict[str, Any]], target_column: str
    ) -> list[dict[str, Any]]:
        """Shape parsed rows into endpoint instances (shared by CSV and JSON).

        Two input shapes, auto-detected from the FIRST row's keys:
          - TIME SERIES: rows have item_id + timestamp; reduced to
            item_id/timestamp/target (the target column renamed to `target`).
          - TABULAR REGRESSION (e.g. machine startup duration): feature rows passed through
            verbatim (numeric strings coerced); AutoGluon TabularPredictor
            tolerates extra/missing columns and predicts a scalar per row.
        """
        instances: list[dict[str, Any]] = []
        if not rows:
            return instances
        first_keys = set(rows[0].keys())
        is_time_series = "item_id" in first_keys and "timestamp" in first_keys

        if is_time_series:
            if target_column != "target" and target_column not in first_keys:
                raise InferenceInvocationError(
                    f"target column '{target_column}' not found in prediction input; "
                    f"available columns: {sorted(first_keys)}"
                )
            for row in rows:
                raw_target = row.get(target_column)
                target_value: Any = raw_target
                if isinstance(raw_target, str):
                    coerced = _coerce_number(raw_target)
                    if coerced is not None:
                        target_value = coerced
                instances.append(
                    {
                        "item_id": row.get("item_id"),
                        "timestamp": row.get("timestamp"),
                        "target": target_value,
                    }
                )
                if len(instances) >= MAX_INSTANCES:
                    break
        else:
            for row in rows:
                instance: dict[str, Any] = {}
                for name, value in row.items():
                    if name is None:
                        continue
                    coerced = _coerce_number(value) if isinstance(value, str) else value
                    instance[name] = coerced if coerced is not None else value
                instances.append(instance)
                if len(instances) >= MAX_INSTANCES:
                    break
        return instances

    def run_sync(
        self,
        *,
        endpoint_name: str,
        input_uri: str,
        output_uri: str,
        target_column: str = "target",
    ) -> dict[str, Any]:
        """Read instances (CSV or JSON) from ``input_uri`` — a single object or a
        whole directory — invoke the endpoint synchronously, write the prediction
        JSON to ``output_uri``, and return the output location plus counts."""
        instances, source_keys = self._read_instances(input_uri, target_column)
        request_body = json.dumps({"instances": instances}).encode("utf-8")
        if len(request_body) > MAX_PAYLOAD_BYTES:
            # Much likelier now that a directory can be expanded into one batch.
            raise InferenceInvocationError(
                f"prediction request is {len(request_body) // (1024 * 1024)} MB "
                f"({len(instances)} instances from {len(source_keys)} file(s)), "
                f"above the SageMaker {MAX_PAYLOAD_BYTES // (1024 * 1024)} MB limit; "
                f"split the input into smaller batches"
            )
        try:
            response = self._runtime.invoke_endpoint(
                EndpointName=endpoint_name,
                ContentType="application/json",
                Accept="application/json",
                Body=request_body,
            )
        except ClientError as exc:
            raise InferenceInvocationError(
                f"invoke_endpoint failed for {endpoint_name}: {exc}"
            ) from exc

        payload = response["Body"].read()
        try:
            result = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InferenceInvocationError(
                f"endpoint {endpoint_name} returned a non-JSON response"
            ) from exc

        out_bucket, out_key = _split_s3_uri(output_uri)
        # Treat the output location as a prefix/folder unless it already points
        # at a concrete .json file, writing predictions.json underneath it.
        last_segment = out_key.rsplit("/", 1)[-1]
        if not out_key or out_key.endswith("/") or "." not in last_segment:
            out_key = f"{out_key.rstrip('/')}/predictions.json".lstrip("/")
        try:
            self._s3.put_object(
                Bucket=out_bucket,
                Key=out_key,
                Body=json.dumps(result).encode("utf-8"),
                ContentType="application/json",
            )
        except ClientError as exc:
            raise InferenceInvocationError(
                f"could not write predictions to {output_uri}: {exc}"
            ) from exc

        predictions = result.get("predictions") if isinstance(result, dict) else None
        return {
            "output_uri": f"s3://{out_bucket}/{out_key}",
            "prediction_count": len(predictions) if isinstance(predictions, list) else 0,
            "instance_count": len(instances),
            # Which objects were actually read — the only way to tell what a
            # directory input picked up.
            "input_files": source_keys,
        }
