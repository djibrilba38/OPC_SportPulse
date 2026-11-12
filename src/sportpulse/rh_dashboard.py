"""Vue RH admin à partir des classeurs réellement fournis au POC.

Pas d'accès public, jamais d'attribution de prime. Les sources sont versionnées.
"""
from __future__ import annotations

import os
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Query
from fastapi.responses import FileResponse

from sportpulse import admin_portal as admin

router = APIRouter(tags=["rh-import-reel"])
ASSETS = Path(__file__).resolve().parent / "rh_assets"


def money(value: Decimal | int | str) -> str:
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def import_rows(year: int) -> tuple[dict | None, list[dict]]:
    """Lit le dernier instantané RH actif. Ne peut pas retomber sur la démo."""
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise HTTPException(503, detail="DATABASE_URL non configuré")
    import psycopg
    try:
        with psycopg.connect(dsn, connect_timeout=7) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT to_regclass('rh.import_batches'), to_regclass('benefits.sport_commute_declarations'), to_regclass('benefits.wellbeing_activity_declarations')")
                schema, commute_table, activities_table = cur.fetchone()
                if not schema:
                    raise HTTPException(503, detail="Migration rh absente : appliquer sql/step10_rh_import.sql")
                cur.execute("""SELECT import_id, reference_year, source_label, source_type, source_hash,
                                      employee_count, imported_at
                               FROM rh.import_batches WHERE reference_year=%s AND active=TRUE""", (year,))
                batch_row = cur.fetchone()
                if not batch_row:
                    return None, []
                batch = {key: val for key, val in zip(("import_id", "reference_year", "source_label", "source_type", "source_hash", "employee_count", "imported_at"), batch_row)}
                # Ne pas transformer des données simulées ou absentes en décisions RH.
                commute_cte = """SELECT employee_id,
                    COUNT(*) FILTER (WHERE validation_status='validated' AND office_arrival_confirmed=TRUE)::integer AS validated_commutes
                    FROM benefits.sport_commute_declarations
                    WHERE commute_date>= %s AND commute_date < %s
                    GROUP BY employee_id""" if commute_table else "SELECT NULL::text employee_id, 0::integer validated_commutes WHERE FALSE"
                # La colonne deleted_at est ajoutée par l'étape 7, mais pas forcément dans les anciens environnements.
                activity_deleted_clause = ""
                if activities_table:
                    cur.execute("""SELECT EXISTS (
                      SELECT 1 FROM information_schema.columns
                      WHERE table_schema='benefits' AND table_name='wellbeing_activity_declarations' AND column_name='deleted_at')""")
                    if cur.fetchone()[0]:
                        activity_deleted_clause = " AND deleted_at IS NULL"
                activity_cte = ("""SELECT employee_id,
                    COUNT(*) FILTER (WHERE outside_work=TRUE)::integer AS validated_activities,
                    COUNT(*)::integer AS declarations_total
                    FROM benefits.wellbeing_activity_declarations
                    WHERE activity_date>= %s AND activity_date < %s""" + activity_deleted_clause + " GROUP BY employee_id") if activities_table else "SELECT NULL::text employee_id, 0::integer validated_activities,0::integer declarations_total WHERE FALSE"
                params: list = []
                for present in (commute_table, activities_table):
                    if present:
                        params.extend([date(year, 1, 1), date(year + 1, 1, 1)])
                params.append(batch["import_id"])
                # Paramètres systématiquement liés ; les noms de tables sont des constantes.
                query = f"""
                    WITH commute_counts AS ({commute_cte}), activity_counts AS ({activity_cte})
                    SELECT s.employee_id, s.department, s.annual_gross_salary,
                           s.commute_mode, s.commute_class, s.declared_sport,
                           s.sporting_commute_candidate,
                           COALESCE(c.validated_commutes,0), COALESCE(a.validated_activities,0),
                           COALESCE(a.declarations_total,0), COALESCE(comp.salary_validated,FALSE)
                    FROM rh.employee_versions s
                    LEFT JOIN commute_counts c ON c.employee_id=s.employee_id
                    LEFT JOIN activity_counts a ON a.employee_id=s.employee_id
                    LEFT JOIN benefits.employee_compensation comp
                      ON comp.employee_id=s.employee_id AND comp.reference_year=%s
                    WHERE s.import_id=%s ORDER BY s.employee_id
                """
                # L'année de rémunération précède la version active dans les paramètres.
                params.insert(-1, year)
                cur.execute(query, params)
                result = []
                for row in cur.fetchall():
                    result.append({
                        "employee_id": row[0], "department": row[1],
                        "annual_gross_salary": Decimal(row[2]), "commute_mode": row[3],
                        "commute_class": row[4], "declared_sport": row[5],
                        "sporting_commute_candidate": bool(row[6]),
                        "validated_commutes": int(row[7]), "validated_activities": int(row[8]),
                        "declarations_total": int(row[9]), "salary_validated": bool(row[10]),
                    })
                return batch, result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail="Lecture PostgreSQL RH indisponible. Vérifier migrations et connexion.") from exc


def compute_overview(batch: dict, rows: list[dict], rate_percent: Decimal = Decimal("5")) -> dict:
    if not Decimal(0) <= rate_percent <= Decimal(20):
        raise ValueError("Taux hors plage 0..20")
    rate = rate_percent / Decimal(100)
    total = sum((r["annual_gross_salary"] for r in rows), Decimal(0))
    candidates = [r for r in rows if r["sporting_commute_candidate"]]
    candidate_salary = sum((r["annual_gross_salary"] for r in candidates), Decimal(0))
    validated_activity_people = sum(r["validated_activities"] >= 15 for r in rows)
    units: dict[str, dict] = defaultdict(lambda: {"employees": 0, "sporting_declarations": 0, "payroll": Decimal(0), "candidate_payroll": Decimal(0)})
    for r in rows:
        bucket = units[r["department"]]
        bucket["employees"] += 1
        bucket["sporting_declarations"] += int(r["sporting_commute_candidate"])
        bucket["payroll"] += r["annual_gross_salary"]
        if r["sporting_commute_candidate"]:
            bucket["candidate_payroll"] += r["annual_gross_salary"]
    return {
        "source": "rh_files_provided", "reference_year": int(batch["reference_year"]),
        "source_label": batch["source_label"], "import_id": batch["import_id"],
        "source_hash_prefix": batch["source_hash"][:12], "imported_at": str(batch["imported_at"]),
        "metrics": {
            "employees_total": len(rows),
            "annual_payroll_gross_eur": money(total),
            "employees_declaring_sport_commute": len(candidates),
            "employees_commute_to_review": sum(r["commute_class"] == "review" for r in rows),
            "salary_records_validated_by_hr": sum(r["salary_validated"] for r in rows),
            "employees_with_validated_commutes": sum(r["validated_commutes"] > 0 for r in rows),
            "activity_declarations_recorded": sum(r["declarations_total"] for r in rows),
            "validated_outside_work_activities": sum(r["validated_activities"] for r in rows),
            "employees_reaching_15_validated_activities": validated_activity_people,
            "wellbeing_days_potential_pending_rh": validated_activity_people * 5,
            "bonus_rate_percent": str(rate_percent),
            "bonus_budget_provisional_eur": money(candidate_salary * rate),
            "bonus_budget_upper_bound_all_employees_eur": money(total * rate),
            "bonus_budget_approved_eur": None,
        },
        "by_department": [{"department": dep, "employees": v["employees"],
            "sporting_commute_declarations": v["sporting_declarations"],
            "payroll_gross_eur": money(v["payroll"]),
            "provisional_bonus_budget_eur": money(v["candidate_payroll"] * rate)}
            for dep, v in sorted(units.items())],
        "notes": [
            "Données issues des fichiers RH du POC : non certifiées comme paie réelle.",
            "Montant de prime = hypothèse sur modes de trajet déclarés, non prime accordée.",
            "Distances réelles, majorité du trajet, fréquence et validation RH restent à contrôler.",
            "Seules les activités hors travail validées sont retenues pour l'estimation des cinq jours.",
            "Les sessions sportives simulées ne donnent aucun droit RH.",
        ],
    }


def hr_financial_snapshot(year: int) -> dict | None:
    """Adaptateur vers l'ancien simulateur /admin/replay — vrai référentiel RH, scénario uniquement."""
    batch, rows = import_rows(year)
    if not batch:
        return None
    summary = compute_overview(batch, rows)
    candidates_salary = sum((r["annual_gross_salary"] for r in rows if r["sporting_commute_candidate"]), Decimal(0))
    return {
        "source": "postgres", "source_label": f"POSTGRESQL — RH fournie ({batch['import_id']})",
        "source_import_id": batch["import_id"], "year": year,
        "employee_count": len(rows),
        "eligible_candidates": summary["metrics"]["employees_declaring_sport_commute"],
        "annual_salary_total": candidates_salary,
        "current_rate": Decimal("0.05"),
        "policy_status": "to_confirm",
        "warning": "Candidats selon transport déclaré; contrôle trajet et validation RH requis. SIMULATION UNIQUEMENT.",
    }


@router.get("/admin/rh", include_in_schema=False)
def rh_admin_page(session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session)
    return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/admin/rh/assets/{asset}", include_in_schema=False)
def rh_asset(asset: str, session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session)
    if asset not in {"app.js", "style.css", "sp14_rh.js", "sp14_rh.css", "sp15_rh.js", "sp15_rh.css"}:
        raise HTTPException(404)
    return FileResponse(ASSETS / asset, headers={"Cache-Control": "no-store"})


@router.get("/admin/api/rh/overview")
def rh_overview(year: int = Query(default=2026, ge=2020, le=2100),
                rate_percent: Decimal = Query(default=Decimal("5"), ge=0, le=20),
                session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session)
    batch, rows = import_rows(year)
    if not batch:
        return {"status": "not_imported", "reference_year": year,
                "message": "Aucun import RH réel actif pour l'année. Utilise sportpulse.rh_import."}
    return {"status": "ok", **compute_overview(batch, rows, rate_percent)}


@router.get("/admin/api/rh/employees")
def rh_employees(year: int = Query(default=2026, ge=2020, le=2100),
                 limit: int = Query(default=50, ge=1, le=100),
                 offset: int = Query(default=0, ge=0),
                 session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session, required_role="admin")
    batch, rows = import_rows(year)
    if not batch:
        return {"total": 0, "items": [], "status": "not_imported"}
    # Identifiant et rémunération : uniquement administrateur RH authentifié.
    return {"total": len(rows), "source_import_id": batch["import_id"],
            "items": [{"employee_id": r["employee_id"], "department": r["department"],
                       "annual_gross_salary_eur": money(r["annual_gross_salary"]),
                       "declared_commute_mode": r["commute_mode"],
                       "sporting_commute_candidate": r["sporting_commute_candidate"],
                       "declared_sport": r["declared_sport"],
                       "validated_commutes": r["validated_commutes"],
                       "validated_activities": r["validated_activities"],
                       "salary_validated": r["salary_validated"]}
                      for r in rows[offset:offset + limit]]}
