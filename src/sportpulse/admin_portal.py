"""SportPulse AI : portail administrateur RH, lecture agrégée et scénarios versionnés.

- Identité admin distincte du portail salarié.
- Aucun identifiant admin par défaut ni auto-inscription publique.
- Simulations uniquement : ne change PAS benefits.benefit_policy.
- Connexion PostgreSQL en lecture seule ; aucun salaire individuel exposé.
- Historique des scénarios en SQLite avec piste d'audit.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(tags=["administration-rh"])
ASSETS = Path(__file__).resolve().parent / "admin_assets"
COOKIE_NAME = "sportpulse_admin_session"
SESSION_SECONDS = 4 * 3600
MAX_ATTEMPTS = 8
ALLOWED_ROLES = {"admin", "rh_viewer"}
DEMO_SALARIES = ("32000.00", "37500.00", "42000.00", "48000.00", "56000.00")
DEMO_ELIGIBLE_INDICES = (0, 2, 4)


def database_path() -> Path:
    return Path(os.getenv("ADMIN_DB_PATH", "data/admin.sqlite"))


def _now() -> int:
    return int(time.time())


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _password_digest(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


@contextmanager
def connect_admin():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_salt TEXT NOT NULL,
            password_digest TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','rh_viewer')),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token_digest TEXT PRIMARY KEY,
            csrf_digest TEXT NOT NULL,
            user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_login_attempts (
            identity TEXT NOT NULL, attempted_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_attempts
            ON admin_login_attempts(identity, attempted_at);
        CREATE TABLE IF NOT EXISTS admin_simulations (
            run_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            created_by_id INTEGER NOT NULL REFERENCES admin_users(id),
            reference_year INTEGER NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('demo','postgres')),
            previous_rate TEXT NOT NULL,
            simulated_rate TEXT NOT NULL,
            eligible_candidates INTEGER NOT NULL,
            employees_reviewed INTEGER NOT NULL,
            annual_salary_total TEXT NOT NULL,
            previous_budget TEXT NOT NULL,
            new_budget TEXT NOT NULL,
            budget_delta TEXT NOT NULL,
            policy_status TEXT NOT NULL,
            run_status TEXT NOT NULL DEFAULT 'SIMULATION_ONLY',
            source_label TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_simulations_created
            ON admin_simulations(created_at DESC);
        CREATE TABLE IF NOT EXISTS admin_audit (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            happened_at TEXT NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            object_ref TEXT,
            detail TEXT NOT NULL DEFAULT '{}'
        );
    """)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, user_id: int | None, action: str, object_ref: str | None = None, detail: dict | None = None):
    conn.execute(
        "INSERT INTO admin_audit(happened_at,user_id,action,object_ref,detail) VALUES(?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), user_id, action, object_ref, json.dumps(detail or {}, sort_keys=True)),
    )


def create_admin(email: str, password: str, *, role: str = "admin"):
    """À appeler depuis Docker CLI, pas depuis une route HTTP."""
    email = email.strip().casefold()
    if not email or "@" not in email:
        raise ValueError("Adresse e-mail non valide")
    if role not in ALLOWED_ROLES:
        raise ValueError("Rôle inconnu")
    if len(password) < 14 or len(password) > 128:
        raise ValueError("Le mot de passe doit contenir entre 14 et 128 caractères")
    salt = secrets.token_bytes(16)
    with connect_admin() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO admin_users(email,password_salt,password_digest,role,created_at) VALUES(?,?,?,?,?)",
                (email, salt.hex(), _password_digest(password, salt), role, _now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Un compte existe déjà pour cette adresse") from exc
        audit(conn, cur.lastrowid, "admin_account_created", detail={"role": role})


def _attempt(conn: sqlite3.Connection, key: str):
    now = _now()
    conn.execute("DELETE FROM admin_login_attempts WHERE attempted_at<?", (now - 900,))
    count = conn.execute(
        "SELECT COUNT(*) FROM admin_login_attempts WHERE identity=? AND attempted_at>=?",
        (key, now - 900),
    ).fetchone()[0]
    if count >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans 15 minutes.")
    conn.execute("INSERT INTO admin_login_attempts VALUES (?,?)", (key, now))
    conn.commit()


def current_admin(session: str | None, *, required_role: str | None = None) -> dict:
    if not session:
        raise HTTPException(status_code=401, detail="Connexion administrateur requise.")
    with connect_admin() as conn:
        row = conn.execute("""
            SELECT a.id, a.email, a.role, s.csrf_digest
            FROM admin_sessions s JOIN admin_users a ON a.id=s.user_id
            WHERE s.token_digest=? AND s.expires_at>? AND a.enabled=1
        """, (_hash(session), _now())).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Session administrateur expirée.")
    data = dict(row)
    if required_role and data["role"] != required_role:
        raise HTTPException(status_code=403, detail="Droits administrateur insuffisants.")
    return data


def _check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin:
        parts = urlsplit(origin)
        if parts.scheme != request.url.scheme or parts.netloc != request.url.netloc:
            raise HTTPException(status_code=403, detail="Origine refusée.")


def require_csrf(request: Request, user: dict, csrf: str | None):
    _check_origin(request)
    if not csrf or not hmac.compare_digest(_hash(csrf), user["csrf_digest"]):
        raise HTTPException(status_code=403, detail="Jeton CSRF invalide.")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _decimal_rate(value: str) -> Decimal:
    rate = Decimal(value)
    if not rate.is_finite() or rate < 0 or rate > Decimal("0.20"):
        raise HTTPException(status_code=422, detail="Taux hors limite : de 0 à 20 %.")
    return rate


def demo_source(year: int) -> dict:
    """Jeu de démonstration explicitement synthétique, sans données RH réelles."""
    eligible = [Decimal(DEMO_SALARIES[i]) for i in DEMO_ELIGIBLE_INDICES]
    return {
        "source": "demo", "source_label": "SYNTHÉTIQUE — 5 profils fictifs",
        "year": year, "employee_count": len(DEMO_SALARIES),
        "eligible_candidates": len(eligible),
        "annual_salary_total": sum(eligible, Decimal(0)),
        "current_rate": Decimal("0.05"),
        "policy_status": "simulation_sans_validation_rh",
        "warning": "Données purement fictives : aucun paiement ni droit RH accordé.",
    }


def pg_source(year: int) -> dict:
    """Lit des agrégats RH, sans salaires individuels côté client.

    Le schéma benefits est celui de SportPulse_AI_step 2.
    La vérification des critères est faite dans PostgreSQL.
    """
    # Priorité au référentiel RH du POC réellement importé, sans repli sur cinq profils fictifs.
    from sportpulse.rh_dashboard import hr_financial_snapshot
    imported = hr_financial_snapshot(year)
    if imported is not None:
        return imported

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(status_code=503, detail="DATABASE_URL absent.")
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=4) as db:
            with db.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                    WITH routes AS (
                        SELECT employee_id, COUNT(DISTINCT commute_date)::integer AS n
                        FROM benefits.sport_commute_declarations
                        WHERE EXTRACT(YEAR FROM commute_date)::integer=%s
                          AND validation_status='validated'
                          AND office_arrival_confirmed=TRUE
                        GROUP BY employee_id
                    ), rows AS (
                        SELECT s.employee_id, s.annual_gross_salary,
                               (s.salary_validated=TRUE AND
                                p.criteria_status='defined' AND
                                p.min_validated_commute_days_per_year IS NOT NULL AND
                                COALESCE(r.n,0) >= p.min_validated_commute_days_per_year) AS candidate,
                               p.bonus_rate, p.criteria_status
                        FROM benefits.employee_compensation s
                        JOIN benefits.benefit_policy p ON p.benefit_code='sport_commute_bonus'
                        LEFT JOIN routes r ON r.employee_id=s.employee_id
                          WHERE s.reference_year=%s
                    )
                    SELECT COUNT(*)::integer AS reviewed,
                           COUNT(*) FILTER (WHERE candidate)::integer AS eligible,
                           COALESCE(SUM(annual_gross_salary) FILTER (WHERE candidate),0),
                           MAX(bonus_rate), MAX(criteria_status)
                    FROM rows
                """, (year, year))
                reviewed, eligible, total, current, status = cur.fetchone()
    except Exception as exc:
        # Le cas du schéma/migration incomplète est volontairement visible sans fuite SQL.
        raise HTTPException(status_code=503, detail=(
            "Agrégats RH indisponibles. Vérifie les migrations benefits et DATABASE_URL."
        )) from exc
    return {
        "source": "postgres", "source_label": "POSTGRESQL — agrégat RH en lecture seule",
        "year": year, "employee_count": reviewed,
        "eligible_candidates": eligible,
        "annual_salary_total": Decimal(total),
        "current_rate": Decimal(current) if current is not None else Decimal("0.05"),
        "policy_status": status or "to_confirm",
        "warning": "Candidats estimés, pas de primes acquises ou payables sans décision RH.",
    }


def load_source(source: Literal["demo", "postgres"], year: int) -> dict:
    if year < 2020 or year > 2100:
        raise HTTPException(status_code=422, detail="Année hors plage.")
    if source == "demo":
        if os.getenv("ADMIN_DEMO_MODE", "false").casefold() != "true":
            raise HTTPException(status_code=403, detail="Mode démonstration non activé.")
        return demo_source(year)
    return pg_source(year)


def build_simulation(snapshot: dict, proposed_rate: Decimal) -> dict:
    base = snapshot["annual_salary_total"]
    before = (base * snapshot["current_rate"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    after = (base * proposed_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "reference_year": snapshot["year"],
        "source": snapshot["source"], "source_label": snapshot["source_label"],
        "employees_reviewed": snapshot["employee_count"],
        "eligible_candidates": snapshot["eligible_candidates"],
        "annual_salary_total_eur": _money(base),
        "previous_rate": str(snapshot["current_rate"]),
        "simulated_rate": str(proposed_rate),
        "previous_budget_eur": _money(before),
        "new_budget_eur": _money(after),
        "budget_delta_eur": _money(after - before),
        "policy_status": snapshot["policy_status"],
        "warning": snapshot["warning"],
        "run_status": "SIMULATION_ONLY",
    }


class LoginBody(BaseModel):
    # Demo accounts may use the reserved .local domain. EmailStr rejects it,
    # whereas create_admin() allows it; keep these validation rules consistent.
    email: str = Field(min_length=3, max_length=254)
    password: str

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized.count("@") != 1 or any(ch.isspace() for ch in normalized):
            raise ValueError("Adresse e-mail invalide")
        local, domain = normalized.split("@")
        if not local or not domain or "." not in domain:
            raise ValueError("Adresse e-mail invalide")
        return normalized


class ScenarioBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["demo", "postgres"] = "demo"
    reference_year: int = Field(ge=2020, le=2100)
    bonus_rate_percent: Decimal = Field(ge=0, le=20, max_digits=5, decimal_places=2)


@router.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(ASSETS / "index.html", headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; object-src 'none'",
    })


@router.get("/admin/assets/{name}", include_in_schema=False)
def admin_asset(name: str):
    if name not in {"app.js", "style.css", 'sp12_admin.js', 'sp12_layout.js', 'sp13_admin.js', 'sp13_admin.css'}:
        raise HTTPException(status_code=404)
    return FileResponse(ASSETS / name, headers={"Cache-Control": "no-store"})


@router.post("/admin/api/login")
def admin_login(body: LoginBody, response: Response, request: Request):
    _check_origin(request)
    email = str(body.email).casefold()
    key = f"admin:{request.client.host if request.client else 'unknown'}:{email}"
    with connect_admin() as conn:
        _attempt(conn, key)
        row = conn.execute("SELECT * FROM admin_users WHERE email=? AND enabled=1", (email,)).fetchone()
    probe = _password_digest(body.password, bytes.fromhex(row["password_salt"])) if row else _password_digest(body.password, bytes(16))
    if row is None or not hmac.compare_digest(probe, row["password_digest"]):
        raise HTTPException(status_code=401, detail="Identifiants incorrects.")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connect_admin() as conn:
        conn.execute("INSERT INTO admin_sessions(token_digest,csrf_digest,user_id,expires_at) VALUES(?,?,?,?)",
                     (_hash(token), _hash(csrf), row["id"], _now()+SESSION_SECONDS))
        audit(conn, row["id"], "admin_login")
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        samesite="strict", path="/admin",
                        secure=os.getenv("PORTAL_COOKIE_SECURE", "false").lower()=="true")
    return {"role": row["role"], "csrf_token": csrf, "message": "Connexion réussie."}


@router.get("/admin/api/me")
def admin_me(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    user = current_admin(session)
    return {"email": user["email"], "role": user["role"]}


@router.post("/admin/api/csrf")
def admin_csrf(request: Request, session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    _check_origin(request)
    user = current_admin(session)
    csrf = secrets.token_urlsafe(32)
    with connect_admin() as conn:
        conn.execute("UPDATE admin_sessions SET csrf_digest=? WHERE token_digest=?", (_hash(csrf), _hash(session)))
    return {"csrf_token": csrf, "role": user["role"]}


@router.post("/admin/api/logout")
def admin_logout(request: Request, response: Response,
                 session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
                 x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    user = current_admin(session)
    require_csrf(request, user, x_csrf_token)
    with connect_admin() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token_digest=?", (_hash(session),))
        audit(conn, user["id"], "admin_logout")
    response.delete_cookie(COOKIE_NAME, path="/admin")
    return {"message": "Session fermée."}


@router.get("/admin/api/overview")
def overview(source: Literal["demo", "postgres"] = "demo", reference_year: int = 2026,
             session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    current_admin(session)
    snap = load_source(source, reference_year)
    return build_simulation(snap, snap["current_rate"])


@router.post("/admin/api/simulations", status_code=201)
def create_simulation(body: ScenarioBody, request: Request,
                      session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
                      x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    user = current_admin(session, required_role="admin")
    require_csrf(request, user, x_csrf_token)
    rate = _decimal_rate(body.bonus_rate_percent / Decimal("100"))
    snap = load_source(body.source, body.reference_year)
    result = build_simulation(snap, rate)
    result["run_id"] = uuid.uuid4().hex[:16]
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    with connect_admin() as conn:
        conn.execute("""
            INSERT INTO admin_simulations(
                run_id,created_at,created_by_id,reference_year,source,
                previous_rate,simulated_rate,eligible_candidates,employees_reviewed,
                annual_salary_total,previous_budget,new_budget,budget_delta,
                policy_status,run_status,source_label)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (result["run_id"], result["created_at"], user["id"], result["reference_year"],
              result["source"], result["previous_rate"], result["simulated_rate"],
              result["eligible_candidates"], result["employees_reviewed"],
              result["annual_salary_total_eur"], result["previous_budget_eur"],
              result["new_budget_eur"], result["budget_delta_eur"], result["policy_status"],
              result["run_status"], result["source_label"]))
        audit(conn, user["id"], "scenario_created", result["run_id"], {
            "year": body.reference_year, "source": body.source, "rate": str(rate)
        })
    return result


@router.get("/admin/api/simulations")
def list_simulations(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    current_admin(session)
    with connect_admin() as conn:
        from sportpulse.admin_history import ensure_schema
        ensure_schema(conn)
        rows = conn.execute("""
            SELECT run_id,created_at,reference_year,source,previous_rate,
                   simulated_rate,eligible_candidates,employees_reviewed,
                   previous_budget,new_budget,budget_delta,policy_status,run_status,source_label
            FROM admin_simulations s
            WHERE NOT EXISTS (SELECT 1 FROM admin_hidden_history h
                 WHERE h.kind='simulation' AND h.ref=s.run_id)
            ORDER BY s.created_at DESC, s.run_id DESC LIMIT 5
        """).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/admin/api/audit")
def list_audit(session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    current_admin(session, required_role="admin")
    with connect_admin() as conn:
        from sportpulse.admin_history import ensure_schema
        ensure_schema(conn)
        rows = conn.execute("""
            SELECT a.event_id,a.happened_at,a.action,a.object_ref,a.detail
            FROM admin_audit a
            WHERE a.action <> 'history_archived'
              AND NOT EXISTS (SELECT 1 FROM admin_hidden_history h
                   WHERE h.kind='audit' AND h.ref=CAST(a.event_id AS TEXT))
            ORDER BY a.event_id DESC LIMIT 5
        """).fetchall()
    return {"items": [{"event_id":r["event_id"],"happened_at":r["happened_at"],"action":r["action"],
                       "object_ref":r["object_ref"],"detail":json.loads(r["detail"])} for r in rows]}


def main():
    parser = argparse.ArgumentParser(description="Gestion CLI des comptes administrateurs SportPulse AI")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("create-admin", help="Compte admin sécurisé créé localement")
    add.add_argument("--email", required=True)
    add.add_argument("--role", choices=sorted(ALLOWED_ROLES), default="admin")
    args = parser.parse_args()
    if args.command == "create-admin":
        pw = getpass.getpass("Mot de passe administrateur (14 caractères minimum) : ")
        again = getpass.getpass("Confirmer le mot de passe : ")
        if pw != again:
            raise SystemExit("Les deux mots de passe ne correspondent pas")
        try:
            create_admin(args.email, pw, role=args.role)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print("Compte administrateur créé. Aucun mot de passe affiché ou enregistré en clair.")


if __name__ == "__main__":
    main()
