"""Charge les vrais classeurs RH/sport du POC, sans fabriquer des primes acquises.

Inspections sans accès DB; import transactionnel, versionné et idempotent dans PostgreSQL.
Aucune identité civile, adresse ou date de naissance n'est conservée dans le datamart.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class RHImportError(ValueError):
    pass


def canonical(label: Any) -> str:
    text = unicodedata.normalize("NFKD", str(label or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def employee_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RHImportError(f"Source introuvable : {path}")
    if path.suffix.casefold() == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RHImportError("La dépendance openpyxl manque dans l'image Docker.") from exc
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = wb.active
            iterator = sheet.iter_rows(values_only=True)
            try:
                headers = [str(c or "").strip() for c in next(iterator)]
            except StopIteration:
                raise RHImportError(f"Classeur vide : {path.name}") from None
            return [{k: v for k, v in zip(headers, line)} for line in iterator if any(v is not None and str(v).strip() for v in line)]
        finally:
            wb.close()
    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            return list(csv.DictReader(handle, dialect=dialect))
    raise RHImportError("Format accepté : .xlsx ou .csv")


def field(row: dict[str, Any], *aliases: str, required: bool = False) -> Any:
    index = {canonical(k): v for k, v in row.items()}
    for alias in aliases:
        value = index.get(canonical(alias))
        if value is not None and str(value).strip():
            return value
    if required:
        raise RHImportError(f"Colonne ou valeur requise manquante : {aliases[0]}")
    return None


def positive_salary(value: Any) -> Decimal:
    raw = str(value).replace("\u00a0", "").replace(" ", "").replace("€", "").strip()
    # Accepte 2 500,50 / 2500.50 / 2,500.50 ; évite toute conversion implicite ambiguë.
    if raw.count(",") == 1 and "." not in raw:
        raw = raw.replace(",", ".")
    elif "," in raw and "." in raw:
        raw = raw.replace(",", "")
    try:
        n = Decimal(raw)
    except (InvalidOperation, TypeError) as exc:
        raise RHImportError("Salaire brut invalide") from exc
    if not n.is_finite() or n <= 0 or n > 10_000_000:
        raise RHImportError("Salaire brut non positif ou hors plage")
    return n.quantize(Decimal("0.01"))


def commute_category(value: Any) -> str:
    mode = canonical(value)
    if not mode:
        return "review"
    # Le mode déclaré ne prouve ni la majorité du trajet sportif ni sa fréquence.
    if any(x in mode for x in ("voiture", "automobile", "bus", "metro", "train", "tram", "moto", "transport en commun", "taxi")):
        return "non_sport"
    if any(x in mode for x in ("marche", "running", "course", "velo", "cycl", "trottinette", "scooter", "skate")):
        return "sport"
    return "review"  # « Autres » ne suffit pas à conclure.


def prepare(rh_rows: list[dict[str, Any]], sport_rows: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sport_by_id: dict[str, str] = {}
    for index, row in enumerate(sport_rows or [], 2):
        eid = employee_id(field(row, "ID salarié", "employee_id", "id salarie", required=True))
        if not eid or eid in sport_by_id:
            raise RHImportError(f"ID sportif manquant ou dupliqué à la ligne {index}")
        sport_by_id[eid] = str(field(row, "Pratique d'un sport", "sport_type_declared", "declared_activity") or "").strip()

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rh_rows, 2):
        try:
            eid = employee_id(field(row, "ID salarié", "employee_id", "id salarie", required=True))
            salary = positive_salary(field(row, "Salaire brut", "salaire brut annuel", "annual_gross_salary_eur", "annual_gross_salary", required=True))
            department = str(field(row, "BU", "department", "business_unit", "departement") or "Non renseigné").strip()
            mode = str(field(row, "Moyen de déplacement", "declared_commute_mode", "mode de transport") or "Non renseigné").strip()
        except RHImportError as exc:
            raise RHImportError(f"Ligne RH {index} : {exc}") from exc
        if not eid or eid in seen:
            raise RHImportError(f"ID salarié vide ou dupliqué ligne RH {index}")
        seen.add(eid)
        category = commute_category(mode)
        normalized.append({
            "employee_id": eid, "department": department, "annual_gross_salary": str(salary),
            "commute_mode": mode, "commute_class": category,
            "declared_sport": sport_by_id.get(eid) or None,
            "sporting_commute_candidate": category == "sport",
        })
    if not normalized:
        raise RHImportError("Aucun salarié à importer")
    extra = len(set(sport_by_id) - seen)
    return normalized, {"employees": len(normalized), "sport_rows_unmatched": extra,
                        "declared_sport_commutes": sum(int(x["sporting_commute_candidate"]) for x in normalized),
                        "unknown_commute_modes": sum(x["commute_class"] == "review" for x in normalized)}


def snapshot_fingerprint(rows: list[dict[str, Any]], year: int) -> str:
    content = json.dumps({"year": year, "rows": sorted(rows, key=lambda x: x["employee_id"])},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_database(rows: list[dict[str, Any]], year: int, digest: str, source_label: str) -> tuple[str, bool]:
    try:
        import psycopg
    except ImportError as exc:
        raise RHImportError("psycopg est requis dans l'image Docker") from exc
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise RHImportError("DATABASE_URL manque dans le conteneur app")
    batch_id = f"rh-{year}-{digest[:18]}"
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (year,))
                cur.execute("SELECT to_regclass('rh.import_batches'), to_regclass('benefits.employee_compensation')")
                schema, compensation = cur.fetchone()
                if not schema or not compensation:
                    raise RHImportError("Appliquer sql/step10_rh_import.sql et sql/benefits_schema.sql avant l'import")
                cur.execute("SELECT EXISTS(SELECT 1 FROM rh.import_batches WHERE import_id=%s)", (batch_id,))
                exists = bool(cur.fetchone()[0])
                if not exists:
                    cur.execute("""INSERT INTO rh.import_batches(import_id,reference_year,source_hash,source_label,employee_count,active)
                                   VALUES(%s,%s,%s,%s,%s,FALSE)""",
                                (batch_id, year, digest, source_label, len(rows)))
                    for row in rows:
                        cur.execute("""INSERT INTO rh.employee_versions
                          (import_id,employee_id,department,annual_gross_salary,commute_mode,commute_class,declared_sport,sporting_commute_candidate)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (batch_id, row["employee_id"], row["department"], row["annual_gross_salary"],
                           row["commute_mode"], row["commute_class"], row["declared_sport"], row["sporting_commute_candidate"]))
                cur.execute("UPDATE rh.import_batches SET active=FALSE WHERE reference_year=%s AND active", (year,))
                cur.execute("UPDATE rh.import_batches SET active=TRUE WHERE import_id=%s", (batch_id,))
                for row in rows:
                    cur.execute("""
                      INSERT INTO benefits.employee_compensation(employee_id,reference_year,annual_gross_salary,currency,salary_validated)
                      VALUES (%s,%s,%s,'EUR',FALSE)
                      ON CONFLICT(employee_id,reference_year) DO UPDATE SET
                        annual_gross_salary=EXCLUDED.annual_gross_salary,
                        salary_validated=CASE
                          WHEN benefits.employee_compensation.annual_gross_salary = EXCLUDED.annual_gross_salary
                          THEN benefits.employee_compensation.salary_validated
                          ELSE FALSE END,
                        updated_at=NOW()
                    """, (row["employee_id"], year, row["annual_gross_salary"]))
    except RHImportError:
        raise
    except Exception as exc:
        raise RHImportError(f"Import PostgreSQL échoué : {type(exc).__name__} (voir journaux privés)") from exc
    return batch_id, not exists


def main() -> int:
    ap = argparse.ArgumentParser(description="Import RH fourni vers PostgreSQL, traçable et non payable")
    ap.add_argument("--rh", type=Path, required=True, help="Fichier RH .xlsx ou .csv dans /app/data/")
    ap.add_argument("--sport", type=Path, help="Fichier sport optionnel .xlsx ou .csv")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--source-label", default="Fichiers RH fournis — POC")
    ap.add_argument("--commit", action="store_true", help="Active l'écriture PostgreSQL ; sinon inspection seule")
    args = ap.parse_args()
    if args.year < 2020 or args.year > 2100:
        ap.error("year doit être entre 2020 et 2100")
    try:
        rows, stats = prepare(read_rows(args.rh), read_rows(args.sport) if args.sport else None)
        digest = snapshot_fingerprint(rows, args.year)
        print(json.dumps({"status": "READY", "reference_year": args.year,
                          "source_hash_prefix": digest[:16], "stats": stats,
                          "annual_payroll_gross_eur": str(sum(Decimal(x["annual_gross_salary"]) for x in rows)),
                          "note": "Budget non approuvé : salaires sources non encore validés"}, ensure_ascii=False))
        if args.commit:
            batch_id, created = write_database(rows, args.year, digest, args.source_label)
            print(json.dumps({"status": "IMPORTED", "import_id": batch_id, "new_version": created,
                              "employees": len(rows)}))
        else:
            print("Aucune écriture PostgreSQL (ajouter --commit après validation)")
    except RHImportError as exc:
        ap.exit(2, f"ERREUR : {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
