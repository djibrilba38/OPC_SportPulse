"""Vue personnelle des avantages POC : strictement liée au compte connecté.

Le fichier RH du POC est un jeu fourni, pas un registre de paiement.
Aucun compte démo n'obtient d'information salariale.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException

from sportpulse import employee_portal as portal

router = APIRouter(tags=["employee-benefits"])


def monthly_progress(activities: list[dict], month: str, goal: int) -> dict:
    seen = set()
    count = 0
    for a in activities:
        key = str(a.get("id", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        if str(a.get("started_at", ""))[:7] == month:
            count += 1
    return {"month": month, "count": count, "goal": goal,
            "remaining": max(0, goal - count),
            "progress_percent": min(100, round(100 * count / goal))}


def personal_finance(acct: dict, year: int) -> dict:
    base = {"linked_rh": False, "budget_potential_eur": None,
            "rate_percent": None, "candidate": None,
            "granted_bonus_eur": None, "paid_bonus_eur": None,
            "status": "no_rh_link", "source": None}
    if acct["is_demo"]:
        base["status"] = "demo_account"
        return base
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise HTTPException(503, "Référentiel RH indisponible")
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                # Double correspondance identité RH + ID de compte SQLite (non juste le nom).
                cur.execute("""
                    SELECT e.annual_gross_salary, e.sporting_commute_candidate,
                           s.bonus_rate
                    FROM rh.portal_account_links AS l
                    JOIN rh.import_batches AS b ON b.import_id=l.import_id
                    JOIN rh.employee_versions AS e
                      ON e.import_id=l.import_id AND e.employee_id=l.employee_id
                    LEFT JOIN LATERAL (
                        SELECT bonus_rate FROM rh.budget_scenarios
                        WHERE import_id=b.import_id AND reference_year=b.reference_year
                        ORDER BY calculated_at DESC,scenario_id DESC LIMIT 1
                    ) AS s ON TRUE
                    WHERE l.portal_account_id=%s AND l.employee_id=%s
                      AND b.active=TRUE AND b.reference_year=%s
                    LIMIT 1
                """, (acct["id"], acct["employee_id"], year))
                record = cur.fetchone()
        if record is None:
            return base
        salary, candidate, rate = record
        base.update(linked_rh=True, candidate=bool(candidate), status="candidate_not_approved" if candidate else "not_candidate",
                    source="rh.employee_versions (fichier RH du POC)")
        if rate is not None:
            base["rate_percent"] = str(Decimal(str(rate)) * 100)
            if candidate:
                base["budget_potential_eur"] = str((Decimal(str(salary)) * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return base
    except Exception as exc:
        raise HTTPException(503, "Données RH personnelles temporairement indisponibles") from exc


def build_benefits(acct: dict, year: int, sports_dashboard: dict) -> dict:
    start = sports_dashboard["period"]["start"]
    end = sports_dashboard["period"]["end_exclusive"]
    now = datetime.now(timezone.utc).date().isoformat()
    month = now[:7] if start <= now < end else start[:7]
    raw_goal = os.getenv("SPORTPULSE_MONTHLY_GOAL", "4")
    try:
        goal = int(raw_goal)
    except ValueError:
        goal = 4
    goal = goal if 1 <= goal <= 31 else 4
    month_data = sports_dashboard.get("poc_monthly") or monthly_progress(sports_dashboard.get("activities", []), month, goal)
    sport = sports_dashboard.get("sports", {})
    count = int(sport.get("count") or 0)
    return {"employee_id": acct["employee_id"], "year": year,
            "is_demo": bool(acct["is_demo"]),
            "annual_activities": count, "annual_goal": 15,
            "monthly": month_data,
            "wellbeing": {"eligible_by_count_only": count >= 15,
                          "potential_days": 5 if count >= 15 else 0,
                          "granted_days": None},
            "finance": personal_finance(acct, year),
            "notice": "Projection POC : aucune prime payée ni journée accordée sans décision RH."}


@router.get("/employee/api/benefits-overview")
def employee_benefits_overview(
        year: int | None = None,
        session: Annotated[str | None, Cookie(alias=portal.COOKIE_NAME)] = None):
    acct = portal.current_account(session)
    requested = portal.current_social_year() if year is None else year
    if not (2020 <= requested <= 2100):
        raise HTTPException(422, "Année sociale invalide")
    data = portal.dashboard(year=requested, session=session)
    return build_benefits(acct, requested, data)
