"""Pont Kestra vers le pipeline existant SportPulse. Une tâche Kestra par étape.

Usage : python -m sportpulse.kestra_bridge prepare|bronze|silver|quality|load-silver|
        gold|load-gold|import-rh|scenario|verify [...]
Toutes les sorties finales sont agrégées : aucun nom, salaire individuel ou secret dans les logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from sportpulse.source_prep import make_source_bundle


def kestra_vars(**values):
    # Format d'output pris en charge par les tâches Kestra.
    print('::' + json.dumps({'outputs': values}, ensure_ascii=False, separators=(',', ':')) + '::', flush=True)


def execute(command: list[str]) -> dict:
    p = subprocess.run([sys.executable, '-m', *command], text=True, capture_output=True, check=False)
    if p.returncode:
        # Pas de secrets ni de PII dans le résumé d'erreur du flow : voir journaux privés app.
        print(p.stderr[-2500:], file=sys.stderr)
        raise RuntimeError(f'Étape {command[0]} en échec (exit {p.returncode})')
    try:
        result = json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'{command[0]} n’a pas retourné de JSON parsable') from exc
    if result.get('status') not in ('SUCCESS', 'SUCCESS_WITH_REJECTS'):
        raise RuntimeError(f'{command[0]} : statut non accepté : {result.get("status")}')
    return result


def connection():
    import psycopg
    uri = os.getenv('DATABASE_URL', '').strip()
    if not uri:
        raise RuntimeError('DATABASE_URL doit être défini dans le service Kestra')
    return psycopg.connect(uri, connect_timeout=10)


def import_rh(snapshot_file: Path) -> dict:
    import psycopg
    data = json.loads(snapshot_file.read_text(encoding='utf-8'))
    records = data['records']
    year = int(data['reference_year'])
    h = hashlib.sha256(json.dumps({'year': year, 'records': records}, ensure_ascii=False,
                                  sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    imp = f'rh-{year}-{h[:20]}'
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(%s)',(year,))
                cur.execute("SELECT to_regclass('rh.import_batches'), to_regclass('benefits.employee_compensation')")
                a, b = cur.fetchone()
                if not a or not b:
                    raise RuntimeError('Appliquer benefits_schema.sql et step11_kestra_finance.sql avant l’import RH')
                cur.execute('SELECT EXISTS(SELECT 1 FROM rh.import_batches WHERE import_id=%s)',(imp,))
                is_existing = bool(cur.fetchone()[0])
                if not is_existing:
                    cur.execute('''INSERT INTO rh.import_batches(import_id,reference_year,source_hash,source_label,employee_count,active)
                        VALUES (%s,%s,%s,%s,%s,FALSE)''',(imp,year,h,'Classeur RH fourni au POC',len(records)))
                    for rec in records:
                        cur.execute('''INSERT INTO rh.employee_versions(import_id,employee_id,department,annual_gross_salary,commute_mode,commute_class,declared_sport,sporting_commute_candidate)
                          VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                         (imp,rec['employee_id'],rec['department'],rec['annual_gross_salary'],rec['commute_mode'],rec['commute_class'],rec['declared_sport_original'] or None,rec['sporting_commute_candidate']))
                cur.execute('UPDATE rh.import_batches SET active=FALSE WHERE reference_year=%s AND active',(year,))
                cur.execute('UPDATE rh.import_batches SET active=TRUE WHERE import_id=%s',(imp,))
                for rec in records:
                    cur.execute('''INSERT INTO benefits.employee_compensation(employee_id,reference_year,annual_gross_salary,currency,salary_validated)
                    VALUES(%s,%s,%s,'EUR',FALSE)
                    ON CONFLICT(employee_id,reference_year) DO UPDATE SET
                      annual_gross_salary=EXCLUDED.annual_gross_salary,
                      salary_validated=CASE WHEN benefits.employee_compensation.annual_gross_salary=EXCLUDED.annual_gross_salary
                        THEN benefits.employee_compensation.salary_validated ELSE FALSE END,
                      updated_at=NOW()''', (rec['employee_id'],year,rec['annual_gross_salary']))
    return {'import_id': imp, 'employees_imported': len(records), 'new_snapshot': not is_existing,
            'reference_year': year}


def simulate_budget(year: int, rate_raw: str, version: str, min_activities: int = 15,
                    days_per_eligible: int = 5, daily_cost_raw: str = '220.00') -> dict:
    try:
        rate = Decimal(rate_raw)
    except InvalidOperation as exc:
        raise ValueError('Taux non numérique') from exc
    if not rate.is_finite() or not Decimal('0') <= rate <= Decimal('1') or rate.as_tuple().exponent < -4:
        raise ValueError('Taux invalide : 0..1 et au plus 4 décimales')
    if not version or len(version) > 100:
        raise ValueError('Version de règles absente ou trop longue')
    if not 1 <= min_activities <= 3650 or not 0 <= days_per_eligible <= 30:
        raise ValueError('Règle jours bien-être invalide')
    try:
        daily_cost=Decimal(daily_cost_raw)
    except InvalidOperation as exc:
        raise ValueError('Coût journalier invalide') from exc
    if not daily_cost.is_finite() or daily_cost<0 or daily_cost>Decimal('10000'):
        raise ValueError('Coût journalier hors plage')
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT import_id FROM rh.import_batches WHERE reference_year=%s AND active''', (year,))
            row=cur.fetchone()
            if not row:
                raise RuntimeError(f'Aucun référentiel RH actif pour {year}')
            imp=row[0]
            cur.execute('''WITH trip_validation AS (
              SELECT DISTINCT employee_id FROM benefits.sport_commute_declarations
              WHERE validation_status='validated' AND office_arrival_confirmed
                AND commute_date >= make_date(%s,1,1) AND commute_date < make_date(%s+1,1,1)
              )
              SELECT COUNT(*)::INT,
                COUNT(*) FILTER (WHERE e.sporting_commute_candidate)::INT,
                COALESCE(SUM(e.annual_gross_salary) FILTER (WHERE e.sporting_commute_candidate),0),
                COUNT(*) FILTER (WHERE e.sporting_commute_candidate AND t.employee_id IS NOT NULL AND c.salary_validated)::INT,
                COALESCE(SUM(e.annual_gross_salary) FILTER (
                  WHERE e.sporting_commute_candidate AND t.employee_id IS NOT NULL AND c.salary_validated),0)
              FROM rh.employee_versions e
              JOIN benefits.employee_compensation c ON c.employee_id=e.employee_id AND c.reference_year=%s
              LEFT JOIN trip_validation t ON t.employee_id=e.employee_id
              WHERE e.import_id=%s''', (year, year, year, imp))
            n, n_candidates, salary_candidates, n_validated, salary_validated=cur.fetchone()
            cur.execute('''SELECT COUNT(*) FROM (
                SELECT employee_id FROM staging.silver_activities
                WHERE started_at >= make_timestamptz(%s,1,1,0,0,0,'UTC')
                  AND started_at < make_timestamptz(%s+1,1,1,0,0,0,'UTC')
                GROUP BY employee_id HAVING COUNT(*) >= %s
                ) AS eligible_synthetic''',(year,year,min_activities))
            wellbeing_count=int(cur.fetchone()[0])
            simulated_days=wellbeing_count*days_per_eligible
            cent = Decimal('0.01')
            budget = (Decimal(salary_candidates)*rate).quantize(cent,rounding=ROUND_HALF_UP)
            verified = (Decimal(salary_validated)*rate).quantize(cent,rounding=ROUND_HALF_UP)
            capacity_cost=(Decimal(simulated_days)*daily_cost).quantize(cent,rounding=ROUND_HALF_UP)
            scenario_id = hashlib.sha256(f'{imp}|{year}|{version}|{rate:.4f}|{min_activities}|{days_per_eligible}|{daily_cost:.2f}'.encode()).hexdigest()[:24]
            cur.execute('''INSERT INTO rh.budget_scenarios
              (scenario_id,import_id,reference_year,rules_version,bonus_rate,employee_count,
               sporting_commute_candidate_count,candidate_bonus_budget_eur,
               validated_trips_and_salary_count,validated_trips_and_salary_budget_eur,
               synthetic_wellbeing_eligible_count,simulated_wellbeing_days,
               assumed_wellbeing_daily_cost_eur,simulated_wellbeing_capacity_cost_eur,
               min_activities,days_per_eligible)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(scenario_id) DO NOTHING''',
              (scenario_id,imp,year,version,rate,n,n_candidates,budget,n_validated,verified,
               wellbeing_count,simulated_days,daily_cost,capacity_cost,min_activities,days_per_eligible))
        conn.commit()
    return {'scenario_id':scenario_id,'import_id':imp,'reference_year':year,
            'rules_version':version,'rate':str(rate), 'employee_count':n,
            'sporting_commute_candidate_count':n_candidates,
            'candidate_bonus_budget_eur':str(budget),
            'validated_trips_and_salary_count':n_validated,
            'validated_trips_and_salary_budget_eur':str(verified),
            'synthetic_wellbeing_eligible_count':wellbeing_count,
            'simulated_wellbeing_days':simulated_days,
            'simulated_wellbeing_capacity_cost_eur':str(capacity_cost),
            'combined_illustrative_cost_eur':str(budget+capacity_cost),
            'warning':'ESTIMATION, ne vaut pas attribution RH ni paiement'}


def quality_gate(manifest_uri: str, max_rejected: int):
    from sportpulse.settings import get_settings
    from sportpulse.storage import create_s3_client
    from sportpulse.transform import _parse_s3_uri
    cfg=get_settings()
    bucket,key=_parse_s3_uri(manifest_uri)
    if bucket!=cfg.bucket_artifacts or not key.startswith('transform-manifests/'):
        raise ValueError('Manifeste Silver non autorisé')
    s3=create_s3_client(cfg)
    manifest=json.loads(s3.get_object(Bucket=bucket,Key=key)['Body'].read())
    summary=manifest['quality_summary']
    count=int(summary['total_rejected_rows'])
    if count>max_rejected:
        raise RuntimeError(f'Contrôle Silver bloquant : {count} ligne(s) rejetée(s), plafond {max_rejected}')
    return {'accepted_rows':int(summary['total_accepted_rows']), 'rejected_rows':count,
            'status':manifest['status']}


def verify(year: int, scenario_id: str) -> dict:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT employee_count FROM rh.import_batches WHERE reference_year=%s AND active''',(year,))
            row=cur.fetchone()
            if not row:
                raise RuntimeError('Aucun référentiel RH actif')
            expected=int(row[0])
            checks={}
            for key, query in (
                ('silver_employees','SELECT COUNT(*) FROM staging.silver_employees'),
                ('silver_activities','SELECT COUNT(*) FROM staging.silver_activities'),
                ('rh_active','SELECT COUNT(*) FROM rh.v_current_employee_overview WHERE reference_year=%s'),
                ('compensation','SELECT COUNT(*) FROM benefits.employee_compensation WHERE reference_year=%s'),
                ('gold_active_employees','SELECT COUNT(*) FROM analytics.by_employee'),
            ):
                cur.execute(query, (year,) if '%s' in query else ())
                checks[key]=int(cur.fetchone()[0])
            cur.execute('SELECT COUNT(*) FROM rh.budget_scenarios WHERE scenario_id=%s',(scenario_id,))
            checks['scenario_saved']=bool(cur.fetchone()[0])
            if checks['silver_employees']!=expected or checks['rh_active']!=expected or checks['compensation']<expected or not checks['scenario_saved']:
                raise RuntimeError(f'Comptages incohérents avec {expected} salarié(s) RH : {checks}')
            if checks['silver_activities']<=0:
                raise RuntimeError('Aucune activité Silver chargée')
    return {'expected_employees':expected, **checks,'status':'PASS',
            'notice':'Les activités sont synthétiques; les primes ne sont pas accordées'}


def parser():
    p=argparse.ArgumentParser(description='Adaptateur Kestra à SportPulse AI')
    sub=p.add_subparsers(dest='stage',required=True)
    a=sub.add_parser('prepare'); a.add_argument('--rh',required=True); a.add_argument('--sport',required=True)
    a.add_argument('--run-id',required=True); a.add_argument('--year',type=int,required=True)
    a.add_argument('--seed',type=int,default=42)
    a=sub.add_parser('bronze'); a.add_argument('--input-dir',required=True)
    for name in ('silver','quality','load-silver','gold','load-gold'):
        a=sub.add_parser(name); a.add_argument('--manifest-uri',required=True)
        if name=='quality': a.add_argument('--max-rejected',type=int,default=0)
    a=sub.add_parser('import-rh'); a.add_argument('--snapshot',required=True)
    a=sub.add_parser('scenario'); a.add_argument('--year',type=int,required=True)
    a.add_argument('--rate',required=True); a.add_argument('--rules-version',required=True)
    a.add_argument('--min-activities',type=int,default=15)
    a.add_argument('--days-per-eligible',type=int,default=5)
    a.add_argument('--daily-cost-eur',default='220.00')
    a=sub.add_parser('verify'); a.add_argument('--year',type=int,required=True)
    a.add_argument('--scenario-id',required=True)
    return p


def main():
    a=parser().parse_args()
    if a.stage=='prepare':
        payload=make_source_bundle(Path(a.rh),Path(a.sport),Path('/app/data'),a.year,a.run_id,a.seed)
        print(f'PREPARE OK | salariés={payload["employee_count"]} | activités SIMULÉES={payload["simulated_activity_count"]}')
        kestra_vars(source_dir=payload['source_dir'], rh_snapshot=payload['rh_snapshot'],
                   employee_count=payload['employee_count'])
    elif a.stage=='bronze':
        r=execute(['sportpulse.cli','ingest','--input-dir',a.input_dir])
        kestra_vars(manifest_uri=r['manifest_uri'],source_count=r['source_count'])
    elif a.stage=='silver':
        r=execute(['sportpulse.cli','transform','--manifest-uri',a.manifest_uri])
        kestra_vars(manifest_uri=r['transform_manifest_uri'], accepted_rows=r['total_accepted_rows'],rejected_rows=r['total_rejected_rows'])
    elif a.stage=='quality':
        r=quality_gate(a.manifest_uri,a.max_rejected)
        print('Qualité Silver',json.dumps(r)); kestra_vars(**r)
    elif a.stage=='load-silver':
        r=execute(['sportpulse.load_silver_postgres','--manifest-uri',a.manifest_uri])
        kestra_vars(**r['rows'])
    elif a.stage=='gold':
        r=execute(['sportpulse.cli','gold','--manifest-uri',a.manifest_uri])
        kestra_vars(manifest_uri=r['gold_manifest_uri'])
    elif a.stage=='load-gold':
        r=execute(['sportpulse.load_postgres','--manifest-uri',a.manifest_uri])
        kestra_vars(**r['rows'])
    elif a.stage=='import-rh':
        r=import_rh(Path(a.snapshot)); print('Import RH',json.dumps(r)); kestra_vars(**r)
    elif a.stage=='scenario':
        r=simulate_budget(a.year,a.rate,a.rules_version,a.min_activities,a.days_per_eligible,a.daily_cost_eur)
        print('Simulation financière',json.dumps(r,ensure_ascii=False)); kestra_vars(**r)
    elif a.stage=='verify':
        r=verify(a.year,a.scenario_id); print('Contrôle final',json.dumps(r)); kestra_vars(**r)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f'ÉCHEC étape Kestra : {type(e).__name__}: {e}',file=sys.stderr)
        raise SystemExit(2)
