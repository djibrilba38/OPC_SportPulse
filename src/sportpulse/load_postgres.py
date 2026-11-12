from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg import sql

from sportpulse.settings import get_settings
from sportpulse.storage import create_s3_client


SCHEMA = "analytics"

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "by_employee": (
        "employee_id",
        "department",
        "activity_count",
        "total_duration_min",
        "total_distance_m",
        "average_duration_min",
        "average_distance_m",
    ),
    "by_department": (
        "department",
        "employee_count",
        "active_employee_count",
        "activity_count",
        "total_duration_min",
        "total_distance_m",
    ),
    "by_activity_type": (
        "activity_type",
        "active_employee_count",
        "activity_count",
        "total_duration_min",
        "total_distance_m",
        "average_duration_min",
        "average_distance_m",
    ),
    "by_day": (
        "activity_date",
        "active_employee_count",
        "activity_count",
        "total_duration_min",
        "total_distance_m",
        "average_duration_min",
        "average_distance_m",
    ),
}

INTEGER_COLUMNS = {
    "activity_count",
    "employee_count",
    "active_employee_count",
}

DECIMAL_COLUMNS = {
    "total_duration_min",
    "total_distance_m",
    "average_duration_min",
    "average_distance_m",
}

S3_URI_PATTERN = re.compile(r"^s3://([^/]+)/(.+)$")


class LoadError(RuntimeError):
    """Erreur de validation ou de chargement des agrégats Gold."""


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Sépare une URI s3://bucket/key."""
    match = S3_URI_PATTERN.fullmatch(uri.strip())
    if not match:
        raise LoadError("URI S3 invalide : format s3://bucket/cle attendu.")
    return match.group(1), match.group(2)


def read_s3_object(
    s3: Any,
    bucket: str,
    key: str,
) -> bytes:
    """Lit un objet S3."""
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:
        raise LoadError(
            f"Lecture S3 impossible pour la clé '{key}'."
        ) from exc


def read_manifest(
    s3: Any,
    manifest_uri: str,
) -> dict[str, Any]:
    bucket, key = parse_s3_uri(manifest_uri)
    try:
        manifest = json.loads(
            read_s3_object(s3, bucket, key).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoadError("Le manifeste Gold n'est pas un JSON valide.") from exc

    if not isinstance(manifest, dict):
        raise LoadError("Le manifeste Gold doit être un objet JSON.")

    return manifest


def find_artifact_uris(value: Any) -> dict[str, str]:
    """Repère les URI S3 des quatre CSV, même imbriquées dans le manifeste."""
    found: dict[str, str] = {}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and item.startswith("s3://"):
            try:
                _, key = parse_s3_uri(item)
            except LoadError:
                return

            for table in TABLE_COLUMNS:
                expected_suffix = f"/aggregates/{table}.csv"
                if key.endswith(expected_suffix):
                    if table in found and found[table] != item:
                        raise LoadError(
                            f"Plusieurs fichiers Gold trouvés pour '{table}'."
                        )
                    found[table] = item

    visit(value)

    missing = sorted(set(TABLE_COLUMNS) - set(found))
    if missing:
        raise LoadError(
            "Le manifeste ne référence pas tous les agrégats attendus : "
            + ", ".join(missing)
        )

    return found


def read_csv_rows(
    s3: Any,
    uri: str,
    table: str,
) -> list[dict[str, Any]]:
    """Lit un CSV et vérifie ses colonnes avant tout chargement."""
    bucket, key = parse_s3_uri(uri)
    content = read_s3_object(s3, bucket, key)

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LoadError(f"Le CSV '{table}' n'est pas encodé en UTF-8.") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise LoadError(f"Le CSV '{table}' ne possède pas d'en-tête.")

    headers = [header.strip() for header in reader.fieldnames]
    if len(headers) != len(set(headers)):
        raise LoadError(f"Le CSV '{table}' contient des colonnes dupliquées.")

    expected = set(TABLE_COLUMNS[table])
    actual = set(headers)

    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append("manquantes : " + ", ".join(missing))
        if unexpected:
            details.append("inattendues : " + ", ".join(unexpected))
        raise LoadError(
            f"Colonnes incorrectes dans '{table}' : {'; '.join(details)}."
        )

    rows: list[dict[str, Any]] = []

    for line_number, raw_row in enumerate(reader, start=2):
        row: dict[str, Any] = {}

        for column in TABLE_COLUMNS[table]:
            value = (raw_row.get(column) or "").strip()

            if column in {"employee_id", "department", "activity_type"}:
                if not value:
                    raise LoadError(
                        f"{table}, ligne {line_number} : "
                        f"'{column}' ne peut pas être vide."
                    )
                row[column] = value

            elif column == "activity_date":
                try:
                    row[column] = date.fromisoformat(value)
                except ValueError as exc:
                    raise LoadError(
                        f"{table}, ligne {line_number} : date invalide."
                    ) from exc

            elif column in INTEGER_COLUMNS:
                try:
                    number = int(value)
                except ValueError as exc:
                    raise LoadError(
                        f"{table}, ligne {line_number} : "
                        f"entier invalide pour '{column}'."
                    ) from exc
                if number < 0:
                    raise LoadError(
                        f"{table}, ligne {line_number} : "
                        f"'{column}' doit être positif ou nul."
                    )
                row[column] = number

            elif column in DECIMAL_COLUMNS:
                try:
                    number = Decimal(value)
                except (InvalidOperation, ValueError) as exc:
                    raise LoadError(
                        f"{table}, ligne {line_number} : "
                        f"nombre invalide pour '{column}'."
                    ) from exc
                if not number.is_finite() or number < 0:
                    raise LoadError(
                        f"{table}, ligne {line_number} : "
                        f"'{column}' doit être fini et positif ou nul."
                    )
                row[column] = number

        rows.append(row)

    # Les clés primaires doivent être uniques dans chaque fichier.
    primary_key = {
        "by_employee": "employee_id",
        "by_department": "department",
        "by_activity_type": "activity_type",
        "by_day": "activity_date",
    }[table]

    values = [row[primary_key] for row in rows]
    if len(values) != len(set(values)):
        raise LoadError(
            f"Le CSV '{table}' contient des clés primaires dupliquées."
        )

    if table == "by_activity_type":
        allowed = {"Running", "Walking", "Cycling"}
        invalid = {row["activity_type"] for row in rows} - allowed
        if invalid:
            raise LoadError(
                "Types d'activité inconnus : " + ", ".join(sorted(invalid))
            )

    return rows


def load_table(
    conn: psycopg.Connection,
    table: str,
    rows: list[dict[str, Any]],
) -> int:
    """Insère les lignes d'une table. L'appelant gère la transaction."""
    columns = TABLE_COLUMNS[table]

    insert_query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(SCHEMA),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )

    with conn.cursor() as cursor:
        for row in rows:
            cursor.execute(insert_query, tuple(row[column] for column in columns))

    return len(rows)


def load_gold(manifest_uri: str) -> dict[str, Any]:
    """Charge les quatre agrégats et journalise le run."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise LoadError("La variable DATABASE_URL n'est pas définie.")

    cfg = get_settings()
    s3 = create_s3_client(cfg)

    manifest = read_manifest(s3, manifest_uri)

    if manifest.get("pipeline") != "sportpulse-silver-to-gold":
        raise LoadError(
            "Le manifeste fourni ne correspond pas au pipeline Silver-to-Gold."
        )

    if manifest.get("status") not in {"SUCCESS", "SUCCESS_WITH_REJECTS"}:
        raise LoadError("Le manifeste Gold n'indique pas un run réussi.")

    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id:
        raise LoadError("Le manifeste Gold ne contient pas de run_id.")

    artifacts = find_artifact_uris(manifest)
    tables = {
        table: read_csv_rows(s3, uri, table)
        for table, uri in artifacts.items()
    }

    loaded_at = datetime.now(timezone.utc)
    details = {
        "rows": {table: len(rows) for table, rows in tables.items()},
        "manifest_uri": manifest_uri,
    }

    # La suppression, les insertions et le journal SUCCESS sont atomiques.
    # Si une instruction échoue, PostgreSQL annule cette transaction.
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            with conn.cursor() as cursor:
                for table in TABLE_COLUMNS:
                    cursor.execute(
                        sql.SQL("DELETE FROM {}.{}").format(
                            sql.Identifier(SCHEMA),
                            sql.Identifier(table),
                        )
                    )

            for table, rows in tables.items():
                load_table(conn, table, rows)

            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO analytics.pipeline_runs (
                        run_id,
                        pipeline,
                        status,
                        started_at,
                        finished_at,
                        source_manifest_uri,
                        output_manifest_uri,
                        details
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (run_id) DO UPDATE SET
                        pipeline = EXCLUDED.pipeline,
                        status = EXCLUDED.status,
                        finished_at = EXCLUDED.finished_at,
                        source_manifest_uri = EXCLUDED.source_manifest_uri,
                        output_manifest_uri = EXCLUDED.output_manifest_uri,
                        details = EXCLUDED.details
                    """,
                    (
                        run_id,
                        "gold-to-postgres",
                        "SUCCESS",
                        loaded_at,
                        datetime.now(timezone.utc),
                        manifest_uri,
                        manifest_uri,
                        json.dumps(details),
                    ),
                )

    return {
        "status": "SUCCESS",
        "run_id": run_id,
        "manifest_uri": manifest_uri,
        "rows": details["rows"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Charge les agrégats Gold S3 dans PostgreSQL."
    )
    parser.add_argument(
        "--manifest-uri",
        required=True,
        help="URI S3 du manifeste Gold à charger.",
    )
    args = parser.parse_args()

    try:
        result = load_gold(args.manifest_uri)
    except Exception as exc:
        print(f"ÉCHEC du chargement Gold vers PostgreSQL : {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
