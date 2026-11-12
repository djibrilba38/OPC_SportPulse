"""Édition/suppression logique des activités et synchronisation Slack différée.

- Contrôle serveur sur l'identité du salarié, CSRF, version optimiste.
- Suppression logique + journal d'audit. Les activités validées RH sont verrouillées.
- Les messages Slack déjà publiés sont mis à jour/supprimés via une outbox;
  ne jamais publier à nouveau le message initial d'une activité modifiée.
- Les opérations qui échouent sont reprises par le worker (at-least-once).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from sportpulse import employee_portal as portal
from sportpulse import activity_notifications as notifications

router = APIRouter(tags=["activities-management"])
logger = logging.getLogger(__name__)


def demo_setup(conn: sqlite3.Connection):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(demo_activities)")}
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE demo_activities ADD COLUMN deleted_at INTEGER")
    if "revision" not in columns:
        conn.execute("ALTER TABLE demo_activities ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE demo_activities ADD COLUMN updated_at INTEGER")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sportpulse_activity_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            activity_id INTEGER NOT NULL,
            operation TEXT NOT NULL CHECK(operation IN ('update','delete')),
            revision INTEGER NOT NULL,
            performed_at INTEGER NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT
        );
        CREATE TABLE IF NOT EXISTS sportpulse_activity_slack_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('update','delete')),
            channel TEXT NOT NULL,
            slack_ts TEXT NOT NULL,
            payload_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL DEFAULT 0,
            claimed_at INTEGER,
            created_at INTEGER NOT NULL,
            last_error TEXT,
            UNIQUE(activity_id, revision)
        );
        CREATE INDEX IF NOT EXISTS ix_sp_slack_actions_due
          ON sportpulse_activity_slack_actions(status,next_attempt_at);
    """)
    # Prepares the original Slack outbox for accounts that have never shared an activity.
    notifications.initialize_demo_schema(conn)


def _user(session):
    return portal.current_account(session)


def _csrf(request, user, csrf):
    portal.require_csrf(request, user, csrf)


def _date(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise HTTPException(422, detail="Inclure un fuseau horaire.")
    started = value.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if started > now + timedelta(minutes=2) or started < now - timedelta(days=366*2):
        raise HTTPException(422, detail="Date hors plage autorisée.")
    return started


def _activity_id(value: str) -> int:
    try:
        parsed = int(value.removeprefix("demo-").removeprefix("decl-"))
        if parsed < 1:
            raise ValueError()
        return parsed
    except ValueError as exc:
        raise HTTPException(422, detail="Identifiant d'activité invalide") from exc


class ActivityEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    activity_type: str
    started_at: datetime
    duration_min: int = Field(ge=1, le=1440)
    distance_km: float | None = Field(default=None, ge=0, le=500)
    comment: str = Field(default="", max_length=350)


def _validate(body: ActivityEdit) -> datetime:
    if body.activity_type not in portal.ALLOWED_SPORTS:
        raise HTTPException(422, detail="Sport non reconnu")
    return _date(body.started_at)


def _payload(user: dict, body: ActivityEdit, started: datetime):
    return notifications.event_payload(
        display_name=user["display_name"], activity_type=body.activity_type,
        started_at=started.isoformat(), duration_min=body.duration_min,
        distance_km=body.distance_km, is_demo=bool(user["is_demo"]),
    )


def _schedule_demo_action(conn, activity_id: int, revision: int, action: str, row, payload=None):
    """If Slack published, enqueue correction. Otherwise cancel/rewrite original message."""
    original = conn.execute("SELECT status,slack_channel,slack_ts FROM sportpulse_notifications_outbox WHERE activity_id=?",(activity_id,)).fetchone()
    if original is None:
        return "not_shared"
    if original["status"] == "processing":
        raise HTTPException(409, detail="Publication Slack en cours. Réessaie dans quelques secondes.")
    if original["status"] in ("pending", "retry"):
        if action == "delete":
            conn.execute("UPDATE sportpulse_notifications_outbox SET status='failed',last_error='activity_deleted' WHERE activity_id=?", (activity_id,))
            return "publication_canceled"
        conn.execute("UPDATE sportpulse_notifications_outbox SET payload_json=?,message_text=NULL WHERE activity_id=?",
                     (json.dumps(payload,ensure_ascii=False), activity_id))
        return "original_message_updated"
    if original["status"] == "sent" and original["slack_ts"] and original["slack_channel"]:
        # Cancel obsolete pending edits; retain history of the superseded actions.
        conn.execute("""UPDATE sportpulse_activity_slack_actions
           SET status='superseded' WHERE activity_id=? AND status IN ('pending','retry')""",(activity_id,))
        conn.execute("""INSERT INTO sportpulse_activity_slack_actions
          (activity_id,revision,action,channel,slack_ts,payload_json,created_at)
          VALUES(?,?,?,?,?,?,?)""",
          (activity_id,revision,action,original["slack_channel"],original["slack_ts"],
           json.dumps(payload,ensure_ascii=False) if payload else None,int(time.time())))
        return "slack_correction_queued"
    return "no_external_change"


def demo_update(user: dict, activity_id: int, body: ActivityEdit) -> dict:
    started=_validate(body)
    with portal.connect() as conn:
        demo_setup(conn)
        row=conn.execute("SELECT * FROM demo_activities WHERE id=? AND account_id=? AND deleted_at IS NULL",
                         (activity_id,user["id"])).fetchone()
        if row is None:
            raise HTTPException(404,detail="Activité introuvable")
        if row["revision"] != body.revision:
            raise HTTPException(409,detail="Activité modifiée depuis son dernier affichage. Actualise la page.")
        # No changes if the outbox is processing; check *before* changing the activity.
        existing=conn.execute("SELECT status FROM sportpulse_notifications_outbox WHERE activity_id=?",(activity_id,)).fetchone()
        if existing is not None and existing[0]=="processing":
            raise HTTPException(409,detail="Publication Slack en cours; réessaie.")
        updated=conn.execute("""UPDATE demo_activities SET activity_type=?,started_at=?,duration_min=?,
          distance_km=?,comment=?,updated_at=?,revision=revision+1
          WHERE id=? AND account_id=? AND revision=? AND deleted_at IS NULL""",
          (body.activity_type,started.isoformat(),body.duration_min,body.distance_km,
           body.comment,int(time.time()),activity_id,user["id"],body.revision))
        if updated.rowcount!=1:
            raise HTTPException(409,detail="Modification concurrente")
        payload=_payload(user,body,started)
        slack=_schedule_demo_action(conn,activity_id,body.revision+1,"update",row,payload)
        conn.execute("""INSERT INTO sportpulse_activity_audit
           (account_id,activity_id,operation,revision,performed_at,before_json,after_json)
           VALUES(?,?,?,?,?,?,?)""",
           (user["id"],activity_id,"update",body.revision+1,int(time.time()),
            json.dumps(dict(row),default=str),body.model_dump_json()))
    return {"id":f"demo-{activity_id}","revision":body.revision+1,"status":"updated", "slack_sync":slack}


def demo_delete(user: dict, activity_id: int, revision: int) -> dict:
    with portal.connect() as conn:
        demo_setup(conn)
        row=conn.execute("SELECT * FROM demo_activities WHERE id=? AND account_id=? AND deleted_at IS NULL",(activity_id,user["id"])).fetchone()
        if row is None:
            raise HTTPException(404,detail="Activité introuvable")
        if row["revision"]!=revision:
            raise HTTPException(409,detail="Version dépassée. Actualise la page.")
        existing=conn.execute("SELECT status FROM sportpulse_notifications_outbox WHERE activity_id=?",(activity_id,)).fetchone()
        if existing is not None and existing[0]=="processing":
            raise HTTPException(409,detail="Publication Slack en cours; réessaie.")
        conn.execute("""UPDATE demo_activities SET deleted_at=?,updated_at=?,revision=revision+1
           WHERE id=? AND account_id=? AND revision=? AND deleted_at IS NULL""",
           (int(time.time()),int(time.time()),activity_id,user["id"],revision))
        slack=_schedule_demo_action(conn,activity_id,revision+1,"delete",row)
        conn.execute("""INSERT INTO sportpulse_activity_audit
           (account_id,activity_id,operation,revision,performed_at,before_json,after_json)
           VALUES(?,?,?,?,?,?,NULL)""",
           (user["id"],activity_id,"delete",revision+1,int(time.time()),json.dumps(dict(row),default=str)))
    return {"id":f"demo-{activity_id}","status":"deleted", "slack_sync":slack}


def _real_connect():
    import psycopg
    dsn=os.getenv("DATABASE_URL","")
    if not dsn:
        raise HTTPException(503,detail="DATABASE_URL absent")
    return psycopg.connect(dsn,connect_timeout=4)


def real_update(user: dict, activity_id: int, body: ActivityEdit) -> dict:
    started=_validate(body)
    import psycopg
    try:
        with _real_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT employee_id,activity_type,activity_date,duration_min,validation_status,
                  portal_revision,deleted_at FROM benefits.wellbeing_activity_declarations
                  WHERE activity_id=%s FOR UPDATE""",(activity_id,))
                old=cur.fetchone()
                if old is None or old[0]!=user["employee_id"] or old[6] is not None:
                    raise HTTPException(404,detail="Activité introuvable")
                if old[4]!="pending":
                    raise HTTPException(409,detail="Activité validée/rejetée : demande de correction aux RH requise")
                if old[5]!=body.revision:
                    raise HTTPException(409,detail="Version dépassée. Actualise la page.")
                cur.execute("SELECT status FROM benefits.sportpulse_notifications_outbox WHERE activity_id=%s FOR UPDATE",(activity_id,))
                n=cur.fetchone()
                if n and n[0]=="processing":
                    raise HTTPException(409,detail="Publication Slack en cours")
                cur.execute("""UPDATE benefits.wellbeing_activity_declarations
                  SET activity_type=%s,activity_date=%s,duration_min=%s,started_at=%s,
                      distance_km=%s,comment=%s,portal_revision=portal_revision+1,updated_at=now()
                  WHERE activity_id=%s""",(body.activity_type,started.date(),body.duration_min,
                    started,body.distance_km,body.comment,activity_id))
                payload=_payload(user,body,started)
                slack=_schedule_real_action(cur,activity_id,body.revision+1,"update",payload)
                cur.execute("""INSERT INTO benefits.sportpulse_activity_audit
                  (activity_id,employee_id,action,revision,before_json,after_json)
                  VALUES(%s,%s,'update',%s,%s::jsonb,%s::jsonb)""",
                  (activity_id,user["employee_id"],body.revision+1,
                   json.dumps({"activity_type":old[1],"activity_date":str(old[2]),"duration_min":old[3]}),body.model_dump_json()))
    except psycopg.Error as exc:
        raise HTTPException(503,detail="Migrations PostgreSQL de l'étape 7 requises") from exc
    return {"id":f"decl-{activity_id}","revision":body.revision+1,"status":"updated_pending_hr_review","slack_sync":slack}


def real_delete(user: dict,activity_id:int,revision:int)->dict:
    import psycopg
    try:
        with _real_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT employee_id,activity_type,activity_date,duration_min,validation_status,
                   portal_revision,deleted_at FROM benefits.wellbeing_activity_declarations
                   WHERE activity_id=%s FOR UPDATE""",(activity_id,))
                row=cur.fetchone()
                if row is None or row[0]!=user["employee_id"] or row[6] is not None:
                    raise HTTPException(404,detail="Activité introuvable")
                if row[4]!="pending":
                    raise HTTPException(409,detail="Activité traitée par les RH, suppression réservée à l'équipe RH")
                if row[5]!=revision:
                    raise HTTPException(409,detail="Version dépassée")
                cur.execute("SELECT status FROM benefits.sportpulse_notifications_outbox WHERE activity_id=%s FOR UPDATE",(activity_id,))
                n=cur.fetchone()
                if n and n[0]=="processing":
                    raise HTTPException(409,detail="Publication Slack en cours")
                cur.execute("""UPDATE benefits.wellbeing_activity_declarations
                   SET deleted_at=now(),portal_revision=portal_revision+1,updated_at=now(),validation_status='rejected'
                   WHERE activity_id=%s""",(activity_id,))
                slack=_schedule_real_action(cur,activity_id,revision+1,"delete")
                cur.execute("""INSERT INTO benefits.sportpulse_activity_audit
                    (activity_id,employee_id,action,revision,before_json)
                    VALUES(%s,%s,'delete',%s,%s::jsonb)""",
                    (activity_id,user["employee_id"],revision+1,
                     json.dumps({"activity_type":row[1],"activity_date":str(row[2]),"duration_min":row[3]})))
    except psycopg.Error as exc:
        raise HTTPException(503,detail="Migrations PostgreSQL de l'étape 7 requises") from exc
    return {"id":f"decl-{activity_id}","status":"deleted","slack_sync":slack}


def _schedule_real_action(cur, activity_id: int, revision: int, action: str, payload=None) -> str:
    cur.execute("SELECT status,slack_channel,slack_ts FROM benefits.sportpulse_notifications_outbox WHERE activity_id=%s FOR UPDATE",(activity_id,))
    row=cur.fetchone()
    if not row:
        return "not_shared"
    if row[0] in ("pending","retry"):
        if action=="delete":
            cur.execute("UPDATE benefits.sportpulse_notifications_outbox SET status='failed',last_error='activity_deleted' WHERE activity_id=%s",(activity_id,))
            return "publication_canceled"
        cur.execute("UPDATE benefits.sportpulse_notifications_outbox SET payload_json=%s::jsonb,message_text=NULL WHERE activity_id=%s",
                    (json.dumps(payload,ensure_ascii=False),activity_id))
        return "original_message_updated"
    if row[0]=="processing":
        raise HTTPException(409,detail="Publication Slack en cours")
    if row[0]=="sent" and row[1] and row[2]:
        cur.execute("""UPDATE benefits.sportpulse_activity_slack_actions SET status='superseded'
          WHERE activity_id=%s AND status IN ('pending','retry')""",(activity_id,))
        cur.execute("""INSERT INTO benefits.sportpulse_activity_slack_actions
          (activity_id,revision,action,channel,slack_ts,payload_json)
          VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
          (activity_id,revision,action,row[1],row[2],json.dumps(payload) if payload else None))
        return "slack_correction_queued"
    return "no_external_change"


@router.patch("/employee/api/activities/{activity_id}")
def edit_activity(activity_id: str,body:ActivityEdit,request:Request,
                  session:Annotated[str|None,Cookie(alias=portal.COOKIE_NAME)]=None,
                  x_csrf_token:Annotated[str|None,Header(alias="X-CSRF-Token")]=None):
    user=_user(session)
    _csrf(request,user,x_csrf_token)
    numeric=_activity_id(activity_id)
    return demo_update(user,numeric,body) if user["is_demo"] else real_update(user,numeric,body)


@router.delete("/employee/api/activities/{activity_id}")
def remove_activity(activity_id:str,request:Request,revision:int=1,
                    session:Annotated[str|None,Cookie(alias=portal.COOKIE_NAME)]=None,
                    x_csrf_token:Annotated[str|None,Header(alias="X-CSRF-Token")]=None):
    user=_user(session)
    _csrf(request,user,x_csrf_token)
    numeric=_activity_id(activity_id)
    return demo_delete(user,numeric,revision) if user["is_demo"] else real_delete(user,numeric,revision)


def _slack_api(action:str,channel:str,ts:str,text:str|None=None) -> None:
    import httpx
    token=os.getenv("SLACK_BOT_TOKEN","")
    if not token:
        raise RuntimeError("Slack bot token absent")
    endpoint="chat.update" if action=="update" else "chat.delete"
    body={"channel":channel,"ts":ts}
    if action=="update":
        body["text"]=text
    response=httpx.post(f"https://slack.com/api/{endpoint}",
        headers={"Authorization":f"Bearer {token}"},json=body,timeout=10)
    response.raise_for_status()
    data=response.json()
    # SPORTPULSE_STEP12_DELETE_SLACK_NOTICE — chat.delete seul ne notifie personne.
    if not data.get("ok"):
        err = str(data.get("error", "unknown"))
        if not (action == "delete" and err == "message_not_found"):
            raise RuntimeError("Slack " + err[:60])
    if action == "delete":
        # Publication séparée sans identité ni données RH. En cas d'échec, le worker réessaie.
        notice = httpx.post("https://slack.com/api/chat.postMessage",
            headers={"Authorization":f"Bearer {token}"},
            json={"channel":channel,"text":"🗑️ Une activité SportPulse précédemment partagée a été supprimée. Les indicateurs seront recalculés."},
            timeout=10)
        notice.raise_for_status()
        ack = notice.json()
        if not ack.get("ok"):
            raise RuntimeError("Notification de suppression Slack : " + str(ack.get("error", "unknown"))[:60])


def process_demo_corrections(limit:int=20)->dict[str,int]:
    from sportpulse.employee_portal import connect
    if os.getenv("SPORTPULSE_SLACK_MODE","dry_run")!="live":
        return {}
    with connect() as conn:
        demo_setup(conn)
        now=int(time.time())
        conn.execute("""UPDATE sportpulse_activity_slack_actions SET status='retry'
          WHERE status='processing' AND claimed_at<?""",(now-180,))
        due=[r[0] for r in conn.execute("""SELECT id FROM sportpulse_activity_slack_actions
          WHERE status IN ('pending','retry') AND next_attempt_at<=? ORDER BY id LIMIT ?""",(now,limit))]
    results={}
    for id_ in due:
        with connect() as conn:
            demo_setup(conn)
            row=conn.execute("""UPDATE sportpulse_activity_slack_actions
              SET status='processing',attempts=attempts+1,claimed_at=?
              WHERE id=? AND status IN ('pending','retry')
              RETURNING *""",(int(time.time()),id_)).fetchone()
        if row is None: continue
        try:
            payload=json.loads(row["payload_json"]) if row["payload_json"] else None
            message=notifications.format_message(payload) if row["action"]=="update" else None
            _slack_api(row["action"],row["channel"],row["slack_ts"],message)
            with connect() as conn:
                conn.execute("UPDATE sportpulse_activity_slack_actions SET status='sent',last_error=NULL WHERE id=?",(id_,))
            results["sent"]=results.get("sent",0)+1
        except Exception as exc:
            attempts=int(row["attempts"])
            next_at=int(time.time())+min(300,2**attempts)
            with connect() as conn:
                conn.execute("""UPDATE sportpulse_activity_slack_actions SET status=?,next_attempt_at=?,last_error=? WHERE id=?""",
                 ("failed" if attempts>=6 else "retry",next_at,type(exc).__name__+":"+str(exc)[:80],id_))
            results["retry_or_failed"]=results.get("retry_or_failed",0)+1
    return results


def process_real_corrections(limit:int=20)->dict[str,int]:
    if os.getenv("SPORTPULSE_SLACK_MODE","dry_run")!="live" or not os.getenv("DATABASE_URL"):
        return {}
    import psycopg
    results={}
    try:
        with _real_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE benefits.sportpulse_activity_slack_actions SET status='retry'
                  WHERE status='processing' AND claimed_at<now()-interval '3 minutes'""")
                cur.execute("""SELECT id FROM benefits.sportpulse_activity_slack_actions
                  WHERE status IN ('pending','retry') AND next_attempt_at<=now()
                  ORDER BY id LIMIT %s""",(limit,))
                ids=[r[0] for r in cur.fetchall()]
        for id_ in ids:
            with _real_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE benefits.sportpulse_activity_slack_actions
                      SET status='processing',attempts=attempts+1,claimed_at=now()
                      WHERE id=%s AND status IN ('pending','retry') RETURNING action,channel,slack_ts,payload_json,attempts""",(id_,))
                    row=cur.fetchone()
            if row is None: continue
            action,channel,ts,payload,attempts=row
            try:
                obj=payload if isinstance(payload,dict) else json.loads(payload) if payload else None
                msg=notifications.format_message(obj) if action=="update" else None
                _slack_api(action,channel,ts,msg)
                with _real_connect() as conn:
                    conn.execute("UPDATE benefits.sportpulse_activity_slack_actions SET status='sent',last_error=NULL WHERE id=%s",(id_,))
                results["sent"]=results.get("sent",0)+1
            except Exception as exc:
                with _real_connect() as conn:
                    conn.execute("""UPDATE benefits.sportpulse_activity_slack_actions
                      SET status=%s,next_attempt_at=now()+(%s * interval '1 second'),last_error=%s WHERE id=%s""",
                      ("failed" if attempts>=6 else "retry",min(300,2**attempts),type(exc).__name__+":"+str(exc)[:80],id_))
                results["retry_or_failed"]=results.get("retry_or_failed",0)+1
    except psycopg.Error as exc:
        logger.warning("Sync corrections Slack PostgreSQL indisponible: %s",type(exc).__name__)
    return results


def process_corrections()->dict[str,int]:
    counts=process_demo_corrections()
    for k,v in process_real_corrections().items():
        counts[k]=counts.get(k,0)+v
    return counts


def augment_real_dashboard(data:dict,user:dict,year:int)->dict:
    """Expose new declarations in the live portal; does not grant RH benefits."""
    start,end=portal.social_window(year)
    if user["is_demo"]:
        return data
    try:
        with _real_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT activity_id,activity_type,activity_date,duration_min,started_at,
                  distance_km,comment,portal_revision,validation_status
                  FROM benefits.wellbeing_activity_declarations
                  WHERE employee_id=%s AND activity_date>=%s AND activity_date<%s
                    AND deleted_at IS NULL
                  ORDER BY activity_date DESC LIMIT 500""",(user["employee_id"],start,end))
                rows=cur.fetchall()
    except Exception as exc:
        logger.warning("Activités de déclarations non disponibles: %s",type(exc).__name__)
        return data
    entries=[]
    for r in rows:
        entries.append({"id":f"decl-{r[0]}","activity_type":r[1],"label":portal.ALLOWED_SPORTS.get(r[1],r[1]),
         "started_at":r[4].isoformat() if r[4] else str(r[2])+"T12:00:00+00:00",
         "duration_min":r[3],"distance_km":float(r[5]) if r[5] is not None else None,
         "comment":r[6] or "","revision":r[7],"editable":r[8]=="pending", "validation_status":r[8]})
    # Historical dbt facts may already contain some of these declarations. For the
    # initial POC show explicit declarations separately; do not double-count them as validated.
    data["portal_declarations"]=entries
    data["activities"]=entries+data.get("activities",[])
    data["activities"].sort(key=lambda row:row["started_at"],reverse=True)
    data["notices"].append("Déclarations saisies dans le portail affichées séparément de l'historique validé; résultats RH inchangés.")
    return data
