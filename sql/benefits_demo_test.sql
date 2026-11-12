BEGIN;

-- Données entièrement fictives : transaction de test annulée à la fin.
-- Les règles ci-dessous sont provisoires et ne sont pas des règles RH validées.

UPDATE benefits.benefit_policy
SET
    bonus_rate = 0.05,
    min_validated_commute_days_per_year = 5,
    criteria_status = 'defined'
WHERE benefit_code = 'sport_commute_bonus';

UPDATE benefits.benefit_policy
SET
    days_per_year = 5,
    min_validated_activities_per_year = 12,
    min_validated_activity_duration_min_per_year = 300,
    min_active_months_per_year = 3,
    criteria_status = 'defined'
WHERE benefit_code = 'wellbeing_days';

-- Salaires fictifs pour vérifier uniquement la formule de calcul.
INSERT INTO benefits.employee_compensation (
    employee_id,
    reference_year,
    annual_gross_salary,
    currency,
    salary_validated
)
VALUES
    ('EMP-DEMO-001', 2026, 80000, 'EUR', TRUE),
    ('EMP-DEMO-002', 2026, 60000, 'EUR', TRUE),
    ('EMP-DEMO-003', 2026, 90000, 'EUR', TRUE)
ON CONFLICT (employee_id, reference_year)
DO UPDATE SET
    annual_gross_salary = EXCLUDED.annual_gross_salary,
    currency = EXCLUDED.currency,
    salary_validated = EXCLUDED.salary_validated,
    updated_at = NOW();

-- Nettoyage temporaire des déclarations de démonstration.
DELETE FROM benefits.sport_commute_declarations
WHERE employee_id IN (
    'EMP-DEMO-001', 'EMP-DEMO-002', 'EMP-DEMO-003'
);

DELETE FROM benefits.wellbeing_activity_declarations
WHERE employee_id IN (
    'EMP-DEMO-001', 'EMP-DEMO-002', 'EMP-DEMO-003'
);

-- EMP-DEMO-001 : huit trajets sportifs validés.
INSERT INTO benefits.sport_commute_declarations (
    employee_id,
    commute_date,
    transport_mode,
    office_arrival_confirmed,
    validation_status,
    reviewed_by,
    reviewed_at
)
SELECT
    'EMP-DEMO-001',
    d,
    'bicycle',
    TRUE,
    'validated',
    'hr-demo',
    NOW()
FROM (
    VALUES
        (DATE '2026-01-05'),
        (DATE '2026-01-12'),
        (DATE '2026-02-02'),
        (DATE '2026-02-09'),
        (DATE '2026-03-02'),
        (DATE '2026-03-09'),
        (DATE '2026-04-06'),
        (DATE '2026-04-13')
) AS dates(d);

-- EMP-DEMO-002 : deux trajets sportifs validés.
INSERT INTO benefits.sport_commute_declarations (
    employee_id,
    commute_date,
    transport_mode,
    office_arrival_confirmed,
    validation_status,
    reviewed_by,
    reviewed_at
)
VALUES
    (
        'EMP-DEMO-002', DATE '2026-01-06', 'walking',
        TRUE, 'validated', 'hr-demo', NOW()
    ),
    (
        'EMP-DEMO-002', DATE '2026-01-13', 'walking',
        TRUE, 'validated', 'hr-demo', NOW()
    );

-- EMP-DEMO-001 : douze activités hors travail,
-- réparties sur trois mois, trente minutes chacune.
INSERT INTO benefits.wellbeing_activity_declarations (
    employee_id,
    activity_date,
    activity_type,
    duration_min,
    outside_work,
    validation_status,
    reviewed_by,
    reviewed_at
)
SELECT
    'EMP-DEMO-001',
    make_date(2026, m, d),
    'running',
    30,
    TRUE,
    'validated',
    'hr-demo',
    NOW()
FROM (VALUES (1), (3), (5)) AS months(m)
CROSS JOIN (VALUES (3), (10), (17), (24)) AS days(d);

-- EMP-DEMO-002 : quatre activités de démonstration.
INSERT INTO benefits.wellbeing_activity_declarations (
    employee_id,
    activity_date,
    activity_type,
    duration_min,
    outside_work,
    validation_status,
    reviewed_by,
    reviewed_at
)
SELECT
    'EMP-DEMO-002',
    make_date(2026, m, d),
    'walking',
    30,
    TRUE,
    'validated',
    'hr-demo',
    NOW()
FROM (VALUES (1), (2)) AS months(m)
CROSS JOIN (VALUES (5), (20)) AS days(d);

-- TEST 1 : résultat du calcul provisoire des primes.
SELECT
    employee_id,
    validated_sport_commutes,
    bonus_status,
    estimated_bonus_gross,
    policy_status
FROM benefits.v_commute_bonus_estimates
ORDER BY employee_id;

-- TEST 2 : synthèse du budget provisoire.
SELECT *
FROM benefits.v_commute_bonus_budget_summary
ORDER BY reference_year;

-- TEST 3 : candidatures aux journées bien-être.
SELECT
    employee_id,
    reference_year,
    validated_activities,
    validated_duration_min,
    active_months,
    candidate_status
FROM benefits.v_wellbeing_eligibility_review
WHERE reference_year = 2026
ORDER BY employee_id;

-- TEST 4 : indicateurs agrégés par département.
SELECT *
FROM benefits.v_wellbeing_eligibility_department_summary
WHERE reference_year = 2026
ORDER BY department;

-- Annuler toutes les données et les règles temporaires.
ROLLBACK;