"""Contrôles qualité et transformation des sources S3 (Bronze) vers Silver."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from sportpulse.settings import Settings, get_settings
from sportpulse.storage import StorageError, create_s3_client


# --------------------------------------------------------------------------- #
# Constantes et schémas
# --------------------------------------------------------------------------- #

ACTIVITY_COLUMNS: tuple[str, ...] = (
    "activity_id", "employee_id", "activity_type",
    "started_at", "duration_min", "distance_m",
)
EMPLOYEE_COLUMNS: tuple[str, ...] = ("employee_id", "department", "declared_activity")

ALLOWED_ACTIVITIES: dict[str, str] = {
    "running": "Running",
    "walking": "Walking",
    "cycling": "Cycling",
}

MIN_DURATION = Decimal("0.0000001")
MIN_DISTANCE = Decimal("0")

S3_URI_RE = re.compile(r"s3://([^/]+)/(.+)")
RUN_ID_RE = re.compile(r"[A-Za-z0-9T._-]+")

CHECKS: tuple[str, ...] = (
    "required_columns",
    "non_empty_identifiers",
    "unique_identifiers",
    "activity_type_domain",
    "timezone_aware_iso8601_timestamp",
    "positive_duration",
    "non_negative_distance",
    "employee_foreign_key",
    "sha256_source_integrity",
)


class TransformError(RuntimeError):
    """Erreur fonctionnelle pendant la préparation Silver."""


@dataclass
class Rejection:
    source_file: str
    line_number: int | None
    issues: list[str]
    row: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "line_number": self.line_number,
            "issues": self.issues,
            "row": self.row,
        }


@dataclass
class DatasetResult:
    rows: list[dict[str, str]] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    input_rows: int = 0


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    match = S3_URI_RE.fullmatch(uri.strip())
    if not match:
        raise TransformError("URI S3 invalide ; format attendu : s3://bucket/cle.")
    return match.group(1), match.group(2)


def _activity_name(value: str) -> str | None:
    return ALLOWED_ACTIVITIES.get(value.strip().casefold()) if value else None


def _decimal_string(value: str, *, field: str, minimum: Decimal) -> str:
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{field}_not_numeric") from exc

    if not number.is_finite():
        raise ValueError(f"{field}_not_finite")
    if number < minimum:
        raise ValueError(f"{field}_below_minimum")

    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _timestamp_utc(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("started_at_missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("started_at_invalid_iso8601") from exc
    if parsed.utcoffset() is None:
        raise ValueError("started_at_timezone_missing")
    return _utc_text(parsed.astimezone(timezone.utc).replace(microsecond=0))


# --------------------------------------------------------------------------- #
# Lecture S3 et CSV
# --------------------------------------------------------------------------- #

def _s3_read(s3: Any, bucket: str, key: str, *, label: str) -> bytes:
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except (BotoCoreError, ClientError, OSError, KeyError) as exc:
        raise StorageError(f"Impossible de lire '{label}'. Vérifiez s3:GetObject.") from exc


def _s3_write(s3: Any, bucket: str, key: str, content: bytes, content_type: str) -> str:
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            ServerSideEncryption="AES256",
            Metadata={"application": "sportpulse-ai", "layer": "silver"},
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        raise StorageError(f"Impossible d'écrire l'artefact S3 '{key}'.") from exc
    return f"s3://{bucket}/{key}"


def _parse_csv(content: bytes, filename: str) -> tuple[set[str], list[dict[str, str]]]:
    """Retourne (ensemble des colonnes en minuscules, lignes normalisées)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TransformError(f"Le fichier '{filename}' n'est pas encodé en UTF-8.") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise TransformError(f"Le fichier '{filename}' est vide ou sans en-tête CSV.")

    headers = [(name or "").strip() for name in reader.fieldnames]
    if any(not h for h in headers):
        raise TransformError(f"Le fichier '{filename}' contient un nom de colonne vide.")

    lowered = [h.lower() for h in headers]
    if len(lowered) != len(set(lowered)):
        raise TransformError(f"Le fichier '{filename}' contient des colonnes dupliquées.")

    rows = [
        {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
        for row in reader
    ]
    return set(lowered), rows


def _csv_bytes(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _rejections_csv(rejections: list[Rejection]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(
        buf,
        fieldnames=["source_file", "line_number", "issues", "row_json"],
        lineterminator="\n",
    )
    writer.writeheader()
    for r in rejections:
        writer.writerow({
            "source_file": r.source_file,
            "line_number": r.line_number,
            "issues": "|".join(r.issues),
            "row_json": json.dumps(r.row, ensure_ascii=False, sort_keys=True),
        })
    return buf.getvalue().encode("utf-8")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# Validation ligne à ligne
# --------------------------------------------------------------------------- #

def _validate_activity(row: dict[str, str], seen: set[str]) -> tuple[dict[str, str] | None, list[str]]:
    issues: list[str] = []
    item_id = row.get("activity_id", "")
    employee_id = row.get("employee_id", "")
    activity_type = _activity_name(row.get("activity_type", ""))

    if not item_id:
        issues.append("activity_id_missing")
    elif item_id in seen:
        issues.append("activity_id_duplicate")
    else:
        seen.add(item_id)

    if not employee_id:
        issues.append("employee_id_missing")
    if not activity_type:
        issues.append("activity_type_unsupported")

    started_at = duration = distance = ""
    try:
        started_at = _timestamp_utc(row.get("started_at", ""))
    except ValueError as exc:
        issues.append(str(exc))
    try:
        duration = _decimal_string(row.get("duration_min", ""), field="duration_min", minimum=MIN_DURATION)
    except ValueError as exc:
        issues.append(str(exc))
    try:
        distance = _decimal_string(row.get("distance_m", ""), field="distance_m", minimum=MIN_DISTANCE)
    except ValueError as exc:
        issues.append(str(exc))

    if issues:
        return None, issues
    return {
        "activity_id": item_id,
        "employee_id": employee_id,
        "activity_type": activity_type,
        "started_at": started_at,
        "duration_min": duration,
        "distance_m": distance,
    }, []


def _validate_employee(row: dict[str, str], seen: set[str]) -> tuple[dict[str, str] | None, list[str]]:
    issues: list[str] = []
    employee_id = row.get("employee_id", "")
    department = row.get("department", "")
    activity = "Other" if row.get("declared_activity", "").strip().casefold() == "other" else _activity_name(row.get("declared_activity", ""))

    if not employee_id:
        issues.append("employee_id_missing")
    elif employee_id in seen:
        issues.append("employee_id_duplicate")
    else:
        seen.add(employee_id)

    if not department:
        issues.append("department_missing")
    if not activity:
        issues.append("declared_activity_unsupported")

    if issues:
        return None, issues
    return {
        "employee_id": employee_id,
        "department": department,
        "declared_activity": activity,
    }, []


def _process_dataset(
    rows: list[dict[str, str]],
    filename: str,
    validator: Callable[[dict[str, str], set[str]], tuple[dict[str, str] | None, list[str]]],
) -> DatasetResult:
    result = DatasetResult(input_rows=len(rows))
    seen: set[str] = set()
    for line_number, row in enumerate(rows, start=2):
        valid, issues = validator(row, seen)
        if issues:
            result.rejected.append(Rejection(filename, line_number, issues, row))
        else:
            result.rows.append(valid)  # type: ignore[arg-type]
    return result


# --------------------------------------------------------------------------- #
# Sources d'ingestion
# --------------------------------------------------------------------------- #

def _load_sources(manifest: dict[str, Any], cfg: Settings, s3: Any) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    objects = manifest.get("objects")
    if not isinstance(objects, list) or not objects:
        raise TransformError("Le manifeste d'ingestion ne contient aucun objet source.")

    descriptors: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}

    for item in objects:
        if not isinstance(item, dict):
            raise TransformError("Entrée objet invalide dans le manifeste d'ingestion.")

        bucket, key = item.get("bucket"), item.get("key")
        filename = str(item.get("filename") or key or "source-inconnue")

        if bucket != cfg.bucket_raw or not isinstance(key, str) or not key.startswith("source_files/"):
            raise TransformError("Le manifeste référence un objet hors du bucket/prefix source autorisé.")

        content = _s3_read(s3, bucket, key, label=f"source S3 '{filename}'")
        actual_hash = hashlib.sha256(content).hexdigest()

        expected_hash = item.get("sha256")
        if expected_hash and expected_hash != actual_hash:
            raise TransformError(f"L'empreinte SHA-256 de '{filename}' ne correspond pas au manifeste.")

        descriptors.append({
            "filename": filename,
            "bucket": bucket,
            "key": key,
            "uri": f"s3://{bucket}/{key}",
            "sha256": actual_hash,
            "size_bytes": len(content),
        })
        contents[key] = content

    return descriptors, contents


def _classify_sources(
    descriptors: list[dict[str, Any]],
    contents: dict[str, bytes],
) -> tuple[DatasetResult | None, DatasetResult | None, list[str], list[str], Counter[str]]:
    """Identifie les CSV activités / employés et ignore le reste."""
    activities: DatasetResult | None = None
    employees: DatasetResult | None = None
    recognized: list[str] = []
    ignored: list[str] = []
    counts: Counter[str] = Counter()

    for d in descriptors:
        filename, key = d["filename"], d["key"]
        if Path(filename).suffix.lower() != ".csv":
            ignored.append(filename)
            continue

        headers, rows = _parse_csv(contents[key], filename)

        if set(ACTIVITY_COLUMNS).issubset(headers):
            if activities is not None:
                raise TransformError("Plusieurs CSV correspondent au schéma activités ; un seul est attendu.")
            activities = _process_dataset(rows, filename, _validate_activity)
            counts["activities"] = activities.input_rows
            recognized.append(filename)

        elif set(EMPLOYEE_COLUMNS).issubset(headers):
            if employees is not None:
                raise TransformError("Plusieurs CSV correspondent au schéma employés ; un seul est attendu.")
            employees = _process_dataset(rows, filename, _validate_employee)
            counts["employees"] = employees.input_rows
            recognized.append(filename)

        else:
            ignored.append(filename)

    return activities, employees, recognized, ignored, counts


def _require_datasets(activities: DatasetResult | None, employees: DatasetResult | None) -> None:
    missing: list[str] = []
    if activities is None:
        missing.append("activités (" + ", ".join(ACTIVITY_COLUMNS) + ")")
    if employees is None:
        missing.append("employés (" + ", ".join(EMPLOYEE_COLUMNS) + ")")
    if missing:
        raise TransformError("CSV requis introuvable(s) dans le manifeste : " + "; ".join(missing) + ".")


def _apply_foreign_key(activities: DatasetResult, employees: DatasetResult) -> None:
    """Clé étrangère : chaque activité doit référencer un employé connu."""
    valid_ids = {row["employee_id"] for row in employees.rows}
    kept: list[dict[str, str]] = []
    for row in activities.rows:
        if row["employee_id"] in valid_ids:
            kept.append(row)
        else:
            activities.rejected.append(
                Rejection("cross_dataset_validation", None, ["employee_id_not_found"], row)
            )
    activities.rows = kept


# --------------------------------------------------------------------------- #
# Publication
# --------------------------------------------------------------------------- #

def _publish_outputs(
    s3: Any,
    cfg: Settings,
    prefix: str,
    local_root: Path,
    activities: DatasetResult,
    employees: DatasetResult,
) -> dict[str, dict[str, Any]]:
    payloads = {
        "activities": (f"{prefix}/activities.csv",
                       _csv_bytes(ACTIVITY_COLUMNS, activities.rows)),
        "employees": (f"{prefix}/employees.csv",
                      _csv_bytes(EMPLOYEE_COLUMNS, employees.rows)),
        "rejected_activities": (f"{prefix}/rejected/activities.csv",
                                _rejections_csv(activities.rejected)),
        "rejected_employees": (f"{prefix}/rejected/employees.csv",
                               _rejections_csv(employees.rejected)),
    }

    outputs: dict[str, dict[str, Any]] = {}
    for name, (key, content) in payloads.items():
        uri = _s3_write(s3, cfg.bucket_artifacts, key, content, "text/csv")
        local_path = local_root / key.removeprefix(f"{prefix}/")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        outputs[name] = {"uri": uri, "key": key, "size_bytes": len(content), "local_path": str(local_path)}
    return outputs


# --------------------------------------------------------------------------- #
# Pipeline principal
# --------------------------------------------------------------------------- #

def transform_from_manifest(
    manifest_uri: str,
    *,
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Lit un manifeste d'ingestion, applique les contrôles et publie les sorties Silver."""
    cfg = settings or get_settings()
    s3 = client or create_s3_client(cfg)
    started = _utc_now()

    bucket, key = _parse_s3_uri(manifest_uri)
    if bucket != cfg.bucket_artifacts or not key.startswith("ingestion-manifests/"):
        raise TransformError("Le manifeste doit venir du bucket artefacts, sous ingestion-manifests/.")

    try:
        manifest = json.loads(_s3_read(s3, bucket, key, label="manifeste S3").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError("Le manifeste S3 n'est pas un JSON valide.") from exc

    if (
        not isinstance(manifest, dict)
        or manifest.get("pipeline") != "sportpulse-source-ingestion"
        or manifest.get("status") != "SUCCESS"
    ):
        raise TransformError("Le manifeste n'est pas un manifeste d'ingestion SportPulse au statut SUCCESS.")

    descriptors, contents = _load_sources(manifest, cfg, s3)
    activities, employees, recognized, ignored, counts = _classify_sources(descriptors, contents)
    _require_datasets(activities, employees)
    assert activities is not None and employees is not None  # garanti par _require_datasets

    _apply_foreign_key(activities, employees)

    run_id = f"{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    prefix = f"silver/runs/{run_id}"
    local_root = cfg.manifest_dir.parent / "silver" / run_id
    local_root.mkdir(parents=True, exist_ok=True)

    output_files = _publish_outputs(s3, cfg, prefix, local_root, activities, employees)

    all_rejections = activities.rejected + employees.rejected
    issue_counts = Counter(issue for r in all_rejections for issue in r.issues)
    total_input = counts["activities"] + counts["employees"]
    total_accepted = len(activities.rows) + len(employees.rows)
    total_rejected = len(all_rejections)
    status = "SUCCESS" if total_rejected == 0 else "SUCCESS_WITH_REJECTS"

    datasets = {
        "activities": {
            "input_rows": activities.input_rows,
            "accepted_rows": len(activities.rows),
            "rejected_rows": len(activities.rejected),
        },
        "employees": {
            "input_rows": employees.input_rows,
            "accepted_rows": len(employees.rows),
            "rejected_rows": len(employees.rejected),
        },
    }
    issues_sorted = dict(sorted(issue_counts.items()))

    quality_report = {
        "schema_version": "1.0",
        "pipeline": "sportpulse-bronze-to-silver",
        "run_id": run_id,
        "created_at_utc": _utc_text(started),
        "source_manifest_uri": manifest_uri,
        "status": status,
        "datasets": datasets,
        "total_input_rows": total_input,
        "total_accepted_rows": total_accepted,
        "total_rejected_rows": total_rejected,
        "issue_counts": issues_sorted,
        "recognized_source_files": recognized,
        "ignored_source_files": ignored,
        "checks": list(CHECKS),
    }
    quality_key = f"quality-reports/quality-{run_id}.json"
    quality_bytes = _json_bytes(quality_report)
    quality_uri = _s3_write(s3, cfg.bucket_artifacts, quality_key, quality_bytes, "application/json")
    (local_root / "quality-report.json").write_bytes(quality_bytes)

    finished = _utc_now()
    manifest_key = f"transform-manifests/transform-{run_id}.json"
    manifest_uri_out = f"s3://{cfg.bucket_artifacts}/{manifest_key}"
    transform_manifest = {
        "schema_version": "1.0",
        "pipeline": "sportpulse-bronze-to-silver",
        "run_id": run_id,
        "status": status,
        "started_at_utc": _utc_text(started),
        "finished_at_utc": _utc_text(finished),
        "source_manifest_uri": manifest_uri,
        "source_objects": descriptors,
        "outputs": {
            name: {"uri": v["uri"], "size_bytes": v["size_bytes"]}
            for name, v in output_files.items()
        },
        "quality_report_uri": quality_uri,
        "quality_summary": {
            "total_input_rows": total_input,
            "total_accepted_rows": total_accepted,
            "total_rejected_rows": total_rejected,
            "issue_counts": issues_sorted,
        },
        "manifest_uri": manifest_uri_out,
    }
    manifest_bytes = _json_bytes(transform_manifest)
    _s3_write(s3, cfg.bucket_artifacts, manifest_key, manifest_bytes, "application/json")
    (local_root / "transform-manifest.json").write_bytes(manifest_bytes)

    return {
        "status": status,
        "pipeline": "sportpulse-bronze-to-silver",
        "run_id": run_id,
        "source_manifest_uri": manifest_uri,
        "quality_report_uri": quality_uri,
        "transform_manifest_uri": manifest_uri_out,
        "datasets": datasets,
        "total_input_rows": total_input,
        "total_accepted_rows": total_accepted,
        "total_rejected_rows": total_rejected,
        "issue_counts": issues_sorted,
        "outputs": output_files,
        "local_output_dir": str(local_root),
        "recognized_source_files": recognized,
        "ignored_source_files": ignored,
    }