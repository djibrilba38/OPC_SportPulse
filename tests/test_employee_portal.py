"""Tests autonomes du portail salarié (aucune connexion PostgreSQL / Slack)."""
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sportpulse.employee_portal import router, create_invitation, connect, current_social_year


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTAL_DB_PATH', str(tmp_path / 'portal.sqlite'))
    monkeypatch.setenv('PORTAL_COOKIE_SECURE', 'false')
    monkeypatch.setenv('PORTAL_SOCIAL_YEAR_START_MONTH', '1')
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def create_account(client, employee_id='DEMO-001', name='Camille Démo', email='camille@example.com'):
    code = create_invitation(employee_id, name, demo=True)
    res = client.post('/employee/api/register', json={
        'invite_code': code, 'email':email, 'password':'great demo password 1234'
    })
    assert res.status_code == 201, res.text
    return code, res.json()['csrf_token']


def test_portal_html_assets(client):
    assert client.get('/employee').status_code == 200
    assert 'SportPulse' in client.get('/employee').text
    assert client.get('/employee/assets/app.js').status_code == 200
    assert client.get('/employee/assets/style.css').status_code == 200
    assert client.get('/employee/assets/../../etc/passwd').status_code in (404, 422)


def test_invitation_required_and_one_use(client):
    bad = client.post('/employee/api/register',json={
      'invite_code':'not-a-real-invitation-that-is-long','email':'x@example.com','password':'long password 12345'})
    assert bad.status_code == 400
    code, _ = create_account(client)
    again = client.post('/employee/api/register', json={
      'invite_code':code,'email':'other@example.com','password':'long password 12345'})
    assert again.status_code == 400


def test_account_isolation_and_auth_required(client):
    unauth = client.get('/employee/api/dashboard')
    assert unauth.status_code == 401
    _,csrf=create_account(client)
    first = client.get('/employee/api/dashboard')
    assert first.status_code==200
    data=first.json()
    assert data['employee']['id']=='DEMO-001'
    assert data['employee']['demo'] is True
    assert data['sports']['count']==14
    assert data['benefits']['days_potential']==0
    assert data['sports']['score_label'].startswith('Progression POC')  # Le score affiché n'est pas une preuve RH
    # Deux comptes, même application : seuls les jeux de données du compte connecté sont visibles.
    client.post('/employee/api/logout',headers={'X-CSRF-Token':csrf})
    create_account(client,employee_id='DEMO-002',name='Benoît Démo',email='benoit@example.com')
    assert client.get('/employee/api/dashboard').json()['employee']['id']=='DEMO-002'


def test_create_activity_updates_dashboard(client):
    _,csrf=create_account(client)
    payload={'activity_type':'running','started_at':datetime.now(timezone.utc).isoformat(),
             'duration_min':35,'distance_km':6.25,'comment':'Très bonne course'}
    before=client.get('/employee/api/dashboard').json()
    assert before['sports']['count']==14
    rejected=client.post('/employee/api/activities',json=payload)
    assert rejected.status_code==403
    response=client.post('/employee/api/activities',json=payload,headers={'X-CSRF-Token':csrf})
    assert response.status_code==201,response.text
    assert response.json()['slack_status']=='not_shared'
    after=client.get('/employee/api/dashboard').json()
    assert after['sports']['count']==15
    assert after['benefits']['days_potential']==5
    assert any(a['comment']=='Très bonne course' for a in after['activities'])


def test_cannot_modify_other_employee(client):
    _,csrf=create_account(client)
    payload={'activity_type':'cycling','started_at':datetime.now(timezone.utc).isoformat(),
             'duration_min':45,'distance_km':8.5,'comment':'Test'}
    payload['employee_id']='DEMO-999'  # Paramètre étranger rejeté par Pydantic? (ignored by default)
    r=client.post('/employee/api/activities',json=payload,headers={'X-CSRF-Token':csrf})
    assert r.status_code==422  # le client ne peut pas choisir un autre salarié
    data=client.get('/employee/api/dashboard').json()
    assert data['employee']['id']=='DEMO-001'
    with connect() as conn:
        ids=conn.execute('SELECT DISTINCT account_id FROM demo_activities').fetchall()
    assert len(ids)==1


def test_login_password_and_csrf_refresh(client):
    create_account(client)
    client.cookies.clear()
    bad=client.post('/employee/api/login',json={'email':'camille@example.com','password':'wrong'})
    assert bad.status_code==401
    r=client.post('/employee/api/login',json={'email':'camille@example.com','password':'great demo password 1234'})
    assert r.status_code==200
    token=r.json()['csrf_token']
    assert client.get('/employee/api/session').status_code==200
    new_csrf=client.post('/employee/api/csrf').json()['csrf_token']
    assert new_csrf!=token
    deny=client.post('/employee/api/logout',headers={'X-CSRF-Token':token})
    assert deny.status_code==403
    assert client.post('/employee/api/logout',headers={'X-CSRF-Token':new_csrf}).status_code==200
    assert client.get('/employee/api/dashboard').status_code==401


def test_activity_validation(client):
    _,csrf=create_account(client)
    invalid={'activity_type':'running','started_at':'2050-01-01T10:00:00+00:00',
             'duration_min':40,'distance_km':8}
    assert client.post('/employee/api/activities',json=invalid,headers={'X-CSRF-Token':csrf}).status_code==422
    invalid['started_at']=datetime.now(timezone.utc).isoformat()
    invalid['duration_min']=-10
    assert client.post('/employee/api/activities',json=invalid,headers={'X-CSRF-Token':csrf}).status_code==422


def test_status_privacy_headers(client):
    r=client.get('/employee')
    assert r.headers['cache-control']=='no-store'
    assert 'session' not in r.text.lower()
