-- SportPulse AI Step
-- Schéma analytique PostgreSQL pour les agrégats Gold.

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.pipeline_runs (
    run_id TEXT PRIMARY KEY,
    pipeline TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('RUNNING', 'SUCCESS', 'SUCCESS_WITH_REJECTS', 'FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    source_manifest_uri TEXT,
    output_manifest_uri TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS analytics.by_employee (
    employee_id TEXT PRIMARY KEY,
    department TEXT NOT NULL,
    activity_count INTEGER NOT NULL CHECK (activity_count >= 0),
    total_duration_min NUMERIC(14, 2) NOT NULL CHECK (total_duration_min >= 0),
    total_distance_m NUMERIC(16, 2) NOT NULL CHECK (total_distance_m >= 0),
    average_duration_min NUMERIC(14, 2) NOT NULL CHECK (average_duration_min >= 0),
    average_distance_m NUMERIC(16, 2) NOT NULL CHECK (average_distance_m >= 0),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_by_employee_department
    ON analytics.by_employee (department);

CREATE TABLE IF NOT EXISTS analytics.by_department (
    department TEXT PRIMARY KEY,
    employee_count INTEGER NOT NULL CHECK (employee_count >= 0),
    active_employee_count INTEGER NOT NULL CHECK (active_employee_count >= 0),
    activity_count INTEGER NOT NULL CHECK (activity_count >= 0),
    total_duration_min NUMERIC(14, 2) NOT NULL CHECK (total_duration_min >= 0),
    total_distance_m NUMERIC(16, 2) NOT NULL CHECK (total_distance_m >= 0),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (active_employee_count <= employee_count)
);

CREATE TABLE IF NOT EXISTS analytics.by_activity_type (
    activity_type TEXT PRIMARY KEY
        CHECK (activity_type IN ('Running', 'Walking', 'Cycling')),
    active_employee_count INTEGER NOT NULL CHECK (active_employee_count >= 0),
    activity_count INTEGER NOT NULL CHECK (activity_count >= 0),
    total_duration_min NUMERIC(14, 2) NOT NULL CHECK (total_duration_min >= 0),
    total_distance_m NUMERIC(16, 2) NOT NULL CHECK (total_distance_m >= 0),
    average_duration_min NUMERIC(14, 2) NOT NULL CHECK (average_duration_min >= 0),
    average_distance_m NUMERIC(16, 2) NOT NULL CHECK (average_distance_m >= 0),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS analytics.by_day (
    activity_date DATE PRIMARY KEY,
    active_employee_count INTEGER NOT NULL CHECK (active_employee_count >= 0),
    activity_count INTEGER NOT NULL CHECK (activity_count >= 0),
    total_duration_min NUMERIC(14, 2) NOT NULL CHECK (total_duration_min >= 0),
    total_distance_m NUMERIC(16, 2) NOT NULL CHECK (total_distance_m >= 0),
    average_duration_min NUMERIC(14, 2) NOT NULL CHECK (average_duration_min >= 0),
    average_distance_m NUMERIC(16, 2) NOT NULL CHECK (average_distance_m >= 0),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_by_day_activity_date
    ON analytics.by_day (activity_date);
