"""Portail salarié SportPulse AI — étape 4.

Interface FastAPI isolée du SI RH : invitations à usage unique, sessions HttpOnly,
lecture des données PostgreSQL limitée au salarié connecté, et démo synthétique.
Ne verse aucune prime et ne publie pas dans Slack (étapes distinctes).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Cookie, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field

router = APIRouter(tags=["employee-portal"])
ASSETS = Path(__file__).resolve().parent / "employee_portal_assets"
COOKIE_NAME = "sportpulse_session"
AUTH_TTL_S = 8 * 3600
ALLOWED_SPORTS = {
    "running": "Course à pied", "cycling": "Vélo", "walking": "Marche",
    "hiking": "Randonnée", "swimming": "Natation", "football": "Football",
    "tennis": "Tennis", "yoga": "Yoga", "other": "Autre activité",
}


def db_path() -> Path:
    return Path(os.getenv("PORTAL_DB_PATH", "data/portal.sqlite"))


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS invitations (
            invite_digest TEXT PRIMARY KEY,
            employee_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
            expires_at INTEGER NOT NULL,
            used_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            password_digest TEXT NOT NULL,
            is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_digest TEXT PRIMARY KEY,
            csrf_digest TEXT NOT NULL,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demo_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            activity_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            duration_min INTEGER NOT NULL CHECK (duration_min BETWEEN 1 AND 1440),
            distance_km REAL CHECK (distance_km IS NULL OR distance_km >= 0),
            comment TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS demo_commutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            mode TEXT NOT NULL,
            distance_km REAL NOT NULL,
            verification_status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            identifier TEXT NOT NULL,
            attempted_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts (identifier, attempted_at);
        CREATE INDEX IF NOT EXISTS idx_demo_acts ON demo_activities (account_id, started_at);
    """)
    conn.commit()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


def rate_limit(conn: sqlite3.Connection, identifier: str) -> None:
    now = int(time.time())
    conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - 900,))
    count = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE identifier=? AND attempted_at>=?",
        (identifier, now - 900),
    ).fetchone()[0]
    if count >= 8:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez plus tard.")
    conn.execute("INSERT INTO login_attempts VALUES (?,?)", (identifier, now))
    conn.commit()


def current_account(session: str | None) -> dict:
    if not session:
        raise HTTPException(status_code=401, detail="Connectez-vous au portail.")
    with connect() as conn:
        row = conn.execute("""
            SELECT a.id, a.employee_id, a.display_name, a.email, a.is_demo, s.csrf_digest
            FROM sessions AS s JOIN accounts AS a ON a.id=s.account_id
            WHERE s.token_digest=? AND s.expires_at>?
        """, (token_hash(session), int(time.time()))).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expirée. Reconnectez-vous.")
    return dict(row)


def require_csrf(request: Request, acct: dict, token: str | None) -> None:
    """X-CSRF-Token + vérification Origin sur les routes avec effet de bord."""
    if not token or not hmac.compare_digest(token_hash(token), acct["csrf_digest"]):
        raise HTTPException(status_code=403, detail="Jeton CSRF invalide.")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit
        origin_parts = urlsplit(origin)
        # Comparing netloc also checks the nondefault port.
        if origin_parts.netloc != request.url.netloc or origin_parts.scheme != request.url.scheme:
            raise HTTPException(status_code=403, detail="Origine non autorisée.")


def social_window(year: int) -> tuple[date, date]:
    month = int(os.getenv("PORTAL_SOCIAL_YEAR_START_MONTH", "1"))
    if not 1 <= month <= 12:
        raise ValueError("PORTAL_SOCIAL_YEAR_START_MONTH doit être entre 1 et 12")
    return date(year, month, 1), date(year + 1, month, 1)


def current_social_year() -> int:
    today = datetime.now(timezone.utc).date()
    month = int(os.getenv("PORTAL_SOCIAL_YEAR_START_MONTH", "1"))
    return today.year if today.month >= month else today.year - 1


class RegisterBody(BaseModel):
    invite_code: str = Field(min_length=20, max_length=100)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class NewActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_type: str = Field(min_length=1, max_length=30)
    started_at: datetime
    duration_min: int = Field(ge=1, le=1440)
    distance_km: float | None = Field(default=None, ge=0, le=500)
    comment: str = Field(default="", max_length=350)
    share_to_slack: bool = False  # opt-in explicite par séance


def create_invitation(employee_id: str, display_name: str, *, demo: bool = False) -> str:
    if not employee_id.strip() or not display_name.strip():
        raise ValueError("Un identifiant et un nom sont obligatoires")
    if not demo:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise ValueError("DATABASE_URL requis pour inviter un salarié réel")
        import psycopg
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM dbt_marts.dim_employee WHERE employee_id=%s", (employee_id,))
                    if cur.fetchone() is None:
                        raise ValueError("Identifiant absent du référentiel RH")
        except psycopg.Error as exc:
            raise ValueError("Connexion RH indisponible") from exc
    invitation = secrets.token_urlsafe(32)
    with connect() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE employee_id=?", (employee_id,)).fetchone():
            raise ValueError("Un compte existe déjà pour cet identifiant")
        conn.execute("""
            INSERT INTO invitations(invite_digest,employee_id,display_name,is_demo,expires_at)
            VALUES (?,?,?,?,?)
        """, (token_hash(invitation), employee_id.strip(), display_name.strip(), int(demo), int(time.time()) + 48*3600))
    return invitation


def seed_demo(conn: sqlite3.Connection, account_id: int) -> None:
    """Fausse activité, démonstrative, étiquetée comme telle dans l'UI."""
    # 14 activités => l'ajout d'une 15e fait franchir le seuil indicatif.
    start, _ = social_window(current_social_year())
    types = ["running", "cycling", "hiking", "running", "tennis", "cycling", "running",
             "walking", "football", "running", "cycling", "running", "hiking", "running"]
    for i, sport in enumerate(types):
        day = start + timedelta(days=8 + i * 13)
        # Un mois social autre que janvier peut chevaucher la date courante : limiter au passé.
        if day > datetime.now(timezone.utc).date():
            day = datetime.now(timezone.utc).date() - timedelta(days=max(0, (13 - i) * 2))
        km = round(4.3 + (i % 6) * 1.3, 1) if sport in ("running", "cycling", "hiking", "walking") else None
        conn.execute("""
            INSERT INTO demo_activities(account_id,activity_type,started_at,duration_min,distance_km,comment,created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (account_id, sport, day.isoformat() + "T18:30:00+00:00", 28 + i * 4, km,
              ["Belle sortie !", "Objectif régularité", "Avec les collègues"][i % 3], int(time.time())))
    for i in range(5):
        day = datetime.now(timezone.utc).date() - timedelta(days=i * 9 + 1)
        if day < start:
            continue
        conn.execute("""
            INSERT INTO demo_commutes (account_id,day,mode,distance_km,verification_status)
            VALUES (?,?,?,?,?)
        """, (account_id, day.isoformat(), "Vélo" if i % 2 == 0 else "Marche", round(4.2+i*.6, 1), "pending" if i < 2 else "validated"))


def login_session(response: Response, account_id: int) -> str:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("INSERT INTO sessions (token_digest,csrf_digest,account_id,expires_at) VALUES (?,?,?,?)",
                     (token_hash(token), token_hash(csrf), account_id, int(time.time())+AUTH_TTL_S))
    response.set_cookie(
        COOKIE_NAME, token, max_age=AUTH_TTL_S, httponly=True,
        secure=os.getenv("PORTAL_COOKIE_SECURE", "false").lower() == "true",
        samesite="strict", path="/employee",
    )
    return csrf


@router.get("/employee", include_in_schema=False)
def portal_index():
    return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/employee/assets/{name}", include_in_schema=False)
def portal_asset(name: str):
    if name not in ("app.js", "style.css", 'sp12_employee.js', 'sp12_unified.css', 'sp13_map.js', 'sp13_portal.js', 'sp13_style.css', 'sp14_cleanup.css', 'sp15_map.js', 'sp15_ui.js', 'sp15_style.css', 'sp16_benefits.js', 'sp16_benefits.css'):
        raise HTTPException(status_code=404)
    return FileResponse(ASSETS / name)


@router.post("/employee/api/register", status_code=201)
def register(body: RegisterBody, response: Response, request: Request):
    email = str(body.email).casefold()
    with connect() as conn:
        rate_limit(conn, "register:" + str(request.client.host if request.client else "unknown"))
        inv = conn.execute("SELECT * FROM invitations WHERE invite_digest=?", (token_hash(body.invite_code),)).fetchone()
        if not inv or inv["used_at"] or inv["expires_at"] <= int(time.time()):
            raise HTTPException(status_code=400, detail="Invitation invalide ou expirée.")
        salt = secrets.token_bytes(16)
        try:
            cursor = conn.execute("""
                INSERT INTO accounts(employee_id,email,display_name,password_salt,password_digest,is_demo,created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (inv["employee_id"], email, inv["display_name"], salt.hex(),
                  password_hash(body.password, salt), inv["is_demo"], int(time.time())))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Compte déjà enregistré.") from exc
        conn.execute("UPDATE invitations SET used_at=? WHERE invite_digest=? AND used_at IS NULL",
                     (int(time.time()), token_hash(body.invite_code)))
        if inv["is_demo"]:
            seed_demo(conn, cursor.lastrowid)
        new_id = cursor.lastrowid
    csrf = login_session(response, new_id)
    return {"message": "Compte créé.", "csrf_token": csrf}


@router.post("/employee/api/login")
def login(body: LoginBody, response: Response, request: Request):
    email = str(body.email).casefold()
    identifier = "login:" + (request.client.host if request.client else "unknown") + ":" + email
    with connect() as conn:
        rate_limit(conn, identifier)
        account = conn.execute("SELECT id,password_salt,password_digest FROM accounts WHERE email=?", (email,)).fetchone()
    # Temps comparable même si l'adresse n'existe pas.
    actual = password_hash(body.password, bytes.fromhex(account["password_salt"])) if account else password_hash(body.password, bytes(16))
    if not account or not hmac.compare_digest(actual, account["password_digest"]):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    csrf = login_session(response, account["id"])
    return {"message": "Connexion réussie.", "csrf_token": csrf}


@router.get("/employee/api/session")
def session_info(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    acct = current_account(session)
    # Donner un nouveau CSRF au client serait invalide ; ce jeton doit être obtenu au login/register.
    # La reprise de page utilise un second jeton CSRF dédié (voir /csrf).
    return {"employee_id": acct["employee_id"], "display_name": acct["display_name"], "is_demo": bool(acct["is_demo"])}


@router.post("/employee/api/csrf")
def refresh_csrf(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    acct = current_account(session)
    csrf = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("UPDATE sessions SET csrf_digest=? WHERE token_digest=?",
                     (token_hash(csrf), token_hash(session)))
    return {"csrf_token": csrf}


@router.post("/employee/api/logout")
def logout(request: Request, response: Response,
           session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
           x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    acct = current_account(session)
    require_csrf(request, acct, x_csrf_token)
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_digest=?", (token_hash(session),))
    response.delete_cookie(COOKIE_NAME, path="/employee")
    return {"message": "Déconnexion effectuée."}


def display_activity(record: dict) -> dict:
    return {
        "id": str(record["id"]), "activity_type": record["activity_type"],
        "label": ALLOWED_SPORTS.get(record["activity_type"], record["activity_type"]),
        "started_at": record["started_at"], "duration_min": record["duration_min"],
        "distance_km": record.get("distance_km"), "comment": record.get("comment", ""),
        "validation_status": record.get("validation_status", "simulated"),
        "revision": record.get("revision", 1),
        "editable": record.get("editable", record.get("validation_status", "simulated") == "simulated"),
    }


def demo_dashboard(acct: dict, year: int) -> dict:
    first, following = social_window(year)
    with connect() as conn:
        acts = [dict(r) for r in conn.execute("""
            SELECT id, activity_type, started_at, duration_min, distance_km, comment, revision
            FROM demo_activities WHERE account_id=? AND deleted_at IS NULL AND started_at>=? AND started_at<?
            ORDER BY started_at DESC LIMIT 500
        """, (acct["id"], first.isoformat(), following.isoformat())).fetchall()]
        commutes = [dict(r) for r in conn.execute("""
            SELECT id,day,mode,distance_km,verification_status FROM demo_commutes
            WHERE account_id=? AND day>=? AND day<? ORDER BY day DESC LIMIT 100
        """, (acct["id"], first.isoformat(), following.isoformat())).fetchall()]
    n=len(acts)
    km=round(sum(a["distance_km"] or 0 for a in acts),1)
    duration=sum(a["duration_min"] for a in acts)
    progress=min(n,15)
    score=min(100,round(progress/15*75 + min(len(commutes),5)/5*25))
    salary_demo=42000  # Purement synthétique; jamais depuis le vrai RH.
    rate=0.05
    return {
        "employee": {"id": acct["employee_id"], "name": acct["display_name"], "demo": True},
        "year": year,
        "period": {"start":first.isoformat(),"end_exclusive":following.isoformat()},
        "sports": {"count":n,"distance_km":km,"duration_min":duration,"goal":15,"score":score,
                   "score_label":"Score ludique (simulation), sans effet sur les droits"},
        "benefits": {"bonus_rate":rate,"bonus_estimate_eur":round(salary_demo*rate,2),
                     "bonus_status":"simulation_non_validée", "days_official":None,
                     "days_potential":5 if n>=15 else 0,"wellbeing_status":"potentiel_synthétique"},
        "commutes": [{"id":r["id"],"day":r["day"],"mode":r["mode"],
                      "distance_km":r["distance_km"],"status":r["verification_status"]} for r in commutes],
        "activities": [display_activity(a) for a in acts],
        "notices": ["Données 100 % synthétiques : aucune prime réelle ni attribution RH.",
                    "Trajets : distance et statut uniquement ; aucun tracé GPS brut n'est conservé.",
                    "Le seuil 15 activités est illustratif, sous réserve des règles RH validées."],
    }


def real_dashboard(acct: dict, year: int) -> dict:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(status_code=503, detail="Connexion PostgreSQL non configurée.")
    first, following = social_window(year)
    import psycopg
    try:
        with psycopg.connect(dsn, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                    SELECT activity_id::text, activity_type, started_at, duration_min, distance_m
                    FROM dbt_marts.fact_activity
                    WHERE employee_id=%s AND started_at>=%s AND started_at<%s
                    ORDER BY started_at DESC LIMIT 500
                """, (acct["employee_id"], first, following))
                activities = [{"id":r[0],"activity_type":r[1],"started_at":r[2].isoformat(),
                               "duration_min":r[3],"distance_km":float(r[4])/1000 if r[4] is not None else None,
                               "comment":"", "validation_status":"historique_sans_validation_RH"}
                              for r in cur.fetchall()]
                cur.execute("""
                    SELECT commute_id, commute_date, transport_mode, validation_status
                    FROM benefits.sport_commute_declarations
                    WHERE employee_id=%s AND commute_date >=%s AND commute_date < %s
                    ORDER BY commute_date DESC LIMIT 100
                """, (acct["employee_id"], first, following))
                commutes=[{"id":r[0],"day":r[1].isoformat(),"mode":r[2],
                           "distance_km":None,"status":r[3]} for r in cur.fetchall()]
                # Vues annuelles SQL initialement basées sur des années civiles.
                # Ne pas afficher d'avantages pour une année sociale décalée sans moteur aligné.
                bonus, wellbeing = None, None
                if first.month == 1:
                    cur.execute("""
                        SELECT bonus_rate, estimated_bonus_gross, bonus_status
                        FROM benefits.v_commute_bonus_estimates
                        WHERE employee_id=%s AND reference_year=%s
                    """, (acct["employee_id"], year))
                    bonus=cur.fetchone()
                    cur.execute("""
                        SELECT days_entitled, days_remaining, eligibility_status
                        FROM benefits.v_wellbeing_year_status
                        WHERE employee_id=%s AND reference_year=%s
                    """, (acct["employee_id"], year))
                    wellbeing=cur.fetchone()
    except psycopg.Error as exc:
        # Pas de détails SQL/DSN vers l'utilisateur.
        raise HTTPException(status_code=503, detail="Le reporting RH est temporairement indisponible.") from exc
    n=len(activities)
    km=round(sum(a["distance_km"] or 0 for a in activities),1)
    validated_commutes=sum(c["status"]=="validated" for c in commutes)
    # Score gamification sans incidence sur les avantages réels.
    score=min(100,round(min(n,15)/15*75+min(validated_commutes,5)/5*25))
    return {
        "employee":{"id":acct["employee_id"],"name":acct["display_name"],"demo":False},
        "year":year,"period":{"start":first.isoformat(),"end_exclusive":following.isoformat()},
        "sports":{"count":n,"distance_km":km,"duration_min":sum(a["duration_min"] for a in activities),
                  "goal":15,"score":score,"score_label":"Score ludique, non contractuel"},
        "benefits":{"bonus_rate":float(bonus[0]) if bonus and bonus[0] is not None else None,
                    "bonus_estimate_eur":float(bonus[1]) if bonus and bonus[1] is not None else None,
                    "bonus_status":bonus[2] if bonus else "aucune_estimation_validée",
                    "days_official":int(wellbeing[0]) if wellbeing else None,
                    "days_potential":None,
                    "wellbeing_status":wellbeing[2] if wellbeing else "aucune_decision_RH"},
        "commutes":commutes,"activities":[display_activity(a) for a in activities],
        "notices":["Primes affichées uniquement si calculées par le moteur RH autorisé.",
                   "Les activités historiques ne valent pas automatiquement validation RH.",
                   "Le portail ne conserve pas les traces GPS détaillées."],
    }


@router.get("/employee/api/dashboard")
def dashboard(year: int | None = None,
              session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    acct = current_account(session)
    year = current_social_year() if year is None else year
    if year < 2020 or year > 2100:
        raise HTTPException(status_code=422, detail="Année hors plage.")
    if acct["is_demo"]:
        # Schema migration must be initialized before reading the deleted_at column.
        from sportpulse.activity_management import demo_setup
        with connect() as conn:
            demo_setup(conn)
        from sportpulse.poc_dashboard import summarize_unverified_poc
        return summarize_unverified_poc(demo_dashboard(acct, year), acct, year)
    # SPORTPULSE_STEP17_REAL_DASHBOARD — seules les déclarations du salarié font l'objectif.
    from sportpulse.employee_activity_sync import build_real_portal_dashboard
    return build_real_portal_dashboard(acct, year)


@router.post("/employee/api/activities", status_code=201)
def add_activity(body: NewActivity, request: Request, background_tasks: BackgroundTasks,
                 session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
                 x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    acct = current_account(session)
    require_csrf(request, acct, x_csrf_token)
    if body.activity_type not in ALLOWED_SPORTS:
        raise HTTPException(status_code=422, detail="Type de sport non reconnu.")
    if body.started_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="L'heure doit inclure un fuseau horaire.")
    started = body.started_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if started > now + timedelta(minutes=2) or started < now - timedelta(days=366*2):
        raise HTTPException(status_code=422, detail="Date hors plage autorisée.")
    if acct["is_demo"]:
        from sportpulse.activity_notifications import (initialize_demo_schema,
            demo_enqueue_atomic, event_payload, process_event)
        event_key = None
        with connect() as conn:
            # Initialize before any INSERT: executescript() commits on SQLite.
            initialize_demo_schema(conn)
            cursor=conn.execute("""
                INSERT INTO demo_activities(account_id,activity_type,started_at,duration_min,distance_km,comment,created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (acct["id"], body.activity_type, started.isoformat(), body.duration_min,
                  body.distance_km, body.comment, int(time.time())))
            activity_id = cursor.lastrowid
            if body.share_to_slack:
                event_key = demo_enqueue_atomic(conn, account_id=acct["id"], activity_id=activity_id,
                    payload=event_payload(display_name=acct["display_name"],
                        activity_type=body.activity_type, started_at=started.isoformat(),
                        duration_min=body.duration_min, distance_km=body.distance_km, is_demo=True))
        if event_key:
            background_tasks.add_task(process_event, "demo", event_key)
        return {"activity_id":"demo-"+str(activity_id),"status":"simulated",
                "message":"Activité enregistrée ; tableau de bord actualisé.",
                "slack_status":"queued" if event_key else "not_shared",
                "reporting_status":"portal_immediate"}
    # SPORTPULSE_STEP17_REAL_ACTIVITY — FK RH + création PostgreSQL et Slack atomique.
    from sportpulse.employee_activity_sync import create_real_activity
    return create_real_activity(acct, body, started, background_tasks)



@router.get("/employee/api/notifications")
def notification_status(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    acct = current_account(session)
    if acct["is_demo"]:
        from sportpulse.activity_notifications import demo_notification_rows
        return {"notifications": demo_notification_rows(acct["id"])}
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(status_code=503, detail="PostgreSQL non disponible.")
    import psycopg
    try:
        with psycopg.connect(dsn, connect_timeout=4) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                    SELECT event_key,status,created_at,published_at,slack_channel,message_text
                    FROM benefits.sportpulse_notifications_outbox
                    WHERE employee_id=%s ORDER BY created_at DESC LIMIT 20
                """, (acct["employee_id"],))
                rows = cur.fetchall()
        return {"notifications": [{"event_key":r[0], "status":r[1],
                    "created_at":r[2].isoformat(),
                    "published_at":r[3].isoformat() if r[3] else None,
                    "slack_channel":r[4],"message_text":r[5]} for r in rows]}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Historique des notifications indisponible.") from exc

def main():
    parser=argparse.ArgumentParser(description="Invitations sécurisées du portail salarié")
    sub=parser.add_subparsers(dest="command",required=True)
    invite=sub.add_parser("create-invite",help="Créer une invitation à usage unique valable 48h")
    invite.add_argument("--employee-id",required=True)
    invite.add_argument("--name",required=True)
    invite.add_argument("--demo",action="store_true",help="Salarié synthétique de démonstration")
    args=parser.parse_args()
    if args.command=="create-invite":
        code=create_invitation(args.employee_id,args.name,demo=args.demo)
        print("Invitation à transmettre directement au salarié (ne pas publier en Git) :")
        print(code)
        print("Durée : 48h ; un seul usage.")


if __name__ == "__main__":
    main()
