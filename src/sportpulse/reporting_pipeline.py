"""Reporting Gold SportPulse pour l'administration (lecture seule).

Source de vérité : dbt_marts.fact_activity, rh.import_batches et rh.budget_scenarios.
Les activités du pipeline sont SYNTHÉTIQUES ; les montants sont prévisionnels.
Ne jamais utiliser cette fonction pour payer, attribuer des jours ou valider un trajet.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

from fastapi import HTTPException

SPORTS = {"Running": "Course à pied", "Walking": "Marche", "Cycling": "Vélo"}


def _money(value):
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _read_pipeline(start: date, end: date, year: int) -> dict:
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise HTTPException(503, "Connexion PostgreSQL non configurée.")
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=6) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("""
                    SELECT b.import_id, b.employee_count, b.source_type
                    FROM rh.import_batches b
                    WHERE b.active = TRUE AND b.reference_year = %s
                    LIMIT 1
                """, (year,))
                batch = cur.fetchone()
                if batch is None:
                    raise HTTPException(409, "Aucun import RH actif pour cette année. Lance d'abord Kestra.")
                import_id, declared_count, source_type = batch
                cur.execute("""
                    SELECT COUNT(*) FROM rh.employee_versions
                    WHERE import_id = %s
                """, (import_id,))
                actual_count = int(cur.fetchone()[0])
                if actual_count != int(declared_count):
                    raise HTTPException(409, "Référentiel RH incomplet : contrôle des imports nécessaire.")
                # Les faits Gold sont générés par dbt, sans salary ni identité affichée.
                cur.execute("""
                    SELECT f.activity_id::text, f.employee_id::text, f.activity_type,
                           f.started_at, f.duration_min, f.distance_m
                    FROM dbt_marts.fact_activity AS f
                    JOIN rh.employee_versions AS e
                      ON e.employee_id=f.employee_id AND e.import_id=%s
                    WHERE f.started_at >= %s AND f.started_at < %s
                    ORDER BY f.started_at DESC, f.activity_id
                """, (import_id, start, end))
                imported = [
                    {"id": "gold:" + str(r[0]), "account_id": str(r[1]),
                     "activity_type": SPORTS.get(str(r[2]), str(r[2])),
                     "started_at": r[3].isoformat(), "duration_min": int(r[4]),
                     "distance_km": float(r[5])/1000 if r[5] is not None else None,
                     "valid_for_threshold": True, "status": "simulation_synthetique",
                     "slack_status": "sans_publication"}
                    for r in cur.fetchall()
                ]
                # Les nouveaux enregistrements du portail, s'ils existent, sont
                # distincts et reconnaissables ; pas de rapprochement implicite.
                cur.execute("""
                    SELECT a.activity_id::text,a.employee_id::text,a.activity_type,
                           a.activity_date,a.duration_min,a.distance_km,a.outside_work
                    FROM benefits.wellbeing_activity_declarations AS a
                    JOIN rh.employee_versions AS e
                      ON e.employee_id=a.employee_id AND e.import_id=%s
                    WHERE a.activity_date >= %s AND a.activity_date < %s
                      AND a.deleted_at IS NULL
                    ORDER BY a.activity_date DESC, a.activity_id
                """, (import_id, start, end))
                portal = [
                    {"id": "portal:" + str(r[0]), "account_id": str(r[1]),
                     "activity_type": SPORTS.get(str(r[2]), str(r[2])),
                     "started_at": r[3].isoformat(), "duration_min": int(r[4]),
                     "distance_km": float(r[5]) if r[5] is not None else None,
                     "valid_for_threshold": bool(r[6]), "status": "declaration_portail",
                     "slack_status": "non_suivi_ici"}
                    for r in cur.fetchall()
                ]
                # Ne pas reconstruire un budget depuis la SQLite d'administration.
                cur.execute("""
                    SELECT s.scenario_id,s.rules_version,s.bonus_rate,
                           s.sporting_commute_candidate_count,s.candidate_bonus_budget_eur,
                           s.validated_trips_and_salary_count,
                           s.validated_trips_and_salary_budget_eur,
                           s.synthetic_wellbeing_eligible_count,
                           s.simulated_wellbeing_days,
                           s.simulated_wellbeing_capacity_cost_eur,
                           s.calculated_at
                    FROM rh.budget_scenarios AS s
                    WHERE s.import_id=%s AND s.reference_year=%s
                    ORDER BY s.calculated_at DESC, s.scenario_id DESC
                    LIMIT 1
                """, (import_id, year))
                finance = cur.fetchone()
                cur.execute("""
                    SELECT COUNT(*) FROM benefits.sport_commute_declarations
                    WHERE commute_date >= %s AND commute_date < %s
                      AND validation_status = 'validated'
                      AND office_arrival_confirmed = TRUE
                """, (start, end))
                commutes_validated = int(cur.fetchone()[0])
        return {"import_id": str(import_id), "employee_count": actual_count,
                "source_type": str(source_type), "imported": imported, "portal": portal,
                "financial": finance, "commutes_validated": commutes_validated}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Reporting Gold indisponible : vérifier PostgreSQL, dbt et les migrations RH.") from exc


def build_pipeline_report(year: int) -> dict:
    from sportpulse.annual_reporting import aggregate_report, reporting_window, _cost_assumption
    start, end = reporting_window(year)
    data = _read_pipeline(start, end, year)
    entries = data["imported"] + data["portal"]
    report = aggregate_report(entries, [], data["employee_count"], "postgres", year,
                              financial_scenario=None, daily_cost=_cost_assumption())
    report["source_label"] = "POSTGRESQL — Gold dbt + référentiel RH (activités historiques synthétiques)"
    report["data_origin"] = "dbt_marts.fact_activity + benefits.wellbeing_activity_declarations"
    report["import_id"] = data["import_id"]
    report["pipeline"] = {
        "imported_synthetic_activities": len(data["imported"]),
        "portal_declarations": len(data["portal"]),
        "hr_employees": data["employee_count"],
        "synthetic_activity_notice": True,
    }
    report["commutes"] = {"total": data["commutes_validated"],
                           "by_status": {"validated": data["commutes_validated"]}}
    # Les jours sont un potentiel dans les simulations, aucune preuve RH.
    report["wellbeing"]["validated_only"] = False
    report["wellbeing"]["official_entitlement"] = None
    finance = data["financial"]
    if finance:
        (scenario_id, rules, rate, candidates, candidate_budget,
         validated_count, validated_budget, synthetic_eligible,
         synthetic_days, capacity_cost, calculated_at) = finance
        report["finance"] = {
            "source": "rh.budget_scenarios", "scenario_id": str(scenario_id),
            "rate_percent": str(Decimal(str(rate)) * 100),
            "rules_version": str(rules),
            "candidates": int(candidates),
            "candidate_budget_eur": _money(candidate_budget),
            "validated_trips_count": int(validated_count),
            "validated_budget_eur": _money(validated_budget),
            "payments_confirmed_eur": None,
            "synthetic_wellbeing_eligible": int(synthetic_eligible),
            "synthetic_wellbeing_days": int(synthetic_days),
            "capacity_cost_eur": _money(capacity_cost),
            "calculated_at": calculated_at.isoformat() if calculated_at else None,
        }
        # Cohérent avec le scénario Kestra, ne représente pas des jours accordés.
        report["wellbeing"]["potential_eligible_employees"] = int(synthetic_eligible)
        report["wellbeing"]["potential_days"] = int(synthetic_days)
        report["wellbeing"]["estimated_capacity_cost_eur"] = _money(capacity_cost)
    else:
        report["finance"] = {"source": "rh.budget_scenarios", "candidate_budget_eur": None,
                             "validated_budget_eur": None, "payments_confirmed_eur": None}
    report["notes"] = [
        "Historique Gold issu des activités synthétiques préparées par Kestra : ce ne sont pas des séances physiquement vérifiées.",
        "Les déclarations récentes du portail RH sont présentées séparément des faits Gold ; contrôler les doublons métier éventuels.",
        "Les montants de primes proviennent des scénarios Kestra basés sur le fichier RH, pas de la paie exécutée.",
        "Aucune somme réellement versée n'est disponible dans les données actuelles.",
        "Les cinq journées bien-être restent une simulation, non une décision RH.",
    ]
    return report
