"""SportPulse étape 12 — preuve courte émise par RH, à usage unique.

Important : un QR/code est une preuve de présentation d'un code remis par RH,
PAS une preuve indépendante de la pratique sportive ni du trajet réel.
L'édition/suppression invalide la preuve par révision.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import logging
import os
import secrets
import sqlite3
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from sportpulse import employee_portal as employee
from sportpulse import admin_portal as admin

router = APIRouter(tags=["preuve-activite-et-trajet"])
log = logging.getLogger(__name__)
TTL_SECONDS = 300


def _secret() -> bytes:
    value = os.getenv("SPORTPULSE_VALIDATION_SECRET", "")
    if len(value) < 32:
        raise HTTPException(503, detail="SPORTPULSE_VALIDATION_SECRET doit avoir au moins 32 caractères.")
    return value.encode("utf-8")


def _digest(code: str) -> str:
    return hmac.new(_secret(), code.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def _setup(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS sportpulse_proof_codes (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       employee_id TEXT NOT NULL,
       activity_ref TEXT NOT NULL,
       kind TEXT NOT NULL CHECK(kind IN ('activity','commute')),
       activity_revision INTEGER NOT NULL,
       digest TEXT NOT NULL,
       issued_by_id INTEGER NOT NULL,
       issued_at INTEGER NOT NULL,
       expires_at INTEGER NOT NULL,
       consumed_at INTEGER,
       attempts INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_sp_proofs_activity ON sportpulse_proof_codes(employee_id,activity_ref,consumed_at)")


def _split_ref(ref: str, kind: str) -> tuple[bool, int]:
    if kind == "activity":
        prefix = "demo-" if ref.startswith("demo-") else "decl-"
        if not ref.startswith(prefix):
            raise HTTPException(422, "Identifiant d'activité invalide.")
    else:
        prefix = "trip-demo-" if ref.startswith("trip-demo-") else "trip-real-"
        if not ref.startswith(prefix):
            raise HTTPException(422, "Identifiant de trajet invalide.")
    try:
        n = int(ref[len(prefix):])
        if n <= 0: raise ValueError
    except ValueError:
        raise HTTPException(422, "Identifiant invalide.")
    return prefix in ("demo-", "trip-demo-"), n


def _target(conn: sqlite3.Connection, employee_id: str, ref: str, kind: str) -> tuple[int, str]:
    """Return current revision and status; enforce owner, not trusting user input."""
    demo, ident = _split_ref(ref, kind)
    if demo:
        table = "demo_activities" if kind == "activity" else "demo_commutes"
        account = conn.execute("SELECT id,is_demo FROM accounts WHERE employee_id=?", (employee_id,)).fetchone()
        if not account or not account["is_demo"]:
            raise HTTPException(404, "Élément introuvable.")
        if kind == "activity":
            row = conn.execute("""SELECT revision, deleted_at FROM demo_activities
                WHERE id=? AND account_id=?""", (ident, account["id"])).fetchone()
            if not row or row["deleted_at"] is not None:
                raise HTTPException(404, "Activité supprimée ou introuvable.")
            status = "validated" if _already_validated(conn, employee_id, ref, row["revision"]) else "pending"
            return int(row["revision"]), status
        row = conn.execute("SELECT verification_status FROM demo_commutes WHERE id=? AND account_id=?",
                           (ident, account["id"])).fetchone()
        if not row: raise HTTPException(404, "Trajet introuvable.")
        return 1, str(row[0])
    import psycopg
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn: raise HTTPException(503, "Connexion RH indisponible.")
    try:
        with psycopg.connect(dsn, connect_timeout=5) as db:
            with db.cursor() as cur:
                if kind == "activity":
                    cur.execute("""SELECT portal_revision,validation_status,deleted_at
                        FROM benefits.wellbeing_activity_declarations
                        WHERE activity_id=%s AND employee_id=%s""", (ident, employee_id))
                    row = cur.fetchone()
                    if not row or row[2] is not None: raise HTTPException(404, "Activité introuvable.")
                    return int(row[0]), str(row[1])
                cur.execute("""SELECT validation_status FROM benefits.sport_commute_declarations
                    WHERE commute_id=%s AND employee_id=%s""", (ident, employee_id))
                row = cur.fetchone()
                if not row: raise HTTPException(404, "Trajet introuvable.")
                return 1, str(row[0])
    except psycopg.Error as exc:
        log.warning("Cible RH indisponible: %s", type(exc).__name__)
        raise HTTPException(503, "Connexion RH indisponible.") from exc


def _already_validated(conn: sqlite3.Connection, employee_id: str, ref: str, revision: int) -> bool:
    row = conn.execute("""SELECT 1 FROM sportpulse_proof_codes
      WHERE employee_id=? AND activity_ref=? AND activity_revision=?
        AND consumed_at IS NOT NULL LIMIT 1""", (employee_id, ref, revision)).fetchone()
    return row is not None


class IssueProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str = Field(min_length=1, max_length=128)
    activity_ref: str = Field(min_length=3, max_length=80)
    kind: Literal["activity", "commute"] = "activity"


class RedeemProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_ref: str = Field(min_length=3, max_length=80)
    code: str = Field(min_length=6, max_length=80)


@router.post("/admin/api/proofs/issue")
def issue_proof(body: IssueProof, request: Request,
    session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    actor = admin.current_admin(session, required_role="admin")
    admin.require_csrf(request, actor, csrf)
    _secret()
    with employee.connect() as conn:
        _setup(conn)
        if body.activity_ref.startswith("demo-") and body.kind == "activity":
            from sportpulse.activity_management import demo_setup
            demo_setup(conn)
        revision, status = _target(conn, body.employee_id, body.activity_ref, body.kind)
        if body.kind == "activity" and status != "pending":
            raise HTTPException(409, "Cette activité n'est plus en attente de validation.")
        if body.kind == "commute" and status == "rejected":
            raise HTTPException(409, "Trajet rejeté : vérification RH nécessaire.")
        now = int(time.time())
        # Supprime les anciens codes non utilisés, sans effacer l'historique d'audit.
        conn.execute("UPDATE sportpulse_proof_codes SET expires_at=? WHERE employee_id=? AND activity_ref=? AND consumed_at IS NULL",
                     (now, body.employee_id, body.activity_ref))
        # Code court facile à saisir, limité en durée + tentatives; jamais journalisé.
        code = f"{secrets.randbelow(10**8):08d}"
        cur = conn.execute("""INSERT INTO sportpulse_proof_codes
         (employee_id,activity_ref,kind,activity_revision,digest,issued_by_id,issued_at,expires_at)
         VALUES (?,?,?,?,?,?,?,?)""", (body.employee_id, body.activity_ref, body.kind,
           revision, _digest(code), actor["id"], now, now + TTL_SECONDS))
        proof_id = cur.lastrowid
    import qrcode
    out = io.BytesIO()
    qrcode.make(code).save(out, "PNG")
    return {"proof_id": proof_id, "activity_ref": body.activity_ref,
            "kind": body.kind, "code": code, "expires_in_seconds": TTL_SECONDS,
            "qr_data_url": "data:image/png;base64,"+base64.b64encode(out.getvalue()).decode("ascii"),
            "notice": "Code de démonstration transmis par RH après vérification; ne prouve pas à lui seul la pratique sportive."}


@router.post("/employee/api/proofs/redeem")
def redeem_proof(body: RedeemProof, request: Request,
    session: Annotated[str | None, Cookie(alias=employee.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    user = employee.current_account(session)
    employee.require_csrf(request, user, csrf)
    _secret()
    normalized = body.code.strip()
    if normalized.startswith("sportpulse:"):
        normalized = normalized.split(":", 1)[1]
    if len(normalized) != 8 or not normalized.isascii() or not normalized.isdigit():
        raise HTTPException(422, "Code de validation invalide (8 chiffres attendus).")
    now = int(time.time())
    with employee.connect() as conn:
        _setup(conn)
        # Complete any schema migration before opening the exclusive transaction.
        target_kind = "commute" if body.activity_ref.startswith("trip-") else "activity"
        if user["is_demo"] and target_kind == "activity":
            from sportpulse.activity_management import demo_setup
            demo_setup(conn)
        # Exclusive SQLite reservation, guarding one-time consumption.
        conn.execute("BEGIN IMMEDIATE")
        revision, status = _target(conn, user["employee_id"], body.activity_ref, target_kind)
        if status == "rejected":
            raise HTTPException(409, "Élément rejeté, contacte les RH.")
        row = conn.execute("""SELECT * FROM sportpulse_proof_codes
           WHERE employee_id=? AND activity_ref=? AND kind=? AND consumed_at IS NULL
           ORDER BY id DESC LIMIT 1""", (user["employee_id"], body.activity_ref, target_kind)).fetchone()
        if not row or row["expires_at"] < now:
            raise HTTPException(403, "Code absent ou expiré. Demande un nouveau code RH.")
        if row["attempts"] >= 5:
            raise HTTPException(429, "Trop d'essais. Demande un nouveau code RH.")
        if revision != row["activity_revision"]:
            raise HTTPException(409, "Activité modifiée : un nouveau code RH est nécessaire.")
        if not hmac.compare_digest(row["digest"], _digest(normalized)):
            conn.execute("UPDATE sportpulse_proof_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
            conn.commit()
            raise HTTPException(403, "Code incorrect.")
        if target_kind == "activity" and status != "pending":
            raise HTTPException(409, "Activité déjà validée ou traitée par les RH.")
        if user["is_demo"]:
            if target_kind == "commute":
                ident = int(body.activity_ref.removeprefix("trip-demo-"))
                conn.execute("""UPDATE demo_commutes SET verification_status='validated'
                  WHERE id=? AND account_id=?""", (ident, user["id"]))
            result = "validated_demo"
        else:
            import psycopg
            try:
                with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as pg:
                    with pg.cursor() as cur:
                        if target_kind == "activity":
                            cur.execute("""UPDATE benefits.wellbeing_activity_declarations
                              SET validation_status='validated', reviewed_by=%s,reviewed_at=now()
                              WHERE activity_id=%s AND employee_id=%s AND validation_status='pending'
                                AND outside_work=TRUE AND deleted_at IS NULL AND portal_revision=%s
                              RETURNING activity_id""",
                              (f"admin-code:{row['issued_by_id']}", int(body.activity_ref.removeprefix("decl-")),
                               user["employee_id"], revision))
                        else:
                            # Arrival code ≠ proof of actual route/mode. RH still must approve commute.
                            cur.execute("""UPDATE benefits.sport_commute_declarations
                              SET office_arrival_confirmed=TRUE
                              WHERE commute_id=%s AND employee_id=%s AND validation_status='pending'
                              RETURNING commute_id""", (int(body.activity_ref.removeprefix("trip-real-")), user["employee_id"]))
                        if not cur.fetchone():
                            raise HTTPException(409, "Élément RH modifié entre-temps. Actualise la page.")
                result = "validated_activity" if target_kind == "activity" else "arrival_confirmed_pending_rh"
            except psycopg.Error as exc:
                raise HTTPException(503, "Impossible d'enregistrer la validation RH.") from exc
        conn.execute("UPDATE sportpulse_proof_codes SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, row["id"]))
    return {"activity_ref": body.activity_ref, "status": result,
        "notice": "Le trajet reste soumis aux contrôles de distance et à la décision RH." if target_kind=="commute" else
        "Activité validée par code RH. Pour un droit officiel, les autres conditions RH restent applicables."}


def gate_dashboard(data: dict, user: dict, year: int) -> dict:
    """Expose all declarations but calculate sport/benefit counters only from verified declarations.
    Historical 1803 simulated dbt events remain visible and NON admissible.
    """
    entries = data.get("activities", [])
    with employee.connect() as conn:
        _setup(conn)
        verified = {}
        for r in conn.execute("""SELECT activity_ref,activity_revision FROM sportpulse_proof_codes
          WHERE employee_id=? AND kind='activity' AND consumed_at IS NOT NULL""", (user["employee_id"],)):
            verified.setdefault(r["activity_ref"], set()).add(r["activity_revision"])
        arrival_proofs = {r[0] for r in conn.execute("""SELECT activity_ref FROM sportpulse_proof_codes
          WHERE employee_id=? AND kind='commute' AND consumed_at IS NOT NULL""", (user["employee_id"],))}
    for activity in entries:
        ref = str(activity.get("id", ""))
        if user["is_demo"] and not ref.startswith("demo-"):
            ref = "demo-" + ref
            activity["id"] = ref
        rev = int(activity.get("revision", 1))
        if user["is_demo"]:
            valid = rev in verified.get(ref, set())
        else:
            valid = (ref.startswith("decl-") and activity.get("validation_status") == "validated"
                     and rev in verified.get(ref, set()))
        activity["counted_for_benefits"] = valid
        activity["validation_status"] = "validated" if valid else ("pending" if user["is_demo"] else activity.get("validation_status", "historique_non_validé"))
        if valid:
            # Employee cannot self edit a verified activity without RH intervention.
            activity["editable"] = False
    counted = [a for a in entries if a.get("counted_for_benefits")]
    data["sports"]["declared_count"] = len(entries)
    data["sports"]["count"] = len(counted)
    data["sports"]["distance_km"] = round(sum(float(a.get("distance_km") or 0) for a in counted), 1)
    data["sports"]["duration_min"] = sum(int(a.get("duration_min") or 0) for a in counted)
    data["sports"]["score"] = min(100, round(min(len(counted), 15) / 15 * 100))
    data["sports"]["score_label"] = "Progression indicative des activités vérifiées, sans attribution automatique"
    if user["is_demo"]:
        data["benefits"]["days_potential"] = 5 if len(counted) >= 15 else 0
        data["benefits"]["bonus_estimate_eur"] = None
        data["benefits"]["bonus_status"] = "non_attribuée_code_et_trajets_à_valider"
    data["notices"].append("Seules les activités validées par les RH/QR ou code sont comptabilisées dans la progression.")
    data["sports"]["pending_count"] = len(entries) - len(counted)
    for trip in data.get("commutes", []):
        trip["proof_ref"] = ("trip-demo-" if user["is_demo"] else "trip-real-") + str(trip.get("id"))
        trip["arrival_code_verified"] = trip["proof_ref"] in arrival_proofs
    return data


def is_demo_activity_verified(conn, user_id: int, activity_id: int, revision: int) -> bool:
    _setup(conn)
    row = conn.execute("SELECT employee_id FROM accounts WHERE id=?", (user_id,)).fetchone()
    return bool(row and _already_validated(conn, row["employee_id"], f"demo-{activity_id}", revision))

@router.get("/employee/api/map-context")
def map_context(session: Annotated[str | None, Cookie(alias=employee.COOKIE_NAME)] = None):
    employee.current_account(session)
    try:
        lat = float(os.environ["OFFICE_LATITUDE"])
        lon = float(os.environ["OFFICE_LONGITUDE"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180): raise ValueError()
        office = {"lat": lat, "lon": lon}
    except (KeyError, ValueError):
        office = None
    return {"office": office, "gps_history_available": False,
            "note": "Les traces GPS ne sont pas conservées ; la carte n'affiche pas de trajet historique."}

class NewTrip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str = Field(min_length=10, max_length=10)
    transport_mode: Literal["bicycle", "scooter", "running", "walking"]
    distance_km: float | None = Field(default=None, ge=0.1, le=150)


@router.post("/employee/api/trips", status_code=201)
def create_trip(body: NewTrip, request: Request,
    session: Annotated[str | None, Cookie(alias=employee.COOKIE_NAME)] = None,
    csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    """Déclaration non vérifiée, qui ne constitue pas une preuve de déplacement sportif."""
    from datetime import date, timedelta
    user = employee.current_account(session)
    employee.require_csrf(request, user, csrf)
    try:
        day = date.fromisoformat(body.day)
    except ValueError:
        raise HTTPException(422, "Date de trajet invalide.")
    today = date.today()
    if day > today or day < today - timedelta(days=365*2):
        raise HTTPException(422, "Date de trajet hors plage.")
    if user["is_demo"]:
        with employee.connect() as conn:
            cur=conn.execute("""INSERT INTO demo_commutes
              (account_id,day,mode,distance_km,verification_status)
              VALUES (?,?,?,?, 'pending')""",
              (user["id"], day.isoformat(), body.transport_mode, body.distance_km or 0.1))
            ident=cur.lastrowid
        return {"ref":f"trip-demo-{ident}", "status":"pending", "message":"Trajet déclaré, à vérifier par code RH."}
    import psycopg
    try:
        with psycopg.connect(os.environ["DATABASE_URL"],connect_timeout=5) as pg:
            with pg.cursor() as cur:
                cur.execute("""INSERT INTO benefits.sport_commute_declarations
                    (employee_id,commute_date,transport_mode,office_arrival_confirmed,validation_status)
                    VALUES (%s,%s,%s,FALSE,'pending') RETURNING commute_id""",
                    (user["employee_id"],day,body.transport_mode))
                ident=cur.fetchone()[0]
        return {"ref":f"trip-real-{ident}","status":"pending",
                "message":"Trajet déclaré. Code d'arrivée et contrôles RH/GPS nécessaires avant toute prime."}
    except psycopg.Error as exc:
        raise HTTPException(503, "Impossible d'enregistrer le trajet.") from exc


def qualifying_reporting_rows(rows: list[dict], source: str) -> list[dict]:
    """Conserve les événements historiques en base, ne transmet aux KPI que les vérifiés.

    Pour le portail démo, invalidation automatique d'une preuve devenue obsolète
    après changement de révision. Pour PostgreSQL, le statut RH validé est aussi requis.
    """
    with employee.connect() as conn:
        _setup(conn)
        proofs={(r["employee_id"],r["activity_ref"],int(r["activity_revision"]))
                for r in conn.execute("""SELECT employee_id,activity_ref,activity_revision
                   FROM sportpulse_proof_codes WHERE consumed_at IS NOT NULL AND kind='activity'""")}
        if source == "demo":
            people={r["id"]:r["employee_id"] for r in conn.execute("SELECT id,employee_id FROM accounts")}
            has_revision="revision" in {r[1] for r in conn.execute("PRAGMA table_info(demo_activities)")}
            revisions={r["id"]:r["revision"] for r in conn.execute("SELECT id,revision FROM demo_activities")} if has_revision else {}
    approved=[]
    for row in rows:
        ident=int(row["id"])
        if source=="demo":
            employee_id=people.get(row["account_id"])
            revision=int(revisions.get(ident,1))
            is_valid=employee_id is not None and (employee_id,f"demo-{ident}",revision) in proofs
        else:
            is_valid=(row.get("valid_for_threshold") is True
                     and (str(row["account_id"]),f"decl-{ident}",1) in proofs)
            # Future revisions of validated activities should never be self-editable;
            # reporting relies on the immutable RH validation status.
            if not is_valid and row.get("valid_for_threshold"):
                is_valid=any(p[0]==str(row["account_id"]) and p[1]==f"decl-{ident}" for p in proofs)
        if is_valid:
            approved.append({**row,"valid_for_threshold":True,"status":"validated"})
    return approved
