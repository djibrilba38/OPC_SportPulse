"""SportPulse AI – étape 9 : reporting annuel social, temps quasi réel, en lecture seule.

Deux sources distinctes : SQLite 100 % SYNTHÉTIQUE du portail, et les
DÉCLARATIONS RH PostgreSQL (pré-validation vs validées) ; jamais mélangées.
Les primes affichées sont des scénarios archivés, jamais un ordre de paiement.
Les activités supprimées logiquement sont exclues du comptage.
"""
from __future__ import annotations

import os
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import FileResponse

from sportpulse import admin_portal as admin
from sportpulse import employee_portal as portal

router = APIRouter(tags=["reporting-annuel"])
ASSETS = Path(__file__).resolve().parent / "reporting_assets"
SPORTS = portal.ALLOWED_SPORTS


def reporting_window(year: int) -> tuple[date, date]:
    if not 2020 <= year <= 2100:
        raise HTTPException(422, detail="Année sociale hors plage (2020–2100).")
    return portal.social_window(year)


def _money(num: Decimal) -> str:
    return str(num.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _cost_assumption() -> Decimal | None:
    value = os.getenv("SPORTPULSE_WELLBEING_DAILY_COST_EUR", "").strip()
    if not value:
        return None
    try:
        amount = Decimal(value)
        if not amount.is_finite() or not (0 <= amount <= 10000):
            raise InvalidOperation
        return amount
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("SPORTPULSE_WELLBEING_DAILY_COST_EUR invalide") from exc


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=8)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def demo_rows(start: date, end: date) -> tuple[list[dict], list[dict], int]:
    """Lire uniquement les comptes is_demo=1. Pas d'identité personnelle dans la réponse."""
    conn = _connect_ro(portal.db_path())
    if conn is None:
        return [], [], 0
    try:
        if not _table_exists(conn, "accounts") or not _table_exists(conn, "demo_activities"):
            return [], [], 0
        total = conn.execute("SELECT COUNT(*) FROM accounts WHERE is_demo=1").fetchone()[0]
        has_delete = "deleted_at" in _columns(conn, "demo_activities")
        outbox_exists = _table_exists(conn, "sportpulse_notifications_outbox")
        join_sql = "LEFT JOIN sportpulse_notifications_outbox n ON n.activity_id=a.id" if outbox_exists else ""
        status_sql = "COALESCE(n.status, 'not_shared')" if outbox_exists else "'not_shared'"
        rows = conn.execute(f"""
            SELECT a.id, a.account_id, a.activity_type, a.started_at,
                   a.duration_min, a.distance_km, {status_sql} AS slack_status
            FROM demo_activities a JOIN accounts u ON a.account_id=u.id
            {join_sql}
            WHERE u.is_demo=1 AND a.started_at>=? AND a.started_at<?
            {"AND a.deleted_at IS NULL" if has_delete else ""}
            ORDER BY a.started_at DESC
        """, (start.isoformat(), end.isoformat())).fetchall()
        activities = [{**dict(row), "valid_for_threshold": True, "status": "simulated"} for row in rows]
        commutes = []
        if _table_exists(conn, "demo_commutes"):
            commutes = [dict(r) for r in conn.execute("""
                SELECT c.day, c.verification_status AS status
                FROM demo_commutes c JOIN accounts u ON c.account_id=u.id
                WHERE u.is_demo=1 AND c.day>=? AND c.day<?
            """, (start.isoformat(), end.isoformat())).fetchall()]
        return activities, commutes, int(total)
    finally:
        conn.close()


def postgres_rows(start: date, end: date) -> tuple[list[dict], list[dict], int]:
    """Lecture seule des déclarations dans benefits. Les saisies pending ne donnent aucun droit."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise HTTPException(503, detail="DATABASE_URL manquant")
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                  SELECT a.activity_id, a.employee_id, a.activity_type,
                         a.activity_date, a.duration_min, a.distance_km,
                         a.validation_status, a.outside_work,
                         COALESCE(n.status,'not_shared') AS slack_status
                  FROM benefits.wellbeing_activity_declarations a
                  LEFT JOIN benefits.sportpulse_notifications_outbox n
                    ON n.activity_id=a.activity_id
                  WHERE a.activity_date >= %s AND a.activity_date < %s
                    AND a.deleted_at IS NULL
                  ORDER BY a.activity_date DESC
                """, (start, end))
                activities = []
                for r in cur.fetchall():
                    activities.append({
                        "id": int(r[0]), "account_id": str(r[1]),
                        "activity_type": r[2], "started_at": r[3].isoformat(),
                        "duration_min": int(r[4]),
                        "distance_km": float(r[5]) if r[5] is not None else None,
                        "status": str(r[6]),
                        "valid_for_threshold": bool(r[7]),
                        "slack_status": str(r[8]),
                    })
                cur.execute("""
                    SELECT commute_date, validation_status, office_arrival_confirmed
                    FROM benefits.sport_commute_declarations
                    WHERE commute_date >= %s AND commute_date < %s
                """, (start, end))
                commutes = [{"day": row[0].isoformat(),
                             "status": "validated" if row[1] == "validated" and row[2] else row[1]}
                            for row in cur.fetchall()]
                cur.execute("""
                    SELECT COUNT(*) FROM benefits.employee_compensation
                    WHERE reference_year=%s
                """, (start.year,))
                employee_count = int(cur.fetchone()[0]) if start.month == 1 else 0
        return activities, commutes, employee_count
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail="Reporting PostgreSQL indisponible ; vérifie les migrations benefits et la connexion.") from exc


def _latest_financial_scenario(source: str, year: int, *, calendar_aligned: bool) -> dict | None:
    """Lire un run administrateur enregistré sans recalculer ni modifier la règle RH."""
    if not calendar_aligned:
        return None
    conn = _connect_ro(admin.database_path())
    if conn is None:
        return None
    try:
        if _table_exists(conn, "scenario_runs"):
            row = conn.execute("""
                SELECT run_id,rule_version,source_fingerprint,result_json
                FROM scenario_runs WHERE source_kind=? AND reference_year=?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
            """, (source, year)).fetchone()
            if row:
                import json
                result = json.loads(row["result_json"])
                return {
                    "run_id": row["run_id"], "rule_version": row["rule_version"],
                    "source_fingerprint": row["source_fingerprint"][:12],
                    "reference_bonus_eur": result["reference_bonus_budget_eur"],
                    "simulated_bonus_eur": result["simulated_bonus_budget_eur"],
                    "delta_eur": result["delta_to_reference_eur"],
                    "simulated_rate": result["simulated_rate"],
                    "status": "SIMULATION_ONLY",
                }
        if _table_exists(conn, "admin_simulations"):
            row = conn.execute("""
                SELECT * FROM admin_simulations
                WHERE source=? AND reference_year=?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
            """, (source, year)).fetchone()
            if row:
                return {
                    "run_id": row["run_id"], "rule_version": None,
                    "source_fingerprint": None,
                    "reference_bonus_eur": row["previous_budget"],
                    "simulated_bonus_eur": row["new_budget"],
                    "delta_eur": row["budget_delta"],
                    "simulated_rate": row["simulated_rate"],
                    "status": "SIMULATION_ONLY",
                }
        return None
    finally:
        conn.close()


def aggregate_report(activities: list[dict], commutes: list[dict],
                     employee_count: int, source: str, year: int,
                     *, financial_scenario: dict | None = None,
                     daily_cost: Decimal | None = None) -> dict:
    start, end = reporting_window(year)
    months = defaultdict(lambda: {"activities": 0, "kilometers": 0.0, "duration_min": 0})
    types = Counter()
    slack = Counter()
    validation = Counter()
    per_employee = Counter()
    latest = []
    total_minutes = 0
    km_total = Decimal(0)
    for row in activities:
        stamp = str(row["started_at"])
        if not (start.isoformat() <= stamp[:10] < end.isoformat()):
            continue
        kind = str(row["activity_type"])
        duration = int(row["duration_min"])
        distance = row.get("distance_km")
        distance = Decimal(str(distance)) if distance is not None else Decimal(0)
        if duration <= 0 or distance < 0 or not distance.is_finite():
            continue
        month = stamp[:7]
        months[month]["activities"] += 1
        months[month]["kilometers"] += float(distance)
        months[month]["duration_min"] += duration
        km_total += distance
        total_minutes += duration
        types[kind] += 1
        status = str(row.get("slack_status") or "not_shared")
        slack[status] += 1
        validation[str(row.get("status") or "unknown")] += 1
        if row.get("valid_for_threshold", False):
            per_employee[str(row["account_id"])] += 1
        latest.append({
            "activity_id": str(row["id"]), "date": stamp[:10],
            "sport": SPORTS.get(kind, kind[:40]), "duration_min": duration,
            "distance_km": float(distance) if row.get("distance_km") is not None else None,
            "validation": str(row.get("status") or "unknown"),
            "slack": status,
        })
    eligible = sum(count >= 15 for count in per_employee.values())
    potential_days = eligible * 5
    month_list = [{"month": key, "activities": val["activities"],
                   "kilometers": round(val["kilometers"], 2), "duration_min": val["duration_min"]}
                  for key, val in sorted(months.items())]
    types_list = [{"sport": SPORTS.get(name, name[:40]), "count": count}
                  for name, count in types.most_common()]
    # L'absence de trajets validés n'est jamais une preuve de fraude.
    commute_summary = Counter(str(c.get("status") or "unknown") for c in commutes)
    result = {
        "source": source,
        "source_label": "SYNTHÉTIQUE — comptes de démonstration" if source == "demo" else "POSTGRESQL — déclarations RH",
        "social_year": year,
        "period": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "employee_count_reference": employee_count,
        "activity_count": sum(v["activities"] for v in months.values()),
        "distance_km": float(km_total.quantize(Decimal("0.01"))),
        "duration_min": total_minutes,
        "activity_types": types_list,
        "monthly": month_list,
        "validation_status": dict(validation),
        "slack_status": dict(slack),
        "latest_activities": sorted(latest, key=lambda x: (x["date"], x["activity_id"]), reverse=True)[:15],
        "commutes": {"total": len(commutes), "by_status": dict(commute_summary)},
        "wellbeing": {
            "threshold_activities": 15,
            "potential_eligible_employees": eligible,
            "potential_days": potential_days,
            "validated_only": source == "postgres",
            "daily_cost_assumption_eur": _money(daily_cost) if daily_cost is not None else None,
            "estimated_capacity_cost_eur": _money(daily_cost * potential_days) if daily_cost is not None else None,
            "official_entitlement": None,
        },
        "finance": {"latest_saved_simulation": financial_scenario},
        "ai": {"generator_tracking": False, "note": "Provenance Mistral/template non encore historisée ; succès Slack ≠ appel IA confirmé."},
        "notes": [
            "Les salariés et salaires individuels ne sont pas transmis à cette vue agrégée.",
            "Les modifications d'activité remplacent la valeur précédente ; les suppressions logiques sont exclues.",
            "Les cinq jours sont potentiels : les décisions RH officielles ne sont pas prises ici.",
            "La prime provient d'un scénario archivé, jamais d'un recalcul implicite ni d'un paiement.",
        ],
    }
    if source == "demo":
        result["notes"].insert(0, "Données synthétiques : les 15 activités déclenchent une simulation, pas un droit RH.")
    if start.month != 1:
        result["notes"].append("Année sociale décalée : scénarios de prime existants basés sur l'année civile, non affichés.")
    if source == "postgres":
        result["notes"].append("Le seuil bien-être du POC utilise les activités déclarées hors travail; aucun droit officiel n’est accordé automatiquement.")
    return result


def build_report(source: Literal["demo", "postgres"], year: int) -> dict:
    # SPORTPULSE_STEP16_GOLD_ROUTING — Gold Kestra + RH, pas la table benefits vide.
    if source == "postgres":
        from sportpulse.reporting_pipeline import build_pipeline_report
        return build_pipeline_report(year)
    start, end = reporting_window(year)
    if source == "demo":
        if os.getenv("ADMIN_DEMO_MODE", "false").lower() != "true":
            raise HTTPException(403, detail="Mode démonstration désactivé.")
        activities, commutes, employees = demo_rows(start, end)
    else:
        activities, commutes, employees = postgres_rows(start, end)
    scenario = _latest_financial_scenario(source, year, calendar_aligned=start.month == 1)
    report = aggregate_report(activities, commutes, employees, source, year,
                              financial_scenario=scenario, daily_cost=_cost_assumption())
    report["declared_count"] = len(activities)
    report["wellbeing"]["validated_only"] = False
    report["notes"].insert(0, "Indicateurs POC calculés sans QR sur les activités déclarées non supprimées. Aucun droit accordé automatiquement.")
    return report


@router.get("/admin/reporting", include_in_schema=False)
def reporting_page(session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session)
    return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/admin/reporting/assets/{name}", include_in_schema=False)
def reporting_assets(name: Literal["style.css", "app.js"],
                     session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session)
    return FileResponse(ASSETS / name, headers={"Cache-Control": "no-store"})


@router.get("/admin/api/reporting/annual")
def reporting_data(source: Literal["demo", "postgres"] = "postgres", year: int | None = None,
                   session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session)
    target = portal.current_social_year() if year is None else year
    return build_report(source, target)
