-- SportPulse AI — Étape 10 — historique des sources RH, sans paiement.
-- Requiert benefits_schema.sql et tables benefits.employee_compensation.
BEGIN;
CREATE SCHEMA IF NOT EXISTS rh;
REVOKE ALL ON SCHEMA rh FROM PUBLIC;

CREATE TABLE IF NOT EXISTS rh.import_batches (
    import_id TEXT PRIMARY KEY,
    reference_year SMALLINT NOT NULL CHECK (reference_year BETWEEN 2020 AND 2100),
    source_hash CHAR(64) NOT NULL,
    source_label TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'provided_rh_file',
    employee_count INTEGER NOT NULL CHECK (employee_count >= 0),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(reference_year, source_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS rh_one_active_per_year
    ON rh.import_batches(reference_year) WHERE active;

CREATE TABLE IF NOT EXISTS rh.employee_versions (
    import_id TEXT NOT NULL REFERENCES rh.import_batches(import_id) ON DELETE RESTRICT,
    employee_id TEXT NOT NULL,
    department TEXT NOT NULL,
    annual_gross_salary NUMERIC(12,2) NOT NULL CHECK(annual_gross_salary > 0),
    commute_mode TEXT NOT NULL,
    commute_class TEXT NOT NULL CHECK(commute_class IN ('sport','non_sport','review')),
    declared_sport TEXT,
    sporting_commute_candidate BOOLEAN NOT NULL,
    PRIMARY KEY(import_id,employee_id)
);
CREATE INDEX IF NOT EXISTS rh_employee_versions_employee ON rh.employee_versions(employee_id);

-- Aucune donnée sensible publiée par défaut aux rôles SQL publics.
REVOKE ALL ON ALL TABLES IN SCHEMA rh FROM PUBLIC;
COMMIT;
