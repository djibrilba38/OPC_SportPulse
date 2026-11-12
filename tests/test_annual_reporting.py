"""Tests hors réseau et hors DB de production pour le module de reporting."""
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi import HTTPException

from sportpulse import annual_reporting as report


def sample_demo_db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE accounts(id INTEGER PRIMARY KEY,employee_id TEXT,display_name TEXT,is_demo INTEGER);
      CREATE TABLE demo_activities(id INTEGER PRIMARY KEY,account_id INTEGER,activity_type TEXT,
        started_at TEXT,duration_min INTEGER,distance_km REAL,comment TEXT,deleted_at INTEGER,revision INTEGER);
      CREATE TABLE demo_commutes(id INTEGER PRIMARY KEY,account_id INTEGER,day TEXT,verification_status TEXT);
      CREATE TABLE sportpulse_notifications_outbox(event_key TEXT,activity_id INTEGER,status TEXT);
    """)
    conn.executemany("INSERT INTO accounts VALUES(?,?,?,?)", [
        (1,'DEMO-A','Demo','1'),(2,'RH-B','Reel',0)
    ])
    for i in range(1, 17):
        # 15 valid demo rows and one soft-deleted row
        conn.execute("INSERT INTO demo_activities VALUES(?,?,?,?,?,?,?,?,?)",
                     (i,1,'running',f'2026-04-{i:02d}T09:00:00+00:00',30,4.5,'comment',
                      100 if i==16 else None,2 if i==16 else 1))
    conn.execute("INSERT INTO demo_activities VALUES(?,?,?,?,?,?,?,?,?)",
                 (100,2,'cycling','2026-04-01T08:00:00+00:00',70,18,'reel',None,1))
    conn.execute("INSERT INTO demo_activities VALUES(?,?,?,?,?,?,?,?,?)",
                 (101,1,'hiking','2025-10-01T08:00:00+00:00',90,11,'ancien',None,1))
    conn.execute("INSERT INTO demo_commutes VALUES(1,1,'2026-02-10','validated')")
    conn.execute("INSERT INTO demo_commutes VALUES(2,1,'2026-02-12','pending')")
    conn.execute("INSERT INTO sportpulse_notifications_outbox VALUES('demo:15',15,'sent')")
    conn.commit();conn.close()


def test_demo_annual_excludes_soft_deleted_and_real_accounts(tmp_path, monkeypatch):
    db=tmp_path/'portal.sqlite';sample_demo_db(db)
    monkeypatch.setattr(report.portal,'db_path',lambda:db)
    monkeypatch.setenv('PORTAL_SOCIAL_YEAR_START_MONTH','1')
    activities,commutes,staff=report.demo_rows(*report.reporting_window(2026))
    outcome=report.aggregate_report(activities,commutes,staff,'demo',2026,daily_cost=Decimal('220'))
    assert outcome['activity_count']==15
    assert outcome['distance_km']==67.5
    assert outcome['duration_min']==450
    assert outcome['employee_count_reference']==1
    assert outcome['wellbeing']['potential_eligible_employees']==1
    assert outcome['wellbeing']['potential_days']==5
    assert outcome['wellbeing']['estimated_capacity_cost_eur']=='1100.00'
    assert outcome['slack_status']['sent']==1
    assert outcome['commutes']['by_status']['validated']==1
    assert not any(x['activity_id']=='100' for x in outcome['latest_activities'])
    assert not any(x['activity_id']=='16' for x in outcome['latest_activities'])


def test_update_then_delete_reflects_in_report(tmp_path,monkeypatch):
    db=tmp_path/'portal.sqlite';sample_demo_db(db)
    monkeypatch.setattr(report.portal,'db_path',lambda:db)
    monkeypatch.setenv('PORTAL_SOCIAL_YEAR_START_MONTH','1')
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE demo_activities SET distance_km=10 WHERE id=15')
    activities,commutes,n=report.demo_rows(*report.reporting_window(2026))
    first=report.aggregate_report(activities,commutes,n,'demo',2026)
    assert first['distance_km']==73.0
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE demo_activities SET deleted_at=123456 WHERE id=15')
    activities,commutes,n=report.demo_rows(*report.reporting_window(2026))
    second=report.aggregate_report(activities,commutes,n,'demo',2026)
    assert second['distance_km']==63.0
    assert second['activity_count']==14
    assert second['wellbeing']['potential_days']==0


def test_social_year_cross_calendar_boundary(monkeypatch):
    monkeypatch.setenv('PORTAL_SOCIAL_YEAR_START_MONTH','7')
    rows=[dict(id=1,account_id=1,activity_type='cycling',started_at='2026-12-01',duration_min=30,
               distance_km=5,status='simulated',slack_status='dry_run',valid_for_threshold=True),
          dict(id=2,account_id=1,activity_type='cycling',started_at='2027-06-30',duration_min=30,
               distance_km=5,status='simulated',slack_status='sent',valid_for_threshold=True),
          dict(id=3,account_id=1,activity_type='cycling',started_at='2027-07-01',duration_min=30,
               distance_km=5,status='simulated',slack_status='sent',valid_for_threshold=True)]
    result=report.aggregate_report(rows,[],1,'demo',2026)
    assert result['activity_count']==2
    assert result['period']['start']=='2026-07-01'
    assert result['period']['end_exclusive']=='2027-07-01'
    assert sum(row['activities'] for row in result['monthly'])==2


def test_pg_pending_not_eligible(monkeypatch):
    monkeypatch.setenv('PORTAL_SOCIAL_YEAR_START_MONTH','1')
    rows=[dict(id=i, account_id='a', activity_type='running', started_at='2026-06-01',duration_min=45,
               distance_km=7, status='pending', slack_status='sent',valid_for_threshold=False) for i in range(15)]
    result=report.aggregate_report(rows,[],1,'postgres',2026)
    assert result['activity_count']==15
    assert result['wellbeing']['potential_days']==0
    assert result['validation_status']['pending']==15


def test_financial_snapshot_read_only(tmp_path,monkeypatch):
    db=tmp_path/'admin.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE scenario_runs(run_id TEXT,rule_version INTEGER,source_fingerprint TEXT,result_json TEXT,source_kind TEXT,reference_year INTEGER,created_at TEXT)')
        conn.execute('INSERT INTO scenario_runs VALUES (?,?,?,?,?,?,?)',('run123',2,'abcdef1234567890',json.dumps({
            'reference_bonus_budget_eur':'6500.00','simulated_bonus_budget_eur':'7800.00',
            'delta_to_reference_eur':'1300.00','simulated_rate':'0.06'}),'demo',2026,'2026-10-27T09:00:00Z'))
    monkeypatch.setattr(report.admin,'database_path',lambda:db)
    scenario=report._latest_financial_scenario('demo',2026,calendar_aligned=True)
    assert scenario['simulated_bonus_eur']=='7800.00'
    assert scenario['rule_version']==2
    assert scenario['source_fingerprint']=='abcdef123456'
    assert report._latest_financial_scenario('demo',2026,calendar_aligned=False) is None


def test_admin_endpoint_requires_authentication():
    app=FastAPI();app.include_router(report.router)
    client=TestClient(app)
    response=client.get('/admin/api/reporting/annual')
    assert response.status_code==401
    assert client.get('/admin/reporting').status_code==401


def test_invalid_year_and_daily_cost(monkeypatch):
    with pytest.raises(HTTPException):report.reporting_window(1800)
    monkeypatch.setenv('SPORTPULSE_WELLBEING_DAILY_COST_EUR','-50')
    with pytest.raises(RuntimeError):report._cost_assumption()
    monkeypatch.setenv('SPORTPULSE_WELLBEING_DAILY_COST_EUR','220.10')
    assert report._cost_assumption()==Decimal('220.10')


def test_build_report_demo_http(tmp_path,monkeypatch):
    db=tmp_path/'portal.sqlite';sample_demo_db(db)
    monkeypatch.setattr(report.portal,'db_path',lambda:db)
    monkeypatch.setattr(report.admin,'database_path',lambda:tmp_path/'missing-admin.sqlite')
    monkeypatch.setenv('PORTAL_SOCIAL_YEAR_START_MONTH','1')
    monkeypatch.setenv('ADMIN_DEMO_MODE','true')
    monkeypatch.delenv('SPORTPULSE_WELLBEING_DAILY_COST_EUR',raising=False)
    monkeypatch.setattr(report.admin,'current_admin',lambda session:{'id':1,'role':'admin'} if session=='ok' else (_ for _ in ()).throw(HTTPException(401,'Not logged in')))
    app=FastAPI();app.include_router(report.router);client=TestClient(app)
    assert client.get('/admin/api/reporting/annual?source=demo&year=2026').status_code==401
    client.cookies.set(report.admin.COOKIE_NAME, 'ok')
    r=client.get('/admin/api/reporting/annual?source=demo&year=2026')
    assert r.status_code==200
    assert r.json()['activity_count']==15  # Déclarations POC comptées, sans attribution de droit RH
    assert r.json()['declared_count']==15
    assert r.json()['finance']['latest_saved_simulation'] is None
    assert r.json()['ai']['generator_tracking'] is False
