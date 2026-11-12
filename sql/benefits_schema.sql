
BEGIN;

CREATE SCHEMA IF NOT EXISTS benefits;

REVOKE ALL ON SCHEMA benefits FROM PUBLIC;

-- 1. Paramètres et règles métier des avantages.
CREATE TABLE IF NOT EXISTS benefits.benefit_policy (
    benefit_code TEXT PRIMARY KEY
        CHECK (benefit_code IN (
            'sport_commute_bonus',
            'wellbeing_days'
        )),
    bonus_rate NUMERIC(5, 4),
    days_per_year SMALLINT,
    eligibility_rule TEXT NOT NULL,
    criteria_status TEXT NOT NULL DEFAULT 'to_confirm'
        CHECK (criteria_status IN ('defined', 'to_confirm')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (
        bonus_rate IS NULL
        OR bonus_rate BETWEEN 0 AND 1
    ),
    CHECK (
        days_per_year IS NULL
        OR days_per_year BETWEEN 0 AND 30
    )
);

INSERT INTO benefits.benefit_policy (
    benefit_code,
    bonus_rate,
    days_per_year,
    eligibility_rule,
    criteria_status
)
VALUES
(
    'sport_commute_bonus',
    0.05,
    NULL,
    'Prime de 5 % du salaire annuel brut pour un trajet domicile-bureau sportif validé. La règle de fréquence doit être confirmée par les RH.',
    'to_confirm'
),
(
    'wellbeing_days',
    NULL,
    5,
    'Jusqu’à 5 journées annuelles selon la politique annoncée. Les critères de pratique physique soutenue restent à formaliser.',
    'to_confirm'
)
ON CONFLICT (benefit_code) DO NOTHING;


-- 2. Données salariales : une ligne par salarié et par année.
CREATE TABLE IF NOT EXISTS benefits.employee_compensation (
    employee_id TEXT NOT NULL,
    reference_year SMALLINT NOT NULL
        CHECK (reference_year BETWEEN 2000 AND 2100),
    annual_gross_salary NUMERIC(12, 2) NOT NULL
        CHECK (annual_gross_salary > 0),
    currency CHAR(3) NOT NULL DEFAULT 'EUR',
    salary_validated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (employee_id, reference_year)
);


-- 3. Déclarations des trajets sportifs vers le bureau.
CREATE TABLE IF NOT EXISTS benefits.sport_commute_declarations (
    commute_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id TEXT NOT NULL,
    commute_date DATE NOT NULL,
    transport_mode TEXT NOT NULL
        CHECK (transport_mode IN (
            'bicycle',
            'electric_bicycle',
            'scooter',
            'running',
            'walking',
            'skateboard',
            'other'
        )),
    transport_mode_detail TEXT,
    office_arrival_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    validation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (validation_status IN (
            'pending',
            'validated',
            'rejected'
        )),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (
        validation_status <> 'validated'
        OR (
            office_arrival_confirmed
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_sport_commute_employee_date
    ON benefits.sport_commute_declarations (
        employee_id,
        commute_date
    );


-- 4. Déclarations d'activités physiques hors travail.
CREATE TABLE IF NOT EXISTS benefits.wellbeing_activity_declarations (
    activity_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id TEXT NOT NULL,
    activity_date DATE NOT NULL,
    activity_type TEXT NOT NULL,
    duration_min INTEGER NOT NULL CHECK (duration_min > 0),
    outside_work BOOLEAN NOT NULL DEFAULT TRUE,
    validation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (validation_status IN (
            'pending',
            'validated',
            'rejected'
        )),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (
        validation_status <> 'validated'
        OR (
            outside_work
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_wellbeing_activity_employee_date
    ON benefits.wellbeing_activity_declarations (
        employee_id,
        activity_date
    );


-- 5. Attribution et consommation des journées bien-être.
CREATE TABLE IF NOT EXISTS benefits.wellbeing_day_entitlements (
    employee_id TEXT NOT NULL,
    reference_year SMALLINT NOT NULL
        CHECK (reference_year BETWEEN 2000 AND 2100),
    eligibility_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (eligibility_status IN (
            'pending',
            'eligible',
            'not_eligible'
        )),
    days_entitled SMALLINT NOT NULL DEFAULT 0
        CHECK (days_entitled BETWEEN 0 AND 5),
    days_used SMALLINT NOT NULL DEFAULT 0
        CHECK (days_used >= 0),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    decision_note TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (employee_id, reference_year),

    CHECK (days_used <= days_entitled),

    CHECK (
        (
            eligibility_status = 'eligible'
            AND days_entitled = 5
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
        )
        OR
        (
            eligibility_status IN ('pending', 'not_eligible')
            AND days_entitled = 0
        )
    )
);


-- Vue privée : estimation annuelle des primes.
-- L'hypothèse de calcul est au moins un trajet validé dans l'année.
CREATE OR REPLACE VIEW benefits.v_commute_bonus_estimates AS
SELECT
    s.employee_id,
    s.reference_year,
    s.currency,
    s.salary_validated,
    p.bonus_rate,
    COUNT(c.commute_id)::INTEGER AS validated_sport_commutes,

    CASE
        WHEN NOT s.salary_validated
            THEN 'salary_to_validate'
        WHEN COUNT(c.commute_id) = 0
            THEN 'no_validated_sport_commute'
        ELSE 'eligible'
    END AS bonus_status,

    CASE
        WHEN NOT s.salary_validated
            THEN NULL::NUMERIC
        WHEN COUNT(c.commute_id) = 0
            THEN 0::NUMERIC
        ELSE ROUND(s.annual_gross_salary * p.bonus_rate, 2)
    END AS estimated_bonus_gross

FROM benefits.employee_compensation AS s

JOIN benefits.benefit_policy AS p
    ON p.benefit_code = 'sport_commute_bonus'

LEFT JOIN benefits.sport_commute_declarations AS c
    ON c.employee_id = s.employee_id
    AND EXTRACT(YEAR FROM c.commute_date)::SMALLINT = s.reference_year
    AND c.office_arrival_confirmed = TRUE
    AND c.validation_status = 'validated'

GROUP BY
    s.employee_id,
    s.reference_year,
    s.currency,
    s.salary_validated,
    s.annual_gross_salary,
    p.bonus_rate;


-- Vue de suivi des journées bien-être et des activités déclarées.
CREATE OR REPLACE VIEW benefits.v_wellbeing_year_status AS
SELECT
    e.employee_id,
    e.reference_year,
    e.eligibility_status,
    e.days_entitled,
    e.days_used,
    e.days_entitled - e.days_used AS days_remaining,
    COUNT(a.activity_id) FILTER (
        WHERE a.validation_status = 'validated'
          AND a.outside_work = TRUE
    )::INTEGER AS validated_outside_work_activities

FROM benefits.wellbeing_day_entitlements AS e

LEFT JOIN benefits.wellbeing_activity_declarations AS a
    ON a.employee_id = e.employee_id
    AND EXTRACT(YEAR FROM a.activity_date)::SMALLINT = e.reference_year

GROUP BY
    e.employee_id,
    e.reference_year,
    e.eligibility_status,
    e.days_entitled,
    e.days_used;


-- Pas d'accès automatique au schéma pour le rôle PUBLIC.
REVOKE ALL ON ALL TABLES IN SCHEMA benefits FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA benefits FROM PUBLIC;

COMMIT;
