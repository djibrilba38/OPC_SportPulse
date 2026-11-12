-- SportPulse AI / Étape 18 — Couche BI agrégée, sans salaires individuels.
-- Prérequis : Kestra étape 11 ; schémas benefits, rh, dbt_marts ; étape 17.
-- À exécuter avec le propriétaire des vues ayant accès aux schémas sources.
-- Aucune modification ni suppression dans les tables sources.
BEGIN;
-- Contrôle de prérequis : évite une erreur cryptique au milieu du script.
DO $$ BEGIN
  IF to_regclass('rh.import_batches') IS NULL
     OR to_regclass('rh.employee_versions') IS NULL
     OR to_regclass('rh.budget_scenarios') IS NULL
     OR to_regclass('rh.portal_account_links') IS NULL
     OR to_regclass('dbt_marts.fact_activity') IS NULL
     OR to_regclass('benefits.wellbeing_activity_declarations') IS NULL
     OR to_regclass('benefits.wellbeing_day_entitlements') IS NULL
     OR to_regclass('analytics.pipeline_runs') IS NULL THEN
    RAISE EXCEPTION 'Prérequis BI manquant : exécuter les étapes RH, dbt et 17 avant la BI.';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS powerbi;
REVOKE ALL ON SCHEMA powerbi FROM PUBLIC;

CREATE TABLE IF NOT EXISTS powerbi.settings (
    id SMALLINT PRIMARY KEY CHECK (id=1),
    social_start_month SMALLINT NOT NULL CHECK (social_start_month BETWEEN 1 AND 12)
);
INSERT INTO powerbi.settings(id, social_start_month) VALUES (1,1)
ON CONFLICT (id) DO NOTHING;
REVOKE ALL ON powerbi.settings FROM PUBLIC;

-- Année sociale : mois de départ dans powerbi.settings, janvier par défaut.
CREATE OR REPLACE VIEW powerbi.v_reporting_years AS
SELECT b.reference_year::integer AS social_year,
       make_date(b.reference_year, s.social_start_month, 1) AS period_start,
       (make_date(b.reference_year, s.social_start_month, 1) + INTERVAL '1 year')::date AS period_end_exclusive,
       s.social_start_month,
       b.imported_at,
       b.source_type
FROM rh.import_batches b
CROSS JOIN powerbi.settings s
WHERE b.active = TRUE;

-- Union de deux populations D'ACTIVITÉS, pas un constat de pratique physique réelle.
-- Pas d'employee_id ni de nom dans la couche BI.
-- Les déclarations sont limitées aux comptes reliés à l'import RH actif.
CREATE OR REPLACE VIEW powerbi.v_activity_monthly AS
WITH raw_activity AS (
    SELECT b.reference_year::integer AS social_year,
           date_trunc('month', f.started_at)::date AS month_start,
           e.department,
           CASE LOWER(f.activity_type)
             WHEN 'running' THEN 'Course à pied'
             WHEN 'walking' THEN 'Marche'
             WHEN 'cycling' THEN 'Vélo'
             ELSE f.activity_type
           END AS activity_type,
           'gold_synthetique'::text AS data_origin,
           f.duration_min::numeric AS duration_min,
           COALESCE(f.distance_m,0)::numeric / 1000 AS distance_km
    FROM rh.import_batches b
    JOIN rh.employee_versions e ON e.import_id=b.import_id
    JOIN dbt_marts.fact_activity f ON f.employee_id=e.employee_id
    CROSS JOIN powerbi.settings s
    WHERE b.active=TRUE
      AND f.started_at::date >= make_date(b.reference_year,s.social_start_month,1)
      AND f.started_at::date < (make_date(b.reference_year,s.social_start_month,1) + INTERVAL '1 year')::date
    UNION ALL
    SELECT b.reference_year::integer AS social_year,
           date_trunc('month',a.activity_date)::date AS month_start,
           e.department,
           CASE LOWER(a.activity_type)
             WHEN 'running' THEN 'Course à pied'
             WHEN 'walking' THEN 'Marche'
             WHEN 'cycling' THEN 'Vélo'
             WHEN 'bicycle' THEN 'Vélo'
             ELSE a.activity_type
           END AS activity_type,
           'portail_declare'::text AS data_origin,
           a.duration_min::numeric AS duration_min,
           COALESCE(a.distance_km,0)::numeric AS distance_km
    FROM rh.import_batches b
    JOIN rh.employee_versions e ON e.import_id=b.import_id
    JOIN rh.portal_account_links l ON l.import_id=e.import_id AND l.employee_id=e.employee_id
    JOIN benefits.wellbeing_activity_declarations a ON a.employee_id=e.employee_id
    CROSS JOIN powerbi.settings s
    WHERE b.active=TRUE
      AND a.deleted_at IS NULL
      AND a.outside_work IS TRUE
      AND a.validation_status <> 'rejected'
      AND a.activity_date >= make_date(b.reference_year,s.social_start_month,1)
      AND a.activity_date < (make_date(b.reference_year,s.social_start_month,1) + INTERVAL '1 year')::date
)
SELECT social_year,month_start,department,activity_type,data_origin,
       COUNT(*)::integer AS activity_count,
       ROUND(SUM(COALESCE(duration_min,0)),2) AS duration_min,
       ROUND(SUM(COALESCE(distance_km,0)),2) AS distance_km
FROM raw_activity
GROUP BY social_year,month_start,department,activity_type,data_origin;

-- Effectifs par département. Masquer la masse salariale pour les groupes < 5.
CREATE OR REPLACE VIEW powerbi.v_employee_department AS
SELECT b.reference_year::integer AS social_year,
       e.department,
       COUNT(*)::integer AS employee_count,
       COUNT(*) FILTER(WHERE e.sporting_commute_candidate)::integer AS sporting_commute_candidates,
       COUNT(*) FILTER(WHERE e.commute_class='review')::integer AS commute_to_review_count,
       CASE WHEN COUNT(*) >= 5
            THEN ROUND(SUM(e.annual_gross_salary),2)
            ELSE NULL::numeric END AS gross_payroll_eur_aggregated,
       CASE WHEN COUNT(*) >= 5
            THEN ROUND(SUM(e.annual_gross_salary) FILTER (WHERE e.sporting_commute_candidate),2)
            ELSE NULL::numeric END AS candidate_salary_mass_eur_aggregated,
       (COUNT(*) < 5) AS finance_suppressed_small_group
FROM rh.import_batches b
JOIN rh.employee_versions e ON e.import_id=b.import_id
WHERE b.active=TRUE
GROUP BY b.reference_year,e.department;

-- Un scénario = une ligne, Y COMPRIS les imports RH archivés.
-- Une modification de source ne doit jamais effacer l'historique de replay.
-- Ne jamais faire SUM de tous les scénarios pour estimer un budget annuel.
CREATE OR REPLACE VIEW powerbi.v_scenario_history AS
SELECT b.reference_year::integer AS social_year,
       b.import_id,b.active AS is_active_import,
       LEFT(b.source_hash,12) AS source_hash_prefix,
       s.scenario_id,s.rules_version,s.bonus_rate,
       s.calculated_at,
       (ROW_NUMBER() OVER (PARTITION BY b.import_id
                           ORDER BY s.calculated_at DESC,s.scenario_id DESC) = 1) AS is_latest,
       s.employee_count,
       s.sporting_commute_candidate_count,
       s.candidate_bonus_budget_eur,
       s.validated_trips_and_salary_count,
       s.validated_trips_and_salary_budget_eur,
       s.synthetic_wellbeing_eligible_count,
       s.simulated_wellbeing_days,
       s.simulated_wellbeing_capacity_cost_eur,
       s.assumed_wellbeing_daily_cost_eur,
       s.min_activities,s.days_per_eligible,
       (s.candidate_bonus_budget_eur + s.simulated_wellbeing_capacity_cost_eur)::numeric(16,2)
           AS combined_illustrative_cost_eur,
       s.status
FROM rh.import_batches b
JOIN rh.budget_scenarios s ON s.import_id=b.import_id AND s.reference_year=b.reference_year
;

-- Résultats salariés déclarés dans le portail : éligibilité POTENTIELLE seulement.
-- La vraie attribution RH reste une autre donnée.
CREATE OR REPLACE VIEW powerbi.v_wellbeing_year AS
WITH portal_counts AS (
    SELECT b.reference_year::integer AS social_year,a.employee_id,COUNT(*)::integer AS activity_count
    FROM rh.import_batches b
    JOIN rh.portal_account_links l ON l.import_id=b.import_id
    JOIN benefits.wellbeing_activity_declarations a ON a.employee_id=l.employee_id
    CROSS JOIN powerbi.settings s
    WHERE b.active=TRUE AND a.deleted_at IS NULL
      AND a.outside_work=TRUE AND a.validation_status <> 'rejected'
      AND a.activity_date >= make_date(b.reference_year,s.social_start_month,1)
      AND a.activity_date < (make_date(b.reference_year,s.social_start_month,1)+INTERVAL '1 year')::date
    GROUP BY b.reference_year,a.employee_id
), portal_summary AS (
    SELECT social_year,
           COUNT(*)::integer AS portal_participating_employees,
           COUNT(*) FILTER(WHERE activity_count >= 15)::integer AS portal_employees_15plus,
           COALESCE(SUM(activity_count),0)::integer AS portal_activities_outside_work
    FROM portal_counts GROUP BY social_year
), official_summary AS (
    SELECT e.reference_year::integer AS social_year,
           COUNT(*) FILTER(WHERE e.eligibility_status='eligible')::integer AS official_eligible_employees,
           COALESCE(SUM(e.days_entitled),0)::integer AS officially_entitled_days,
           COALESCE(SUM(e.days_used),0)::integer AS used_days_recorded
    FROM benefits.wellbeing_day_entitlements e
    GROUP BY e.reference_year
)
SELECT y.social_year,
       COALESCE(p.portal_participating_employees,0) AS portal_participating_employees,
       COALESCE(p.portal_employees_15plus,0) AS portal_employees_15plus,
       COALESCE(p.portal_activities_outside_work,0) AS portal_activities_outside_work,
       COALESCE(p.portal_employees_15plus,0)*5 AS portal_potential_wellbeing_days,
       COALESCE(o.official_eligible_employees,0) AS official_eligible_employees,
       COALESCE(o.officially_entitled_days,0) AS officially_entitled_days,
       COALESCE(o.used_days_recorded,0) AS used_days_recorded
FROM powerbi.v_reporting_years y
LEFT JOIN portal_summary p USING(social_year)
LEFT JOIN official_summary o USING(social_year);

-- Une ligne par année active : import simple dans Power BI Service sur Mac.
CREATE OR REPLACE VIEW powerbi.v_executive_year AS
WITH employee_total AS (
    SELECT social_year,
           SUM(employee_count)::integer AS employee_count,
           SUM(sporting_commute_candidates)::integer AS sporting_commute_candidates,
           SUM(commute_to_review_count)::integer AS commute_to_review_count
    FROM powerbi.v_employee_department GROUP BY social_year
), payroll_total AS (
    -- Masse salariale globale, jamais de salaire individuel exposé.
    SELECT b.reference_year::integer AS social_year,
           ROUND(SUM(e.annual_gross_salary),2) AS annual_gross_payroll_eur
    FROM rh.import_batches b JOIN rh.employee_versions e ON e.import_id=b.import_id
    WHERE b.active=TRUE GROUP BY b.reference_year
), sports_total AS (
    SELECT social_year,
           COALESCE(SUM(activity_count) FILTER(WHERE data_origin='gold_synthetique'),0)::integer AS gold_synthetic_activities,
           COALESCE(SUM(activity_count) FILTER(WHERE data_origin='portail_declare'),0)::integer AS portal_declared_activities,
           COALESCE(SUM(distance_km),0)::numeric(18,2) AS total_distance_km_all_sources,
           COALESCE(SUM(duration_min),0)::numeric(18,2) AS total_duration_min_all_sources
    FROM powerbi.v_activity_monthly GROUP BY social_year
)
SELECT y.social_year,y.period_start,y.period_end_exclusive,y.social_start_month,
       e.employee_count,e.sporting_commute_candidates,e.commute_to_review_count,
       p.annual_gross_payroll_eur,
       COALESCE(a.gold_synthetic_activities,0) AS gold_synthetic_activities,
       COALESCE(a.portal_declared_activities,0) AS portal_declared_activities,
       COALESCE(a.gold_synthetic_activities,0)+COALESCE(a.portal_declared_activities,0)
         AS activity_records_all_sources,
       COALESCE(a.total_distance_km_all_sources,0) AS distance_km_all_sources,
       COALESCE(a.total_duration_min_all_sources,0) AS duration_min_all_sources,
       s.scenario_id AS latest_scenario_id,s.rules_version AS latest_rules_version,
       s.bonus_rate AS latest_bonus_rate,
       s.candidate_bonus_budget_eur AS latest_candidate_bonus_budget_eur,
       s.validated_trips_and_salary_count AS latest_validated_candidate_count,
       s.validated_trips_and_salary_budget_eur AS latest_validated_bonus_budget_eur,
       s.synthetic_wellbeing_eligible_count AS latest_synthetic_wellbeing_eligible,
       s.simulated_wellbeing_days AS latest_simulated_wellbeing_days,
       s.simulated_wellbeing_capacity_cost_eur AS latest_capacity_cost_eur,
       s.combined_illustrative_cost_eur AS latest_total_illustrative_eur,
       w.portal_employees_15plus,w.portal_potential_wellbeing_days,
       w.officially_entitled_days,w.used_days_recorded,
       'POC_ESTIMATIONS_NON_VERSEES'::text AS payment_status
FROM powerbi.v_reporting_years y
LEFT JOIN employee_total e USING(social_year)
LEFT JOIN payroll_total p USING(social_year)
LEFT JOIN sports_total a USING(social_year)
LEFT JOIN powerbi.v_scenario_history s ON s.social_year=y.social_year
                                         AND s.is_latest=TRUE AND s.is_active_import=TRUE
LEFT JOIN powerbi.v_wellbeing_year w ON w.social_year = y.social_year;

-- Historique opérationnel sans données RH ni détails de secrets ou manifestes.
CREATE OR REPLACE VIEW powerbi.v_pipeline_status AS
SELECT pipeline,status,started_at,finished_at,
       EXTRACT(EPOCH FROM (finished_at-started_at))::numeric(14,2) AS duration_seconds
FROM analytics.pipeline_runs;

-- Rôle NOLOGIN à attribuer explicitement à un compte de lecture Power BI.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='sportpulse_powerbi_reader') THEN
    CREATE ROLE sportpulse_powerbi_reader NOLOGIN;
  END IF;
END $$;
GRANT USAGE ON SCHEMA powerbi TO sportpulse_powerbi_reader;
GRANT SELECT ON powerbi.v_reporting_years,
                powerbi.v_activity_monthly,
                powerbi.v_employee_department,
                powerbi.v_scenario_history,
                powerbi.v_wellbeing_year,
                powerbi.v_executive_year,
                powerbi.v_pipeline_status
TO sportpulse_powerbi_reader;
REVOKE ALL ON powerbi.settings FROM PUBLIC;
COMMIT;
