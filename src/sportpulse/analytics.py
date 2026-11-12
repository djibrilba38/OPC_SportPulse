"""Calcul des indicateurs Gold à partir d'un manifeste de transformation Silver."""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from sportpulse.settings import Settings, get_settings
from sportpulse.storage import StorageError, create_s3_client


ACTIVITY_COLUMNS = ("activity_id", "employee_id", "activity_type", "started_at", "duration_min", "distance_m")
EMPLOYEE_COLUMNS = ("employee_id", "department", "declared_activity")
ALLOWED_ACTIVITIES = frozenset({"Running", "Walking", "Cycling"})
TWO_DECIMALS = Decimal("0.01")
ZERO = Decimal("0")
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9T._-]+")
S3_URI_PATTERN = re.compile(r"s3://([^/]+)/(.+)")


class AnalyticsError(RuntimeError):
    """Erreur fonctionnelle pendant la création des indicateurs Gold."""


# --------------------------------------------------------------------------- #
# Utilitaires temps / format
# --------------------------------------------------------------------------- #

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _round2(value: Decimal) -> Decimal:
    return value.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return f"{_round2(value):.2f}"


def _safe_div(total: Decimal, count: int) -> Decimal:
    return total / Decimal(count) if count else ZERO


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    match = S3_URI_PATTERN.fullmatch(uri.strip())
    if not match:
        raise AnalyticsError("URI S3 invalide ; format attendu : s3://bucket/cle.")
    return match.group(1), match.group(2)


def _timestamp_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalyticsError("Date Silver invalide : format ISO 8601 attendu.") from exc
    if parsed.utcoffset() is None:
        raise AnalyticsError("La date Silver doit inclure un fuseau horaire.")
    return parsed.astimezone(timezone.utc).date().isoformat()


def _number(value: str, *, field: str, minimum: Decimal) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AnalyticsError(f"Valeur numérique invalide pour '{field}'.") from exc
    if not number.is_finite() or number < minimum:
        raise AnalyticsError(f"Valeur hors limites pour '{field}'.")
    return number


# --------------------------------------------------------------------------- #
# Entrées / sorties S3
# --------------------------------------------------------------------------- #

def _read_object(s3: Any, bucket: str, key: str) -> bytes:
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except (BotoCoreError, ClientError, OSError, KeyError) as exc:
        raise StorageError(f"Impossible de lire l'objet S3 '{key}'. Vérifiez s3:GetObject.") from exc


def _read_json(s3: Any, bucket: str, key: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_object(s3, bucket, key).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalyticsError(f"Le fichier JSON S3 '{key}' est invalide.") from exc
    if not isinstance(value, dict):
        raise AnalyticsError(f"Le fichier JSON S3 '{key}' doit être un objet JSON.")
    return value


def _upload(s3: Any, *, bucket: str, key: str, content: bytes, content_type: str) -> str:
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            ServerSideEncryption="AES256",
            Metadata={"application": "sportpulse-ai", "layer": "gold"},
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        raise StorageError(f"Impossible d'écrire l'artefact S3 '{key}'.") from exc
    return f"s3://{bucket}/{key}"


def _read_csv(content: bytes, *, label: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AnalyticsError(f"Le fichier '{label}' n'est pas encodé en UTF-8.") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise AnalyticsError(f"Le fichier '{label}' ne contient pas d'en-tête.")

    headers = [str(h).strip().lower() for h in reader.fieldnames]
    if len(headers) != len(set(headers)):
        raise AnalyticsError(f"Le fichier '{label}' contient des colonnes dupliquées.")

    missing = sorted(set(required) - set(headers))
    if missing:
        raise AnalyticsError(f"Colonnes manquantes dans '{label}' : {', '.join(missing)}.")

    return [
        {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
        for row in reader
    ]


def _csv_bytes(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# Agrégation
# --------------------------------------------------------------------------- #

@dataclass
class _Bucket:
    """Accumulateur unique réutilisé par tous les agrégats."""
    count: int = 0
    duration: Decimal = ZERO
    distance: Decimal = ZERO
    employees: set[str] = field(default_factory=set)

    def add(self, duration: Decimal, distance: Decimal, employee_id: str) -> None:
        self.count += 1
        self.duration += duration
        self.distance += distance
        self.employees.add(employee_id)


def _aggregate(
    activity_rows: list[dict[str, str]],
    employees: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Passe unique sur les activités : toutes les agrégations sont calculées ensemble."""
    by_employee: dict[str, _Bucket] = {eid: _Bucket() for eid in employees}
    by_department: dict[str, _Bucket] = defaultdict(_Bucket)
    by_type: dict[str, _Bucket] = defaultdict(_Bucket)
    by_day: dict[str, _Bucket] = defaultdict(_Bucket)
    total = _Bucket()
    seen: set[str] = set()

    for row in activity_rows:
        activity_id = row["activity_id"]
        employee_id = row["employee_id"]
        activity_type = row["activity_type"]

        if not activity_id or activity_id in seen:
            raise AnalyticsError("Les identifiants d'activités Silver sont vides ou dupliqués.")
        seen.add(activity_id)

        if employee_id not in employees:
            raise AnalyticsError(f"L'activité '{activity_id}' référence un employé absent du fichier Silver.")
        if activity_type not in ALLOWED_ACTIVITIES:
            raise AnalyticsError(f"Type d'activité Silver non reconnu pour '{activity_id}'.")

        duration = _number(row["duration_min"], field="duration_min", minimum=Decimal("0.0000001"))
        distance = _number(row["distance_m"], field="distance_m", minimum=ZERO)
        day = _timestamp_date(row["started_at"])
        department = employees[employee_id]["department"]

        by_employee[employee_id].add(duration, distance, employee_id)
        by_department[department].add(duration, distance, employee_id)
        by_type[activity_type].add(duration, distance, employee_id)
        by_day[day].add(duration, distance, employee_id)
        total.add(duration, distance, employee_id)

    return {
        "total": total,
        "by_employee": by_employee,
        "by_department": by_department,
        "by_type": by_type,
        "by_day": by_day,
    }


def _metric_fields(b: _Bucket) -> dict[str, str]:
    return {
        "activity_count": b.count,
        "total_duration_min": _fmt(b.duration),
        "total_distance_m": _fmt(b.distance),
        "average_duration_min": _fmt(_safe_div(b.duration, b.count)),
        "average_distance_m": _fmt(_safe_div(b.distance, b.count)),
    }


def _load_employees(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    employees: dict[str, dict[str, str]] = {}
    for row in rows:
        eid, dept = row["employee_id"], row["department"]
        if not eid or not dept:
            raise AnalyticsError("Un employé Silver possède un identifiant ou département vide.")
        if eid in employees:
            raise AnalyticsError("Le fichier Silver contient des identifiants employés dupliqués.")
        employees[eid] = {"department": dept, "declared_activity": row["declared_activity"]}
    return employees


def _build_tables(agg: dict[str, Any], employees: dict[str, dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    by_employee = [
        {"employee_id": eid, "department": employees[eid]["department"], **_metric_fields(b)}
        for eid, b in sorted(agg["by_employee"].items())
    ]

    by_department = []
    for dept in sorted(agg["by_department"]):
        b = agg["by_department"][dept]
        employee_ids = [e for e, v in employees.items() if v["department"] == dept]
        by_department.append({
            "department": dept,
            "employee_count": len(employee_ids),
            "active_employee_count": len(b.employees),
            "activity_count": b.count,
            "total_duration_min": _fmt(b.duration),
            "total_distance_m": _fmt(b.distance),
        })

    by_type = [
        {"activity_type": t, "active_employee_count": len(b.employees), **_metric_fields(b)}
        for t, b in sorted(agg["by_type"].items())
    ]
    by_day = [
        {"activity_date": d, "active_employee_count": len(b.employees), **_metric_fields(b)}
        for d, b in sorted(agg["by_day"].items())
    ]
    return {"by_employee": by_employee, "by_department": by_department,
            "by_activity_type": by_type, "by_day": by_day}


# --------------------------------------------------------------------------- #
# Pipeline principal
# --------------------------------------------------------------------------- #

def _validate_manifest(transform: dict[str, Any], manifest_uri: str, cfg: Settings) -> tuple[str, dict[str, Any]]:
    if transform.get("pipeline") != "sportpulse-bronze-to-silver" or \
       transform.get("status") not in {"SUCCESS", "SUCCESS_WITH_REJECTS"}:
        raise AnalyticsError("Le fichier fourni n'est pas un manifeste Silver réussi de SportPulse.")

    run_id = transform.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise AnalyticsError("Identifiant de transformation Silver invalide.")

    outputs = transform.get("outputs")
    if not isinstance(outputs, dict):
        raise AnalyticsError("Le manifeste Silver ne contient pas de sorties.")
    return run_id, outputs


def _resolve_output(outputs: dict[str, Any], name: str, run_id: str, cfg: Settings) -> str:
    entry = outputs.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("uri"), str):
        raise AnalyticsError(f"Sortie Silver '{name}' absente du manifeste.")
    bucket, key = _parse_s3_uri(entry["uri"])
    if bucket != cfg.bucket_artifacts or not key.startswith(f"silver/runs/{run_id}/"):
        raise AnalyticsError(f"La sortie Silver '{name}' pointe hors du répertoire attendu.")
    return key


def build_gold_from_manifest(
    manifest_uri: str,
    *,
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Lit un manifeste Silver, calcule les agrégats et publie les résultats Gold."""
    cfg = settings or get_settings()
    s3 = client or create_s3_client(cfg)

    bucket, key = _parse_s3_uri(manifest_uri)
    if bucket != cfg.bucket_artifacts or not key.startswith("transform-manifests/"):
        raise AnalyticsError("Le manifeste doit provenir du bucket artefacts, sous transform-manifests/.")

    transform = _read_json(s3, bucket, key)
    source_run_id, outputs = _validate_manifest(transform, manifest_uri, cfg)

    activities = _read_csv(
        _read_object(s3, cfg.bucket_artifacts, _resolve_output(outputs, "activities", source_run_id, cfg)),
        label="activities.csv", required=ACTIVITY_COLUMNS,
    )
    employees = _load_employees(_read_csv(
        _read_object(s3, cfg.bucket_artifacts, _resolve_output(outputs, "employees", source_run_id, cfg)),
        label="employees.csv", required=EMPLOYEE_COLUMNS,
    ))

    agg = _aggregate(activities, employees)
    tables = _build_tables(agg, employees)
    total: _Bucket = agg["total"]

    now = _utc_now()
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    prefix = f"gold/runs/{run_id}"

    metrics = {
        "activity_count": total.count,
        "employee_count": len(employees),
        "active_employee_count": len(total.employees),
        "total_duration_min": float(_fmt(total.duration)),
        "total_distance_m": float(_fmt(total.distance)),
        "average_duration_min": float(_fmt(_safe_div(total.duration, total.count))),
        "average_distance_m": float(_fmt(_safe_div(total.distance, total.count))),
    }

    csv_specs = {
        "by_employee": ("aggregates/by_employee.csv", tables["by_employee"], (
            "employee_id", "department", "activity_count", "total_duration_min",
            "total_distance_m", "average_duration_min", "average_distance_m")),
        "by_department": ("aggregates/by_department.csv", tables["by_department"], (
            "department", "employee_count", "active_employee_count", "activity_count",
            "total_duration_min", "total_distance_m")),
        "by_activity_type": ("aggregates/by_activity_type.csv", tables["by_activity_type"], (
            "activity_type", "active_employee_count", "activity_count", "total_duration_min",
            "total_distance_m", "average_duration_min", "average_distance_m")),
        "by_day": ("aggregates/by_day.csv", tables["by_day"], (
            "activity_date", "active_employee_count", "activity_count", "total_duration_min",
            "total_distance_m", "average_duration_min", "average_distance_m")),
    }

    summary = {
        "schema_version": "1.0",
        "pipeline": "sportpulse-silver-to-gold",
        "run_id": run_id,
        "created_at_utc": _utc_text(now),
        "source_transform_manifest_uri": manifest_uri,
        "source_transform_run_id": source_run_id,
        "status": "SUCCESS",
        "metrics": metrics,
        "by_activity_type": tables["by_activity_type"],
        "by_day": tables["by_day"],
    }

    # Préparation de tous les contenus, puis écriture locale et S3 en une seule boucle.
    artefacts: dict[str, tuple[str, bytes, str]] = {
        "summary": (f"{prefix}/kpis/summary.json",
                    (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                    "application/json"),
    }
    for name, (rel, rows, cols) in csv_specs.items():
        artefacts[name] = (f"{prefix}/{rel}", _csv_bytes(cols, rows), "text/csv")

    local_root = cfg.manifest_dir.parent / "gold" / "runs" / run_id
    local_root.mkdir(parents=True, exist_ok=True)

    output_files: dict[str, dict[str, Any]] = {}
    for name, (art_key, content, ctype) in artefacts.items():
        uri = _upload(s3, bucket=cfg.bucket_artifacts, key=art_key, content=content, content_type=ctype)
        local_path = local_root / (f"{name}.json" if name == "summary" else f"{name}.csv")
        local_path.write_bytes(content)
        output_files[name] = {"uri": uri, "key": art_key, "size_bytes": len(content), "local_path": str(local_path)}

    gold_manifest_key = f"gold-manifests/gold-{run_id}.json"
    gold_manifest_uri = f"s3://{cfg.bucket_artifacts}/{gold_manifest_key}"
    gold_manifest = {
        "schema_version": "1.0",
        "pipeline": "sportpulse-silver-to-gold",
        "run_id": run_id,
        "status": "SUCCESS",
        "created_at_utc": _utc_text(now),
        "source_transform_manifest_uri": manifest_uri,
        "source_transform_run_id": source_run_id,
        "manifest_uri": gold_manifest_uri,
        "metrics": metrics,
        "outputs": output_files,
    }
    manifest_bytes = (json.dumps(gold_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _upload(s3, bucket=cfg.bucket_artifacts, key=gold_manifest_key,
            content=manifest_bytes, content_type="application/json")
    (local_root / "gold-manifest.json").write_bytes(manifest_bytes)

    return {
        "status": "SUCCESS",
        "pipeline": "sportpulse-silver-to-gold",
        "run_id": run_id,
        "source_transform_manifest_uri": manifest_uri,
        "gold_manifest_uri": gold_manifest_uri,
        "summary_uri": output_files["summary"]["uri"],
        "metrics": metrics,
        "outputs": output_files,
        "local_output_dir": str(local_root),
    }