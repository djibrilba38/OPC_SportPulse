"""Étape 17 — activité saisie par salarié RH : PostgreSQL est la source métier.

Seuls les comptes non-démo reliés explicitement à l'import RH actif peuvent
créer une déclaration. Les Gold synthétiques ne deviennent pas des activités
personnellement déclarées. Slack utilise l'outbox de la même transaction SQL.

Aucune prime versée, validation de trajet ou journée RH accordée ici.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException

from sportpulse import employee_portal as portal

router = APIRouter(tags=["rh-activities-poc"])


def _dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise HTTPException(503, "DATABASE_URL non configuré")
    return dsn


def social_year_for(day: date) -> int:
    month = int(os.getenv("PORTAL_SOCIAL_YEAR_START_MONTH", "1"))
    if not 1 <= month <= 12:
        raise HTTPException(503, "Configuration de l'année sociale invalide")
    return day.year if day.month >= month else day.year - 1


def require_active_link(cur: Any, acct: dict, year: int) -> str:
    """Double rattachement : ID compte SQLite + ID salarié et import RH actif.

    Ne jamais rapprocher les utilisateurs par e-mail ou nom.
    """
    if acct.get("is_demo"):
        raise HTTPException(403, "Compte démo : aucune liaison RH")
    cur.execute("""
        SELECT l.import_id
          FROM rh.portal_account_links l
          JOIN rh.import_batches b ON b.import_id=l.import_id
         WHERE l.portal_account_id=%s AND l.employee_id=%s
           AND b.active=TRUE AND b.reference_year=%s
         LIMIT 1
    """, (acct["id"], acct["employee_id"], year))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(409, "Compte non rattaché à l'import RH actif : utilise « Synchroniser les comptes » dans /admin/rh.")
    return str(row[0])


def create_real_activity(acct: dict, body: Any, started: datetime, background_tasks: Any) -> dict:
    """Enregistre la déclaration et l'événement Slack atomiquement dans PG.

    Les modifications/suppressions existantes continuent via activity_management.
    Ne pas utiliser SQLite pour les déclarations RH.
    """
    if acct.get("is_demo"):
        raise HTTPException(403, "Ce service est réservé aux comptes RH")
    if started.tzinfo is None:
        raise HTTPException(422, "Une date avec fuseau horaire est nécessaire")
    from sportpulse.activity_notifications import real_enqueue, event_payload, process_event
    import psycopg
    event_key = None
    year = social_year_for(started.date())
    try:
        with psycopg.connect(_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                require_active_link(cur, acct, year)
                cur.execute("""
                    INSERT INTO benefits.wellbeing_activity_declarations
                      (employee_id, activity_date, activity_type, duration_min,
                       outside_work, validation_status, started_at, distance_km,
                       comment, portal_revision, updated_at)
                    VALUES (%s,%s,%s,%s,TRUE,'pending',%s,%s,%s,1,NOW())
                    RETURNING activity_id
                """, (acct["employee_id"], started.date(), body.activity_type,
                      body.duration_min, started, body.distance_km, body.comment))
                activity_id = int(cur.fetchone()[0])
                if body.share_to_slack:
                    event_key = real_enqueue(
                        cur, employee_id=acct["employee_id"], activity_id=activity_id,
                        payload=event_payload(
                            display_name=acct["display_name"], activity_type=body.activity_type,
                            started_at=started.isoformat(), duration_min=body.duration_min,
                            distance_km=body.distance_km, is_demo=False))
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(503, "Enregistrement PostgreSQL indisponible ; aucune publication Slack effectuée") from exc
    # Envoi après commit uniquement : ne jamais publier une transaction annulée.
    if event_key:
        background_tasks.add_task(process_event, "real", event_key)
    return {"activity_id": f"decl-{activity_id}", "status": "declared_poc",
            "message": "Activité enregistrée dans PostgreSQL et comptée dans les objectifs du POC.",
            "slack_status": "queued" if event_key else "not_shared",
            "reporting_status": "postgres_immediate"}


def _month_reference(first: date, following: date) -> date:
    today = datetime.now(timezone.utc).date()
    return today if first <= today < following else first


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    return start, date(start.year + (start.month == 12), start.month % 12 + 1, 1)


def _monthly_goal() -> int:
    try:
        value = int(os.getenv("SPORTPULSE_MONTHLY_GOAL", "4"))
    except ValueError:
        value = 4
    return value if 1 <= value <= 31 else 4


def _as_activity(row: Any) -> dict:
    activity_id, kind, started, activity_date, duration, distance, comment, revision, status = row
    if started is not None:
        timestamp = started.isoformat()
    else:
        timestamp = str(activity_date) + "T12:00:00+00:00"
    return {
        "id": f"decl-{activity_id}", "activity_type": kind,
        "label": portal.ALLOWED_SPORTS.get(kind, kind), "started_at": timestamp,
        "duration_min": int(duration),
        "distance_km": float(distance) if distance is not None else None,
        "comment": comment or "", "revision": int(revision),
        "validation_status": "declared_poc" if status == "pending" else status,
        "source": "portal_declared", "editable": status == "pending",
        "counted_for_benefits": True,
    }


def build_real_portal_dashboard(acct: dict, year: int) -> dict:
    """Objectifs : activités déclarées actives uniquement, PAS 1 803 Gold synthétiques.

    Les Gold restent dans le reporting RH (source distincte). On effectue un
    agrégat SQL sans LIMIT pour que les KPI ne soient pas tronqués par l'UI.
    """
    if not 2020 <= year <= 2100:
        raise HTTPException(422, "Année sociale invalide")
    first, following = portal.social_window(year)
    month_ref = _month_reference(first, following)
    month_start, month_end = _month_bounds(month_ref)
    import psycopg
    try:
        with psycopg.connect(_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                require_active_link(cur, acct, year)
                # Historique déclaré pour ce seul salarié, hors suppressions.
                cur.execute("""
                    SELECT COUNT(*),COALESCE(SUM(distance_km),0),
                           COALESCE(SUM(duration_min),0),
                           COUNT(*) FILTER (WHERE activity_date >= %s AND activity_date < %s)
                    FROM benefits.wellbeing_activity_declarations
                    WHERE employee_id=%s AND activity_date >= %s AND activity_date < %s
                      AND deleted_at IS NULL AND outside_work=TRUE
                      AND validation_status <> 'rejected'
                """, (month_start, month_end, acct["employee_id"], first, following))
                n, km, duration, monthly = cur.fetchone()
                cur.execute("""
                    SELECT activity_id, activity_type, started_at, activity_date,
                           duration_min, distance_km, comment, portal_revision, validation_status
                    FROM benefits.wellbeing_activity_declarations
                    WHERE employee_id=%s AND activity_date >= %s AND activity_date < %s
                      AND deleted_at IS NULL AND outside_work=TRUE
                      AND validation_status <> 'rejected'
                    ORDER BY started_at DESC NULLS LAST, activity_id DESC LIMIT 100
                """, (acct["employee_id"], first, following))
                activities = [_as_activity(row) for row in cur.fetchall()]
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(503, "Impossible de consulter les activités PostgreSQL") from exc
    n, monthly = int(n), int(monthly)
    goal = _monthly_goal()
    return {
        "employee": {"id": acct["employee_id"], "name": acct["display_name"], "demo": False},
        "year": year,
        "period": {"start": first.isoformat(), "end_exclusive": following.isoformat()},
        "sports": {"count": n, "declared_count": n, "distance_km": round(float(km), 1),
                   "duration_min": int(duration), "goal": 15,
                   "score": min(100, round(min(n, 15) / 15 * 100)),
                   "score_label": "Progression POC : activités déclarées"},
        "poc_monthly": {"month": month_start.strftime("%Y-%m"), "count": monthly,
                        "goal": goal, "remaining": max(0, goal-monthly),
                        "progress_percent": min(100, round(100*monthly/goal))},
        "benefits": {"bonus_rate": None, "bonus_estimate_eur": None,
                     "bonus_status": "simulation_RH_distincte", "days_official": None,
                     "days_potential": 5 if n >= 15 else 0,
                     "wellbeing_status": "potentiel_sans_attribution_RH"},
        "commutes": [], "activities": activities,
        "notices": [
            "Les objectifs personnels comptent uniquement les activités déclarées dans le portail et non supprimées.",
            "Les 1 803 activités historiques Gold sont synthétiques et restent dans le reporting global, pas dans le compteur personnel.",
            "Aucune prime ni journée bien-être n'est automatiquement accordée par ces déclarations.",
        ],
    }


@router.get("/employee/api/rh-link-status")
def rh_link_status(
    session: str | None = Cookie(default=None, alias=portal.COOKIE_NAME),
):
    acct = portal.current_account(session)
    if acct["is_demo"]:
        return {"employee_id": acct["employee_id"], "is_demo": True, "linked_rh": False}
    import psycopg
    try:
        with psycopg.connect(_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                require_active_link(cur, acct, portal.current_social_year())
        return {"employee_id": acct["employee_id"], "is_demo": False, "linked_rh": True}
    except HTTPException as exc:
        if exc.status_code == 409:
            return {"employee_id": acct["employee_id"], "is_demo": False, "linked_rh": False}
        raise
    except psycopg.Error as exc:
        raise HTTPException(503, "Vérification du lien RH indisponible") from exc
