"""Tests du module administrateur, sans connexion à PostgreSQL, S3 ou Slack."""
from decimal import Decimal
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sportpulse.admin_portal import router, create_admin, connect_admin, build_simulation, demo_source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_DB_PATH", str(tmp_path / "admin.sqlite"))
    monkeypatch.setenv("ADMIN_DEMO_MODE", "true")
    monkeypatch.setenv("PORTAL_COOKIE_SECURE", "false")
    app=FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def login(client, email="admin@test.example", password="Great Strong Password 2026", role="admin"):
    create_admin(email,password,role=role)
    result=client.post("/admin/api/login",json={"email":email,"password":password})
    assert result.status_code == 200, result.text
    return result.json()["csrf_token"]


def test_static_admin_assets(client):
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/assets/app.js").status_code == 200
    assert client.get("/admin/assets/style.css").status_code == 200
    assert "no-store" in client.get("/admin").headers["cache-control"]
    assert client.get("/admin/assets/config.json").status_code == 404


def test_no_auto_registration_or_default_account(client):
    assert client.get("/admin/api/me").status_code == 401
    assert client.post("/admin/api/simulations",json={"reference_year":2026,"bonus_rate_percent":"6"}).status_code == 401
    assert client.post("/admin/api/register").status_code == 404
    assert client.post("/admin/api/login",json={"email":"admin@test.example","password":"bad"}).status_code == 401


def test_create_admin_validation(client):
    with pytest.raises(ValueError): create_admin("x@y.z","short")
    create_admin("manager@test.example", "Another Strong Pass 2026")
    with pytest.raises(ValueError): create_admin("manager@test.example","Another Strong Pass 2026")
    with connect_admin() as c:
        record=c.execute("SELECT password_digest,password_salt FROM admin_users").fetchone()
        assert "Another Strong Pass" not in record["password_digest"]
        assert len(record["password_salt"]) == 32


def test_login_and_session_security(client):
    csrf=login(client)
    assert client.get("/admin/api/me").json()["role"] == "admin"
    cookies=client.cookies
    assert "sportpulse_admin_session" in cookies
    bad=client.post("/admin/api/simulations",json={"reference_year":2026,"bonus_rate_percent":6})
    assert bad.status_code == 403
    another=client.post("/admin/api/simulations",json={"reference_year":2026,"bonus_rate_percent":6},headers={"X-CSRF-Token":"invalid"})
    assert another.status_code == 403
    good=client.post("/admin/api/simulations",json={"reference_year":2026,"bonus_rate_percent":6},headers={"X-CSRF-Token":csrf})
    assert good.status_code == 201
    assert good.json()["run_status"] == "SIMULATION_ONLY"
    logout=client.post("/admin/api/logout",headers={"X-CSRF-Token":csrf})
    assert logout.status_code == 200
    assert client.get("/admin/api/me").status_code == 401


def test_budgets_versioned_history(client):
    csrf=login(client)
    r=client.post("/admin/api/simulations",json={"source":"demo","reference_year":2026,"bonus_rate_percent":6},headers={"X-CSRF-Token":csrf})
    assert r.status_code==201, r.text
    data=r.json()
    assert data["previous_rate"]=="0.05"
    assert data["simulated_rate"]=="0.06"
    # Somme 32000 + 42000 + 56000 = 130000, 5% = 6500, 6% = 7800.
    assert data["previous_budget_eur"] == "6500.00"
    assert data["new_budget_eur"] == "7800.00"
    assert data["budget_delta_eur"] == "1300.00"
    assert data["eligible_candidates"] == 3
    r2=client.post("/admin/api/simulations",json={"reference_year":2026,"bonus_rate_percent":7},headers={"X-CSRF-Token":csrf})
    assert r2.status_code==201
    assert r2.json()["run_id"] != data["run_id"]
    hist=client.get("/admin/api/simulations").json()["items"]
    assert len(hist)==2
    assert {h["run_id"] for h in hist} == {data["run_id"],r2.json()["run_id"]}
    assert all(h["run_status"]=="SIMULATION_ONLY" for h in hist)
    assert client.get("/admin/api/audit").status_code == 200


def test_rh_viewer_cannot_change_budget_or_access_audit(client):
    login(client,email="viewer@test.example",role="rh_viewer")
    assert client.get("/admin/api/overview").status_code == 200
    assert client.get("/admin/api/simulations").status_code == 200
    assert client.post("/admin/api/simulations",json={"reference_year":2026,"bonus_rate_percent":6},headers={"X-CSRF-Token":"any"}).status_code==403
    assert client.get("/admin/api/audit").status_code == 403


def test_demo_mode_off_denies_demo_access(client, monkeypatch):
    login(client)
    monkeypatch.setenv("ADMIN_DEMO_MODE", "false")
    assert client.get("/admin/api/overview?source=demo").status_code == 403


def test_rate_validation_and_no_write(client):
    csrf=login(client)
    payload={"source":"demo","reference_year":2026,"bonus_rate_percent":30}
    assert client.post("/admin/api/simulations",json=payload,headers={"X-CSRF-Token":csrf}).status_code == 422
    payload["bonus_rate_percent"] = 6
    payload["employee_id"] = "EMP-OTHER"
    assert client.post("/admin/api/simulations",json=payload,headers={"X-CSRF-Token":csrf}).status_code == 422


def test_origin_protection(client):
    csrf=login(client)
    r=client.post("/admin/api/simulations",json={"reference_year":2026,"bonus_rate_percent":6},
         headers={"X-CSRF-Token":csrf,"Origin":"https://evil.example"})
    assert r.status_code==403


def test_csrf_refresh_and_compensation_math(client):
    old=login(client)
    refreshed=client.post("/admin/api/csrf")
    assert refreshed.status_code == 200
    new=refreshed.json()["csrf_token"]
    assert new != old
    assert client.post("/admin/api/logout",headers={"X-CSRF-Token":old}).status_code==403
    assert client.post("/admin/api/logout",headers={"X-CSRF-Token":new}).status_code==200
    snap=demo_source(2026)
    result=build_simulation(snap, Decimal("0.06"))
    assert result["budget_delta_eur"]=="1300.00"
