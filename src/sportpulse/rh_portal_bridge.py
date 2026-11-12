"""Lien comptes salariés <-> référentiel RH, validation courte, replay RH.

Le lien métier est strictement l'employee_id issu du référentiel RH actif.
Aucun rapprochement sur noms/e-mails et aucune attribution automatique de prime.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from sportpulse import admin_portal as admin
from sportpulse import employee_portal as portal
from sportpulse import scenario_replay as replay

router = APIRouter(tags=["rh-portail-salarie-lien"])


def _admin(session: str | None, *, write: bool = False, request=None, csrf=None):
    actor = admin.current_admin(session, required_role="admin" if write else None)
    if write:
        admin.require_csrf(request, actor, csrf)
    return actor


def _hr_ids(year: int) -> set[str]:
    """Ne retourne que les IDs RH de la version active; échoue si PostgreSQL inaccessible."""
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(503, "Référentiel RH indisponible")
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=5) as pg:
            with pg.cursor() as cur:
                cur.execute("""SELECT e.employee_id
                    FROM rh.employee_versions e
                    JOIN rh.import_batches b ON b.import_id=e.import_id
                    WHERE b.active=TRUE AND b.reference_year=%s""", (year,))
                return {str(row[0]) for row in cur.fetchall()}
    except Exception as exc:
        raise HTTPException(503, "Impossible de consulter le référentiel RH actif") from exc



def _hr_links(year: int) -> set[str]:
    dsn=os.getenv("DATABASE_URL", "")
    if not dsn: raise HTTPException(503, "PostgreSQL RH indisponible")
    try:
        import psycopg
        with psycopg.connect(dsn,connect_timeout=5) as pg:
            with pg.cursor() as cur:
                cur.execute("""SELECT l.employee_id FROM rh.portal_account_links l
                    JOIN rh.import_batches b ON b.import_id=l.import_id
                    WHERE b.active=TRUE AND b.reference_year=%s""",(year,))
                return {str(r[0]) for r in cur.fetchall()}
    except Exception as exc:
        raise HTTPException(503,"Migration RH étape 14 non appliquée ou connexion indisponible") from exc


class LinkSync(BaseModel):
    model_config=ConfigDict(extra="forbid")
    reference_year: int=Field(default=2026,ge=2020,le=2100)


@router.post("/admin/api/rh/portal-accounts/sync")
def sync_portal_accounts(body: LinkSync, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    actor=_admin(session,write=True,request=request,csrf=csrf)
    real_accounts=[a for a in _portal_accounts("",limit=10000) if not a["is_demo"]]
    try:
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"],connect_timeout=5) as pg:
            with pg.cursor() as cur:
                cur.execute("""SELECT b.import_id,e.employee_id
                    FROM rh.import_batches b JOIN rh.employee_versions e USING(import_id)
                    WHERE b.active=TRUE AND b.reference_year=%s""",(body.reference_year,))
                entries=cur.fetchall()
                if not entries:
                    raise HTTPException(409,"Aucun référentiel RH actif pour cette année")
                import_id=entries[0][0]; valid={r[1] for r in entries}
                linked=0;unmatched=[]
                for a in real_accounts:
                    if a["employee_id"] not in valid:
                        unmatched.append(a["employee_id"]);continue
                    cur.execute("""INSERT INTO rh.portal_account_links
                      (import_id,employee_id,portal_account_id,linked_by_admin_id)
                      VALUES(%s,%s,%s,%s) ON CONFLICT(import_id,employee_id) DO NOTHING
                      RETURNING employee_id""",(import_id,a["employee_id"],a["id"],actor["id"]))
                    inserted=cur.fetchone()
                    if inserted: linked+=1
                    else:
                        cur.execute("""SELECT portal_account_id FROM rh.portal_account_links
                            WHERE import_id=%s AND employee_id=%s""",(import_id,a["employee_id"]))
                        current=cur.fetchone()
                        if not current or int(current[0])!=a["id"]:
                            raise HTTPException(409,"Conflit d'identité RH : vérification manuelle nécessaire")
        with admin.connect_admin() as conn:
            admin.audit(conn,actor["id"],"rh_portal_link_sync",import_id,
                        {"year":body.reference_year,"new_links":linked,"not_matched":len(unmatched)})
        return {"import_id":import_id,"created_links":linked,"unmatched":unmatched,
                "notice":"Seuls les comptes enregistrés via une identité RH exacte sont liés; les démos sont exclues."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,"Synchronisation PostgreSQL indisponible") from exc


def _portal_accounts(search: str = "", *, limit: int = 100) -> list[dict]:
    with portal.connect() as conn:
        term = f"%{search.strip()}%"
        rows = conn.execute("""SELECT id,employee_id,display_name,email,is_demo,created_at
            FROM accounts WHERE (employee_id LIKE ? OR display_name LIKE ? OR email LIKE ?)
            ORDER BY created_at DESC,id DESC LIMIT ?""", (term,term,term,limit)).fetchall()
        return [dict(r) for r in rows]


@router.get("/admin/api/rh/portal-accounts")
def portal_accounts(
    year: int = Query(default=2026, ge=2020, le=2100),
    search: str = Query(default="", max_length=100),
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
):
    _admin(session)
    ids = _hr_ids(year)
    linked = _hr_links(year)
    accounts = _portal_accounts(search)
    return {"items": [
        {"employee_id": a["employee_id"], "name": a["display_name"],
         "email": a["email"], "is_demo": bool(a["is_demo"]),
         "rh_match": a["employee_id"] in ids,
         "linked_rh": not bool(a["is_demo"]) and a["employee_id"] in linked,
         "link_status": ("linked" if not a["is_demo"] and a["employee_id"] in linked else
                         "demo_account" if a["is_demo"] else "unmatched")}
        for a in accounts
    ], "total": len(accounts), "rh_reference_count": len(ids), "year": year,
        "notice": "Le lien RH exige une correspondance exacte et une synchronisation avec FK vers la version RH active."}


class InviteRH(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=2, max_length=128)
    reference_year: int = Field(default=2026, ge=2020, le=2100)


@router.post("/admin/api/rh/invitations", status_code=201)
def invite_rh(body: InviteRH, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    actor = _admin(session, write=True, request=request, csrf=csrf)
    if body.employee_id not in _hr_ids(body.reference_year):
        raise HTTPException(404, "Identifiant absent de la version RH active")
    try:
        token = portal.create_invitation(body.employee_id, body.display_name, demo=False)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    with admin.connect_admin() as conn:
        admin.audit(conn, actor["id"], "rh_employee_invited", body.employee_id,
                    {"year":body.reference_year})
    return {"employee_id":body.employee_id,"invitation_code":token,
            "expires_in_hours":48,
            "notice":"Remettre l'invitation au salarié par un canal privé. Ne pas copier dans Slack."}


# Le replay n'a plus de page indépendante : seules des routes RH authentifiées subsistent.
@router.get("/admin/api/rh/replay/rules")
def rh_rules(session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    return replay.list_rules(session=session)


@router.post("/admin/api/rh/replay/rules", status_code=201)
def rh_create_rule(body: replay.RuleCreate, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    return replay.new_rule(body,request,session=session,x_csrf_token=csrf)


@router.get("/admin/api/rh/replay/runs")
def rh_runs(session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    result=replay.list_runs(session=session)
    return {"items":result["items"][:5],"display_limit":5}


@router.post("/admin/api/rh/replay/runs", status_code=201)
def rh_new_run(body: replay.RunCreate, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    if body.source != 'postgres':
        raise HTTPException(422, "Le tableau RH utilise uniquement la source PostgreSQL")
    return replay.create_run(body,request,session=session,x_csrf_token=csrf)


@router.post("/admin/api/rh/replay/runs/{run_id}/replay", status_code=201)
def rh_replay_run(run_id: str, body: replay.ReplayCreate, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    # Le replay reproduit le snapshot initial, PAS une relecture du dernier CSV RH.
    return replay.replay(run_id,body,request,session=session,x_csrf_token=csrf)
