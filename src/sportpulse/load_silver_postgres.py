
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import urlparse  # noqa: F401  (conservé si utilisé ailleurs)

import psycopg

from sportpulse.settings import get_settings
from sportpulse.storage import create_s3_client

logger = logging.getLogger("sportpulse.load_postgres")

SCHEMA = "staging"
MAX_REPORTED_ERRORS = 20

EMPLOYEE_COLUMNS = (
    "employee_id",
    "department",
    "declared_activity",
)

ACTIVITY_COLUMNS = (
    "activity_id",
    "employee_id",
    "activity_type",
    "started_at",
    "duration_min",
    "distance_m",
)

ALLOWED_ACTIVITY_TYPES = frozenset({"Running", "Walking", "Cycling"})
ALLOWED_DECLARED_ACTIVITIES = ALLOWED_ACTIVITY_TYPES | {"Other"}
_DECLARED_SQL_LIST = ", ".join(f"'{t}'" for t in sorted(ALLOWED_DECLARED_ACTIVITIES))
_ACTIVITY_SQL_LIST = ", ".join(f"'{t}'" for t in sorted(ALLOWED_ACTIVITY_TYPES))

_S3_URI_RE = re.compile(r"s3://([^/]+)/(.+)")


class LoadSilverError(RuntimeError):
    """Erreur de validation ou de chargement des données Silver."""


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #
def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Sépare une URI s3://bucket/cle."""
    match = _S3_URI_RE.fullmatch(uri.strip())
    if not match:
        raise LoadSilverError("URI S3 invalide : format s3://bucket/cle attendu.")
    return match.group(1), match.group(2)


def read_s3_object(s3: Any, uri: str, expected_bucket: str) -> bytes:
    """Lit un objet du bucket d'artefacts autorisé."""
    bucket, key = parse_s3_uri(uri)

    if bucket != expected_bucket:
        raise LoadSilverError(
            "Un fichier du manifeste ne provient pas du bucket artefacts configuré."
        )

    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:
        raise LoadSilverError(f"Impossible de lire l'objet S3 '{key}'.") from exc


def read_transform_manifest(
    s3: Any,
    manifest_uri: str,
    expected_bucket: str,
) -> dict[str, Any]:
    """Valide puis lit le manifeste Silver."""
    bucket, key = parse_s3_uri(manifest_uri)

    if bucket != expected_bucket or not key.startswith("transform-manifests/"):
        raise LoadSilverError(
            "Le manifeste doit appartenir à transform-manifests/ "
            "dans le bucket artefacts configuré."
        )

    raw = read_s3_object(s3, manifest_uri, expected_bucket)

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoadSilverError(
            "Le manifeste de transformation n'est pas un JSON valide."
        ) from exc

    if not isinstance(manifest, dict):
        raise LoadSilverError("Le manifeste doit être un objet JSON.")

    if manifest.get("pipeline") != "sportpulse-bronze-to-silver":
        raise LoadSilverError("Le manifeste ne correspond pas au pipeline Silver.")

    if manifest.get("status") not in {"SUCCESS", "SUCCESS_WITH_REJECTS"}:
        raise LoadSilverError("Le manifeste Silver n'indique pas un traitement réussi.")

    if not manifest.get("run_id"):
        raise LoadSilverError("Le manifeste ne contient pas de run_id.")

    return manifest


def read_csv_rows(
    s3: Any,
    uri: str,
    expected_bucket: str,
    expected_columns: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Lit un CSV Silver en contrôlant son schéma.

    Chaque ligne conserve son vrai numéro dans la clé '_line'
    (les lignes vides sont ignorées sans décaler la numérotation).
    """
    content = read_s3_object(s3, uri, expected_bucket)

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LoadSilverError("Un fichier Silver n'est pas encodé en UTF-8.") from exc

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise LoadSilverError("Un fichier Silver ne contient pas d'en-tête.")

    headers = [column.strip() for column in reader.fieldnames]

    if len(headers) != len(set(headers)):
        raise LoadSilverError("Un fichier Silver contient des colonnes dupliquées.")

    if set(headers) != set(expected_columns):
        raise LoadSilverError(
            "Colonnes Silver incorrectes. "
            f"Attendu : {list(expected_columns)} ; reçu : {headers}."
        )

    rows: list[dict[str, Any]] = []

    for line_number, raw_row in enumerate(reader, start=2):
        if None in raw_row:
            raise LoadSilverError(f"CSV Silver mal formé à la ligne {line_number}.")

        row: dict[str, Any] = {
            column: (raw_row.get(column) or "").strip()
            for column in expected_columns
        }

        if not any(row.values()):
            continue  # ligne vide : ignorée, numérotation préservée

        row["_line"] = line_number
        rows.append(row)

    return rows


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def parse_decimal(
    value: str,
    *,
    column: str,
    line_number: int,
    strictly_positive: bool = False,
) -> Decimal:
    """Convertit un nombre et contrôle son domaine."""
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise LoadSilverError(
            f"Valeur numérique invalide pour {column}, ligne {line_number}."
        ) from exc

    if not number.is_finite():
        raise LoadSilverError(
            f"Valeur non finie pour {column}, ligne {line_number}."
        )

    invalid = number <= 0 if strictly_positive else number < 0
    if invalid:
        rule = "strictement positif" if strictly_positive else "positif ou nul"
        raise LoadSilverError(f"{column} doit être {rule}, ligne {line_number}.")

    return number


def parse_timestamp(value: str, line_number: int) -> datetime:
    """Convertit un ISO 8601 avec fuseau en datetime UTC."""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LoadSilverError(
            f"Date invalide pour une activité, ligne {line_number}."
        ) from exc

    if result.tzinfo is None or result.utcoffset() is None:
        raise LoadSilverError(
            f"La date doit comporter un fuseau horaire, ligne {line_number}."
        )

    return result.astimezone(timezone.utc)


def _raise_if_errors(errors: list[str]) -> None:
    """Lève une erreur unique regroupant les problèmes détectés."""
    if not errors:
        return

    shown = errors[:MAX_REPORTED_ERRORS]
    hidden = len(errors) - len(shown)
    message = "\n".join(shown)
    if hidden > 0:
        message += f"\n... et {hidden} autre(s) erreur(s)."
    raise LoadSilverError(message)


def validate_employees(rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Contrôle les employés. Retourne des tuples prêts pour COPY."""
    validated: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for row in rows:
        line = row["_line"]
        employee_id = row["employee_id"]
        department = row["department"]
        activity = row["declared_activity"]

        if not (employee_id and department and activity):
            errors.append(f"Employé incomplet à la ligne {line}.")
            continue

        if employee_id in seen:
            errors.append(f"Identifiant employé dupliqué : {employee_id} (ligne {line}).")
            continue

        if activity not in ALLOWED_DECLARED_ACTIVITIES:
            errors.append(f"Activité déclarée inconnue à la ligne {line}.")
            continue

        seen.add(employee_id)
        validated.append((employee_id, department, activity))

    _raise_if_errors(errors)
    return validated


def validate_activities(
    rows: list[dict[str, Any]],
    employee_ids: set[str],
) -> list[tuple[Any, ...]]:
    """Contrôle les activités. Retourne des tuples prêts pour COPY."""
    validated: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for row in rows:
        line = row["_line"]
        activity_id = row["activity_id"]
        employee_id = row["employee_id"]
        activity_type = row["activity_type"]

        if not (activity_id and employee_id and activity_type):
            errors.append(f"Activité incomplète à la ligne {line}.")
            continue

        if activity_id in seen:
            errors.append(f"Identifiant activité dupliqué : {activity_id} (ligne {line}).")
            continue

        if employee_id not in employee_ids:
            errors.append(f"Employé inconnu pour l'activité {activity_id} (ligne {line}).")
            continue

        if activity_type not in ALLOWED_ACTIVITY_TYPES:
            errors.append(f"Type d'activité inconnu à la ligne {line}.")
            continue

        try:
            started_at = parse_timestamp(row["started_at"], line)
            duration = parse_decimal(
                row["duration_min"],
                column="duration_min",
                line_number=line,
                strictly_positive=True,
            )
            distance = parse_decimal(
                row["distance_m"],
                column="distance_m",
                line_number=line,
            )
        except LoadSilverError as exc:
            errors.append(str(exc))
            continue

        seen.add(activity_id)
        validated.append(
            (activity_id, employee_id, activity_type, started_at, duration, distance)
        )

    _raise_if_errors(errors)
    return validated


# --------------------------------------------------------------------------- #
# PostgreSQL
# --------------------------------------------------------------------------- #
def create_tables(cursor: Any) -> None:
    """Crée le schéma staging et ses tables si nécessaire (idempotent)."""
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.silver_employees (
            employee_id TEXT PRIMARY KEY,
            department TEXT NOT NULL CHECK (BTRIM(department) <> ''),
            declared_activity TEXT NOT NULL
                CHECK (declared_activity IN ({_DECLARED_SQL_LIST})),
            source_run_id TEXT NOT NULL,
            loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.silver_activities (
            activity_id TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL
                REFERENCES {SCHEMA}.silver_employees(employee_id),
            activity_type TEXT NOT NULL
                CHECK (activity_type IN ({_ACTIVITY_SQL_LIST})),
            started_at TIMESTAMPTZ NOT NULL,
            duration_min NUMERIC(14, 6) NOT NULL CHECK (duration_min > 0),
            distance_m NUMERIC(16, 6) NOT NULL CHECK (distance_m >= 0),
            source_run_id TEXT NOT NULL,
            loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_silver_activities_employee
            ON {SCHEMA}.silver_activities(employee_id)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_silver_activities_started_at
            ON {SCHEMA}.silver_activities(started_at)
        """,
    ]

    for statement in statements:
        cursor.execute(statement)


def copy_rows(
    cursor: Any,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[tuple[Any, ...]],
) -> None:
    """Insère en masse via COPY (un flux au lieu d'un INSERT par ligne)."""
    cols = ", ".join(columns)
    with cursor.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def load_silver(manifest_uri: str) -> dict[str, Any]:
    """Charge les datasets Silver de façon atomique."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise LoadSilverError("DATABASE_URL n'est pas définie.")

    cfg = get_settings()
    s3 = create_s3_client(cfg)

    manifest = read_transform_manifest(s3, manifest_uri, cfg.bucket_artifacts)

    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise LoadSilverError("Le manifeste ne contient pas les sorties Silver.")

    activities_info = outputs.get("activities")
    employees_info = outputs.get("employees")

    if not isinstance(activities_info, dict) or not isinstance(employees_info, dict):
        raise LoadSilverError("Le manifeste doit référencer activities et employees.")

    activities_uri = activities_info.get("uri")
    employees_uri = employees_info.get("uri")

    if not isinstance(activities_uri, str) or not isinstance(employees_uri, str):
        raise LoadSilverError("URI des CSV Silver manquante dans le manifeste.")

    # Validation complète AVANT toute connexion à la base.
    employees = validate_employees(
        read_csv_rows(s3, employees_uri, cfg.bucket_artifacts, EMPLOYEE_COLUMNS)
    )
    employee_ids = {row[0] for row in employees}

    activities = validate_activities(
        read_csv_rows(s3, activities_uri, cfg.bucket_artifacts, ACTIVITY_COLUMNS),
        employee_ids,
    )

    run_id = str(manifest["run_id"])

    # `with connect` : COMMIT si tout réussit, ROLLBACK si une exception survient.
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            create_tables(cursor)

            # TRUNCATE est transactionnel dans PostgreSQL (annulé en cas d'échec).
            cursor.execute(
                f"TRUNCATE {SCHEMA}.silver_activities, {SCHEMA}.silver_employees"
            )

            copy_rows(
                cursor,
                f"{SCHEMA}.silver_employees",
                (*EMPLOYEE_COLUMNS, "source_run_id"),
                ((*row, run_id) for row in employees),
            )

            copy_rows(
                cursor,
                f"{SCHEMA}.silver_activities",
                (*ACTIVITY_COLUMNS, "source_run_id"),
                ((*row, run_id) for row in activities),
            )

    return {
        "status": "SUCCESS",
        "run_id": run_id,
        "manifest_uri": manifest_uri,
        "rows": {
            "silver_employees": len(employees),
            "silver_activities": len(activities),
        },
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout reste réservé au JSON lu par le flow Kestra
    )

    parser = argparse.ArgumentParser(
        description="Charge les données détaillées Silver dans PostgreSQL."
    )
    parser.add_argument(
        "--manifest-uri",
        required=True,
        help="URI S3 du manifeste de transformation Silver.",
    )
    args = parser.parse_args()

    try:
        result = load_silver(args.manifest_uri)
    except Exception as exc:
        logger.error("Échec du chargement Silver vers PostgreSQL : %s", exc)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())