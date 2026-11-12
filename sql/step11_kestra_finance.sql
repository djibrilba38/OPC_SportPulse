-- SportPulse AI — Étape 11 : historique des données RH et scénarios de simulation.
-- EXÉCUTER après benefits_schema.sql; aucune prime n'est validée par ces tables.
BEGIN;
CREATE SCHEMA IF NOT EXISTS rh;
REVOKE ALL ON SCHEMA rh FROM PUBLIC;

CREATE TABLE IF NOT EXISTS rh.import_batches (
  import_id TEXT PRIMARY KEY,
  reference_year SMALLINT NOT NULL CHECK (reference_year BETWEEN 2020 AND 2100),
  source_hash CHAR(64) NOT NULL,
  source_label TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'provided_rh_file',
  employee_count INTEGER NOT NULL CHECK(employee_count >= 0),
  active BOOLEAN NOT NULL DEFAULT FALSE,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(reference_year, source_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS rh_one_active_per_year ON rh.import_batches(reference_year) WHERE active;

CREATE TABLE IF NOT EXISTS rh.employee_versions (
  import_id TEXT NOT NULL REFERENCES rh.import_batches(import_id) ON DELETE RESTRICT,
  employee_id TEXT NOT NULL,
  department TEXT NOT NULL,
  annual_gross_salary NUMERIC(12,2) NOT NULL CHECK(annual_gross_salary > 0),
  commute_mode TEXT NOT NULL,
  commute_class TEXT NOT NULL CHECK(commute_class IN ('sport','non_sport','review')),
  declared_sport TEXT,
  sporting_commute_candidate BOOLEAN NOT NULL,
  PRIMARY KEY(import_id, employee_id)
);
CREATE INDEX IF NOT EXISTS rh_employee_versions_employee ON rh.employee_versions(employee_id);

CREATE TABLE IF NOT EXISTS rh.budget_scenarios (
  scenario_id TEXT PRIMARY KEY,
  import_id TEXT NOT NULL REFERENCES rh.import_batches(import_id) ON DELETE RESTRICT,
  reference_year SMALLINT NOT NULL,
  rules_version TEXT NOT NULL,
  bonus_rate NUMERIC(6,4) NOT NULL CHECK(bonus_rate BETWEEN 0 AND 1),
  employee_count INTEGER NOT NULL,
  sporting_commute_candidate_count INTEGER NOT NULL,
  candidate_bonus_budget_eur NUMERIC(16,2) NOT NULL,
  validated_trips_and_salary_count INTEGER NOT NULL,
  validated_trips_and_salary_budget_eur NUMERIC(16,2) NOT NULL,
  synthetic_wellbeing_eligible_count INTEGER NOT NULL DEFAULT 0,
  simulated_wellbeing_days INTEGER NOT NULL DEFAULT 0,
  assumed_wellbeing_daily_cost_eur NUMERIC(12,2) NOT NULL DEFAULT 0,
  simulated_wellbeing_capacity_cost_eur NUMERIC(16,2) NOT NULL DEFAULT 0,
  min_activities INTEGER NOT NULL DEFAULT 15,
  days_per_eligible INTEGER NOT NULL DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'SIMULATION_NOT_APPROVED',
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(import_id, rules_version, bonus_rate, min_activities, days_per_eligible, assumed_wellbeing_daily_cost_eur)
);

CREATE OR REPLACE VIEW rh.v_budget_simulation_history AS
SELECT scenario_id, reference_year, rules_version, bonus_rate, employee_count,
  sporting_commute_candidate_count, candidate_bonus_budget_eur,
  validated_trips_and_salary_count, validated_trips_and_salary_budget_eur,
  synthetic_wellbeing_eligible_count, simulated_wellbeing_days,
  assumed_wellbeing_daily_cost_eur, simulated_wellbeing_capacity_cost_eur,
  (candidate_bonus_budget_eur + simulated_wellbeing_capacity_cost_eur) AS combined_illustrative_cost_eur,
  status, calculated_at
FROM rh.budget_scenarios;

CREATE OR REPLACE VIEW rh.v_current_employee_overview AS
SELECT b.reference_year, v.employee_id, v.department, v.commute_mode,
       v.commute_class, v.declared_sport, v.sporting_commute_candidate,
       v.annual_gross_salary, b.import_id
FROM rh.import_batches AS b
JOIN rh.employee_versions AS v ON v.import_id=b.import_id
WHERE b.active;

REVOKE ALL ON ALL TABLES IN SCHEMA rh FROM PUBLIC;
COMMIT;
