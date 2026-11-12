"""Tests integration SportPulse step 6; no real Slack/Mistral calls."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sportpulse import activity_notifications as n
from sportpulse import employee_portal as p


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_DB_PATH", str(tmp_path / "portal.sqlite"))
    monkeypatch.setenv("SPORTPULSE_SLACK_MODE", "dry_run")
    monkeypatch.setenv("SPORTPULSE_MOTIVATION_BACKEND", "template")
    monkeypatch.setenv("SLACK_CHANNEL_DEFAULT", "CDEMO")
    app = FastAPI()
    app.include_router(p.router)
    with TestClient(app) as c:
        yield c


def authenticated(client):
    code = p.create_invitation("DEMO-STEP6", "Camille Bernard", demo=True)
    res = client.post("/employee/api/register", json={
        "invite_code":code, "email":"camille@example.org", "password":"long-password-demo-42"})
    assert res.status_code == 201, res.text
    return res.json()["csrf_token"]


def activity_payload(shared=True):
    return {
        "activity_type":"running", "started_at":datetime.now(timezone.utc).isoformat(),
        "duration_min":42, "distance_km":8.4, "comment":"Mon adresse secrète n'est pas à publier",
        "share_to_slack":shared,
    }


def test_share_demo_updates_dashboard_and_creates_dry_run(client):
    csrf = authenticated(client)
    before = client.get("/employee/api/dashboard").json()
    res = client.post("/employee/api/activities", json=activity_payload(),
                      headers={"X-CSRF-Token": csrf})
    assert res.status_code == 201, res.text
    assert res.json()["slack_status"] == "queued"
    after = client.get("/employee/api/dashboard").json()
    assert after["sports"]["count"] == before["sports"]["count"] + 1
    events = client.get("/employee/api/notifications").json()["notifications"]
    assert len(events) == 1
    assert events[0]["status"] == "dry_run"
    assert "DÉMONSTRATION" in events[0]["message_text"]
    assert "adresse secrète" not in events[0]["message_text"]
    assert "Camille" in events[0]["message_text"]
    assert n.process_event("demo", events[0]["event_key"]) == "not_due_or_already_done"


def test_opt_out_does_not_publish(client):
    csrf = authenticated(client)
    res = client.post("/employee/api/activities", json=activity_payload(False),
                      headers={"X-CSRF-Token": csrf})
    assert res.status_code == 201
    assert res.json()["slack_status"] == "not_shared"
    assert client.get("/employee/api/notifications").json()["notifications"] == []


def test_notifications_reject_unauthenticated(client):
    assert client.get("/employee/api/notifications").status_code == 401


def test_slack_delivery_mock_and_route(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-secret")
    monkeypatch.setenv("SLACK_CHANNEL_DEFAULT", "CDEFAULT")
    monkeypatch.setenv("SPORTPULSE_SLACK_CHANNELS_JSON", '{"running":"CRUN"}')
    monkeypatch.setenv("SPORTPULSE_SLACK_MODE", "live")
    calls=[]
    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        class Response:
            status_code=200
            def raise_for_status(self): pass
            def json(self):return {"ok":True,"ts":"123.456"}
        return Response()
    monkeypatch.setattr(n.httpx, "post", fake_post)
    payload=n.event_payload(display_name="A @here <ab>",activity_type="running",
                            started_at="2026-10-26T18:00:00Z",duration_min=35,distance_km=6.2,is_demo=True)
    status,channel,ts,message=n._deliver(payload)
    assert (status, channel, ts) == ("sent", "CRUN", "123.456")
    assert "@here" not in message and "<ab>" not in message
    assert calls[0][1]["json"]["channel"] == "CRUN"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-secret"


def test_mistral_receives_no_identity_or_comment(monkeypatch):
    monkeypatch.setenv("SPORTPULSE_MOTIVATION_BACKEND", "mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-token")
    sent=[]
    def fake_post(url, **kwargs):
        sent.append((url, kwargs))
        class Response:
            def raise_for_status(self): pass
            def json(self):return {"choices":[{"message":{"content":"Continue comme ça !"}}]}
        return Response()
    monkeypatch.setattr(n.httpx, "post", fake_post)
    payload=n.event_payload(display_name="Camille X",activity_type="cycling",
                            started_at="2026-10-26T18:00:00Z",duration_min=30,distance_km=8,is_demo=True)
    assert "Continue" in n._motivation(payload)
    assert "Camille" not in json.dumps(sent)


def test_outbox_unique_constraint_and_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_DB_PATH", str(tmp_path/"db.sqlite"))
    monkeypatch.setenv("SPORTPULSE_SLACK_MODE", "live")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-secret")
    monkeypatch.setenv("SLACK_CHANNEL_DEFAULT", "CDEFAULT")
    with p.connect() as conn:
        n.initialize_demo_schema(conn)
        conn.execute("INSERT INTO accounts(employee_id,email,display_name,password_salt,password_digest,is_demo,created_at) VALUES (?,?,?,?,?,?,?)",
                     ("DEMO-X","x@example.org","X","s","h",1,1))
        aid=conn.execute("SELECT id FROM accounts").fetchone()[0]
        conn.execute("INSERT INTO demo_activities(account_id,activity_type,started_at,duration_min,distance_km,created_at) VALUES (?,?,?,?,?,?)",
                     (aid,"running","2026-10-26T10:00:00+00:00",30,3.5,1))
        payload=n.event_payload(display_name="X",activity_type="running",started_at="2026-10-26T10:00:00+00:00",duration_min=30,distance_km=3.5,is_demo=True)
        assert n.demo_enqueue_atomic(conn, account_id=aid,activity_id=1,payload=payload)=="demo:1"
        n.demo_enqueue_atomic(conn,account_id=aid,activity_id=1,payload=payload)
        assert conn.execute("SELECT COUNT(*) FROM sportpulse_notifications_outbox").fetchone()[0]==1
    def fail(*args,**kwargs):
        raise RuntimeError("failed")
    monkeypatch.setattr(n,"_post_message",fail)
    assert n.process_demo("demo:1")=="retry"
    with p.connect() as conn:
        status=conn.execute("SELECT status, attempts FROM sportpulse_notifications_outbox").fetchone()
        assert status["status"]=="retry" and status["attempts"]==1
    assert n.process_demo("demo:1")=="not_due_or_already_done"


def test_invalid_activity_rejected(client):
    csrf=authenticated(client)
    data=activity_payload(); data["distance_km"]=-99
    res=client.post("/employee/api/activities",json=data,headers={"X-CSRF-Token":csrf})
    assert res.status_code==422
    assert client.get("/employee/api/notifications").json()["notifications"]==[]


def test_real_enqueue_sql_uses_on_conflict():
    class FakeCursor:
        def __init__(self):self.sql="";self.args=()
        def execute(self, query, args):self.sql=query;self.args=args
    cur=FakeCursor()
    key=n.real_enqueue(cur,employee_id="S001",activity_id=555,
                       payload=n.event_payload(display_name="X",activity_type="running",started_at="2026-10-26",duration_min=20,distance_km=None,is_demo=False))
    assert key=="real:555"
    assert "ON CONFLICT" in cur.sql
    assert cur.args[1]==555


def test_demo_activity_and_outbox_roll_back_together(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_DB_PATH", str(tmp_path / "rollback.sqlite"))
    with p.connect() as conn:
        n.initialize_demo_schema(conn)
        conn.execute("INSERT INTO accounts(employee_id,email,display_name,password_salt,password_digest,is_demo,created_at) VALUES (?,?,?,?,?,?,?)",
                     ("ROLLBACK", "rollback@example.org", "X", "s", "h", 1, 1))
        aid = conn.execute("SELECT id FROM accounts").fetchone()[0]
    with pytest.raises(RuntimeError):
        with p.connect() as conn:
            n.initialize_demo_schema(conn)
            aid = conn.execute("SELECT id FROM accounts").fetchone()[0]
            cur=conn.execute("INSERT INTO demo_activities(account_id,activity_type,started_at,duration_min,distance_km,created_at) VALUES (?,?,?,?,?,?)",
                             (aid, "running", "2026-10-26T10:00:00+00:00",30,3.5,1))
            n.demo_enqueue_atomic(conn, account_id=aid, activity_id=cur.lastrowid,
                payload=n.event_payload(display_name="X", activity_type="running",started_at="2026-10-26",duration_min=30,distance_km=3.5,is_demo=True))
            raise RuntimeError("simulate DB error")
    with p.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM demo_activities").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sportpulse_notifications_outbox").fetchone()[0] == 0
