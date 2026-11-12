"""Simulations RH versionnées et replay à sources figées (aucun effet sur la paie).

Intégration additive avec admin_portal.py et la même base admin.sqlite.
Le replay recalcule un agrégat *figé* ; modifier le référentiel RH requiert un
nouveau run avec une nouvelle empreinte de source. Pas de salaires individuels.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from sportpulse import admin_portal as admin

router = APIRouter(tags=["scenario-replay"])


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scenario_rules (
            version INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            created_by_id INTEGER REFERENCES admin_users(id),
            bonus_rate TEXT NOT NULL,
            note TEXT NOT NULL,
            CHECK(version > 0)
        );
        CREATE TABLE IF NOT EXISTS scenario_runs (
            run_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            created_by_id INTEGER NOT NULL REFERENCES admin_users(id),
            rule_version INTEGER NOT NULL REFERENCES scenario_rules(version),
            source_kind TEXT NOT NULL CHECK(source_kind IN ('demo','postgres')),
            reference_year INTEGER NOT NULL,
            source_fingerprint TEXT NOT NULL,
            source_snapshot_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            parent_run_id TEXT REFERENCES scenario_runs(run_id),
            status TEXT NOT NULL DEFAULT 'SIMULATION_ONLY'
        );
        CREATE INDEX IF NOT EXISTS ix_scenario_run_date ON scenario_runs(created_at DESC);
    """)
    conn.execute("""INSERT OR IGNORE INTO scenario_rules
        (version,created_at,created_by_id,bonus_rate,note)
        VALUES(1, ?, NULL, '0.05', 'Règle de référence du POC : 5 %; critères RH à confirmer')
    """, (_utc(),))


def _require_admin(session: str | None, request: Request | None = None, csrf: str | None = None, *, write=False):
    user = admin.current_admin(session, required_role="admin" if write else None)
    if write:
        admin.require_csrf(request, user, csrf)
    return user


def _rule(conn, version: int) -> dict:
    row = conn.execute("SELECT version, bonus_rate, note, created_at FROM scenario_rules WHERE version=?", (version,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Version des règles introuvable")
    return dict(row)


def _snapshot(source: Literal["demo", "postgres"], year: int) -> dict:
    existing = admin.load_source(source, year)
    # Le snapshot est un agrégat analytique sans identité ni salaire individuel.
    return {
        "source": existing["source"],
        "source_label": existing["source_label"],
        "source_import_id": existing.get("source_import_id"),
        "reference_year": int(year),
        "employees_reviewed": int(existing["employee_count"]),
        "eligible_candidates": int(existing["eligible_candidates"]),
        "annual_salary_total_eur": str(existing["annual_salary_total"]),
        "source_policy_rate": str(existing["current_rate"]),
        "policy_status": existing["policy_status"],
        "warning": existing["warning"],
    }


def compute(snapshot: dict, rule: dict) -> dict:
    salary = Decimal(snapshot["annual_salary_total_eur"])
    rate = Decimal(rule["bonus_rate"])
    previous_rate = Decimal(snapshot["source_policy_rate"])
    return {
        "reference_year": snapshot["reference_year"],
        "source": snapshot["source"],
        "source_label": snapshot["source_label"],
        "employees_reviewed": snapshot["employees_reviewed"],
        "eligible_candidates": snapshot["eligible_candidates"],
        "source_policy_rate": str(previous_rate),
        "simulated_rate": str(rate),
        "reference_bonus_budget_eur": _money(salary * previous_rate),
        "simulated_bonus_budget_eur": _money(salary * rate),
        "delta_to_reference_eur": _money(salary * (rate - previous_rate)),
        "policy_status": snapshot["policy_status"],
        "warning": snapshot["warning"],
        "assumptions": [
            "Prime = somme des salaires bruts annuels des candidats retenus × taux.",
            "Les critères de trajet, de majorité du déplacement et de fréquence doivent être validés par les RH.",
            "Les journées bien-être ne sont pas chiffrées par ce simulateur de prime.",
            "SIMULATION_ONLY : aucun droit ni paiement n'est déclenché.",
        ],
    }


def _save_run(conn: sqlite3.Connection, user: dict, snap: dict, rule: dict, parent: str | None = None) -> dict:
    output = compute(snap, rule)
    key = uuid.uuid4().hex[:20]
    created = _utc()
    fp = _fingerprint(snap)
    conn.execute("""
      INSERT INTO scenario_runs(run_id,created_at,created_by_id,rule_version,source_kind,
        reference_year,source_fingerprint,source_snapshot_json,result_json,parent_run_id)
      VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (key,created,user["id"],rule["version"],snap["source"],snap["reference_year"],fp,
          _canonical(snap),_canonical(output),parent))
    admin.audit(conn,user["id"],"scenario_replay" if parent else "scenario_run",key,
                {"rule_version": rule["version"], "parent_run_id": parent, "source_fingerprint": fp[:12]})
    return {"run_id":key,"created_at":created,"rule_version":rule["version"],
            "source_fingerprint":fp,"parent_run_id":parent,"status":"SIMULATION_ONLY",**output}


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_percent: Decimal = Field(ge=0, le=20, max_digits=5, decimal_places=2)
    note: str = Field(min_length=5, max_length=250)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["demo", "postgres"] = "demo"
    reference_year: int = Field(default=2026, ge=2020, le=2100)
    rule_version: int = Field(default=1, ge=1)


class ReplayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_version: int = Field(ge=1)


@router.get("/admin/api/replay/rules")
def list_rules(session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    _require_admin(session)
    with admin.connect_admin() as conn:
        ensure_schema(conn)
        rows=conn.execute("SELECT version,bonus_rate,note,created_at FROM scenario_rules ORDER BY version").fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/admin/api/replay/rules", status_code=201)
def new_rule(body: RuleCreate, request: Request,
             session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
             x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    user=_require_admin(session,request,x_csrf_token,write=True)
    rate=(body.rate_percent / Decimal(100)).quantize(Decimal("0.0001"))
    with admin.connect_admin() as conn:
        ensure_schema(conn)
        # ensure_schema may have started a transaction with INSERT OR IGNORE.
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        next_version=int(conn.execute("SELECT MAX(version) FROM scenario_rules").fetchone()[0])+1
        conn.execute("INSERT INTO scenario_rules(version,created_at,created_by_id,bonus_rate,note) VALUES(?,?,?,?,?)",
                     (next_version,_utc(),user["id"],str(rate),body.note))
        admin.audit(conn,user["id"],"scenario_rule_created",f"v{next_version}",{"rate":str(rate)})
        return {"version":next_version,"bonus_rate":str(rate),"note":body.note,"immutable":True}


@router.post("/admin/api/replay/runs", status_code=201)
def create_run(body: RunCreate, request: Request,
               session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
               x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    user=_require_admin(session,request,x_csrf_token,write=True)
    snap=_snapshot(body.source,body.reference_year)
    with admin.connect_admin() as conn:
        ensure_schema(conn)
        rule=_rule(conn,body.rule_version)
        return _save_run(conn,user,snap,rule)


@router.post("/admin/api/replay/runs/{run_id}/replay", status_code=201)
def replay(run_id: str, body: ReplayCreate, request: Request,
           session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None,
           x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
    user=_require_admin(session,request,x_csrf_token,write=True)
    with admin.connect_admin() as conn:
        ensure_schema(conn)
        row=conn.execute("SELECT source_snapshot_json FROM scenario_runs WHERE run_id=?",(run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404,detail="Run initial introuvable")
        snap=json.loads(row["source_snapshot_json"])
        rule=_rule(conn,body.rule_version)
        return _save_run(conn,user,snap,rule,run_id)


@router.get("/admin/api/replay/runs")
def list_runs(session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    _require_admin(session)
    with admin.connect_admin() as conn:
        ensure_schema(conn)
        rows=conn.execute("""SELECT run_id,created_at,rule_version,source_kind,reference_year,
            source_fingerprint,result_json,parent_run_id,status FROM scenario_runs
            ORDER BY created_at DESC, rowid DESC LIMIT 100""").fetchall()
        items=[]
        for r in rows:
            d=dict(r)
            d["result"]=json.loads(d.pop("result_json"))
            items.append(d)
        return {"items":items}


@router.get("/admin/api/replay/runs/{run_id}")
def get_run(run_id: str, session: Annotated[str | None, Cookie(alias=admin.COOKIE_NAME)] = None):
    _require_admin(session)
    with admin.connect_admin() as conn:
        ensure_schema(conn)
        row=conn.execute("SELECT * FROM scenario_runs WHERE run_id=?",(run_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404,detail="Run introuvable")
        return {"run_id":row["run_id"],"created_at":row["created_at"],
                "source_fingerprint":row["source_fingerprint"],"rule_version":row["rule_version"],
                "parent_run_id":row["parent_run_id"],"source_snapshot":json.loads(row["source_snapshot_json"]),
                "result":json.loads(row["result_json"]),"status":row["status"]}


@router.get("/admin/replay", response_class=HTMLResponse, include_in_schema=False)
def replay_page():
    # Page séparée ; réutilise les cookies HttpOnly et le CSRF du portail admin.
    from fastapi.responses import FileResponse
    from pathlib import Path
    return FileResponse(Path(__file__).resolve().parent / "scenario_assets" / "index.html",
                        headers={"Cache-Control":"no-store", "X-Frame-Options":"DENY",
                        "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'"})


@router.get("/admin/replay/assets/{name}", include_in_schema=False)
def replay_asset(name: str):
    if name not in {"app.js","style.css"}:
        raise HTTPException(404)
    from fastapi.responses import FileResponse
    from pathlib import Path
    return FileResponse(Path(__file__).resolve().parent / "scenario_assets" / name,
                        headers={"Cache-Control":"no-store"})
