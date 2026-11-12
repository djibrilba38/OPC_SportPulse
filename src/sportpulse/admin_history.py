"""Archivage UI des historiques RH : ne supprime jamais l'audit sous-jacent.

La page /admin/replay est retirée de l'API, mais l'historique financier
est conservé pour conformité et traçabilité des scénarios déjà exécutés.
"""
from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from sportpulse import admin_portal as admin

router = APIRouter(tags=["admin-history"])


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS admin_hidden_history (
        kind TEXT NOT NULL CHECK(kind IN ('simulation','audit')),
        ref TEXT NOT NULL,
        archived_by_id INTEGER NOT NULL,
        archived_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY(kind, ref)
    )""")


def archive(kind: str, ref: str, request: Request, session: str | None, csrf: str | None):
    actor = admin.current_admin(session, required_role="admin")
    admin.require_csrf(request, actor, csrf)
    if len(ref) > 100 or not ref:
        raise HTTPException(422, "Référence incorrecte")
    with admin.connect_admin() as conn:
        ensure_schema(conn)
        if kind == "simulation":
            exists = conn.execute("SELECT 1 FROM admin_simulations WHERE run_id=?", (ref,)).fetchone()
        elif kind == "audit":
            if not ref.isascii() or not ref.isdecimal():
                raise HTTPException(422, "Référence d'audit incorrecte")
            exists = conn.execute("SELECT 1 FROM admin_audit WHERE event_id=?", (int(ref),)).fetchone()
        else:
            raise HTTPException(422, "Type incorrect")
        if not exists:
            raise HTTPException(404, "Entrée introuvable")
        conn.execute("INSERT OR IGNORE INTO admin_hidden_history(kind,ref,archived_by_id) VALUES (?,?,?)",
                     (kind, ref, actor["id"]))
        admin.audit(conn, actor["id"], "history_archived", f"{kind}:{ref}", {"kind": kind})
    return {"archived": True, "notice": "Entrée retirée de l'affichage. Trace conservée pour audit."}


@router.delete('/admin/api/history/{run_id}')
def hide_simulation(run_id: str, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    x_csrf_token: Annotated[str | None, Header(alias='X-CSRF-Token')] = None):
    return archive('simulation', run_id, request, session, x_csrf_token)


@router.delete('/admin/api/audit/{event_id}')
def hide_audit(event_id: int, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    x_csrf_token: Annotated[str | None, Header(alias='X-CSRF-Token')] = None):
    return archive('audit', str(event_id), request, session, x_csrf_token)


@router.get('/admin/api/demo-profiles')
def demo_profiles(session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    admin.current_admin(session, required_role='admin')
    from sportpulse import employee_portal as portal
    from sportpulse.activity_management import demo_setup
    from sportpulse.activity_proofs import _setup, _already_validated
    with portal.connect() as conn:
        demo_setup(conn)
        _setup(conn)
        people = conn.execute("""SELECT id, employee_id, display_name FROM accounts
                 WHERE is_demo=1 AND employee_id LIKE 'EMP-DEMO-QR-%'
                 ORDER BY employee_id LIMIT 10""").fetchall()
        result = []
        for person in people:
            activities = conn.execute("""SELECT id,activity_type,revision FROM demo_activities
                 WHERE account_id=? AND deleted_at IS NULL ORDER BY started_at DESC LIMIT 30""",
                 (person['id'],)).fetchall()
            refs = []
            for row in activities:
                ref=f"demo-{row['id']}"
                if not _already_validated(conn, person['employee_id'], ref, row['revision']):
                    refs.append({'ref':ref, 'label':f"{row['activity_type']} — {ref}", 'kind':'activity'})
                if len(refs)>=5:
                    break
            trips = conn.execute("""SELECT id,mode FROM demo_commutes
                 WHERE account_id=? ORDER BY day DESC LIMIT 2""", (person['id'],)).fetchall()
            refs.extend({'ref':f"trip-demo-{x['id']}", 'label':f"{x['mode']} — trip-demo-{x['id']}",
                         'kind':'commute'} for x in trips)
            result.append({'employee_id':person['employee_id'], 'name':person['display_name'], 'items':refs})
    return {'profiles':result, 'notice':'Profils strictement fictifs, distincts du référentiel RH des 161 salariés.'}
