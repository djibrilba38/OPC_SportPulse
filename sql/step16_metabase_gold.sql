-- SportPulse AI 16 : vues agrégées pour Metabase.
-- Aucune donnée nominative ni salaire individuel ; jamais de DROP ou DELETE.
BEGIN;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE VIEW analytics.v_sp16_gold_activity_by_month AS
SELECT b.reference_year,
       date_trunc('month', f.started_at)::date AS activity_month,
       f.activity_type,
       COUNT(*)::integer AS synthetic_activity_count,
       COUNT(DISTINCT f.employee_id)::integer AS participating_employees,
       ROUND((SUM(COALESCE(f.distance_m,0))::numeric / 1000), 2) AS distance_km,
       SUM(f.duration_min)::bigint AS duration_min,
       'synthetic_kestra_gold'::text AS data_origin
FROM dbt_marts.fact_activity f
JOIN rh.import_batches b ON b.active = TRUE
JOIN rh.employee_versions e ON e.import_id=b.import_id AND e.employee_id=f.employee_id
WHERE f.started_at >= make_date(b.reference_year,1,1)
  AND f.started_at < make_date(b.reference_year+1,1,1)
GROUP BY b.reference_year, date_trunc('month',f.started_at)::date, f.activity_type;

CREATE OR REPLACE VIEW analytics.v_sp16_portal_activity_by_month AS
SELECT b.reference_year,
       date_trunc('month',a.activity_date)::date AS activity_month,
       a.activity_type,
       COUNT(*)::integer AS portal_activity_count,
       COUNT(DISTINCT a.employee_id)::integer AS participating_employees,
       ROUND(SUM(COALESCE(a.distance_km,0))::numeric,2) AS distance_km,
       SUM(a.duration_min)::bigint AS duration_min,
       'portal_declaration_poc'::text AS data_origin
FROM benefits.wellbeing_activity_declarations a
JOIN rh.import_batches b ON b.active=TRUE
JOIN rh.employee_versions e ON e.import_id=b.import_id AND e.employee_id=a.employee_id
WHERE a.deleted_at IS NULL
  AND a.activity_date >= make_date(b.reference_year,1,1)
  AND a.activity_date < make_date(b.reference_year+1,1,1)
GROUP BY b.reference_year,date_trunc('month',a.activity_date)::date,a.activity_type;

CREATE OR REPLACE VIEW analytics.v_sp16_bonus_budget AS
SELECT DISTINCT ON (b.import_id)
  b.reference_year,b.import_id,b.employee_count,s.calculated_at,s.rules_version,
  s.bonus_rate,s.sporting_commute_candidate_count,
  s.candidate_bonus_budget_eur,s.validated_trips_and_salary_count,
  s.validated_trips_and_salary_budget_eur,s.synthetic_wellbeing_eligible_count,
  s.simulated_wellbeing_days,s.simulated_wellbeing_capacity_cost_eur,
  'ESTIMATION_ONLY_NOT_PAID'::text AS payment_status
FROM rh.import_batches b
JOIN rh.budget_scenarios s ON s.import_id=b.import_id AND s.reference_year=b.reference_year
WHERE b.active=TRUE
ORDER BY b.import_id,s.calculated_at DESC,s.scenario_id DESC;

REVOKE ALL ON analytics.v_sp16_gold_activity_by_month FROM PUBLIC;
REVOKE ALL ON analytics.v_sp16_portal_activity_by_month FROM PUBLIC;
REVOKE ALL ON analytics.v_sp16_bonus_budget FROM PUBLIC;
COMMIT;
