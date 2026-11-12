
BEGIN;

-- =========================================================
-- 1. Critères configurables, en attente de validation RH
-- =========================================================

ALTER TABLE benefits.benefit_policy
    ADD COLUMN IF NOT EXISTS min_validated_commute_days_per_year SMALLINT,
    ADD COLUMN IF NOT EXISTS min_validated_activities_per_year SMALLINT,
    ADD COLUMN IF NOT EXISTS min_validated_activity_duration_min_per_year INTEGER,
    ADD COLUMN IF NOT EXISTS min_active_months_per_year SMALLINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'benefit_policy_commute_days_check'
          AND conrelid = 'benefits.benefit_policy'::regclass
    ) THEN
        ALTER TABLE benefits.benefit_policy
        ADD CONSTRAINT benefit_policy_commute_days_check
        CHECK (
            min_validated_commute_days_per_year IS NULL
            OR min_validated_commute_days_per_year BETWEEN 1 AND 366
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'benefit_policy_activity_count_check'
          AND conrelid = 'benefits.benefit_policy'::regclass
    ) THEN
        ALTER TABLE benefits.benefit_policy
        ADD CONSTRAINT benefit_policy_activity_count_check
        CHECK (
            min_validated_activities_per_year IS NULL
            OR min_validated_activities_per_year BETWEEN 1 AND 10000
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'benefit_policy_activity_duration_check'
          AND conrelid = 'benefits.benefit_policy'::regclass
    ) THEN
        ALTER TABLE benefits.benefit_policy
        ADD CONSTRAINT benefit_policy_activity_duration_check
        CHECK (
            min_validated_activity_duration_min_per_year IS NULL
            OR min_validated_activity_duration_min_per_year > 0
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'benefit_policy_active_months_check'
          AND conrelid = 'benefits.benefit_policy'::regclass
    ) THEN
        ALTER TABLE benefits.benefit_policy
        ADD CONSTRAINT benefit_policy_active_months_check
        CHECK (
            min_active_months_per_year IS NULL
            OR min_active_months_per_year BETWEEN 1 AND 12
        );
    END IF;
END
$$;


-- =========================================================
-- 2. Calcul de la prime : une journée distincte compte
--    comme un trajet validé.
--    Aucun montant calculé si la règle n'est pas confirmée.
-- =========================================================

CREATE OR REPLACE VIEW benefits.v_commute_bonus_estimates AS
WITH validated_commutes AS (
    SELECT
        employee_id,
        EXTRACT(YEAR FROM commute_date)::SMALLINT AS reference_year,
        COUNT(DISTINCT commute_date)::INTEGER AS validated_sport_commutes
    FROM benefits.sport_commute_declarations
    WHERE validation_status = 'validated'
      AND office_arrival_confirmed = TRUE
    GROUP BY
        employee_id,
        EXTRACT(YEAR FROM commute_date)::SMALLINT
)
SELECT
    s.employee_id,
    s.reference_year,
    s.currency,
    s.salary_validated,
    p.bonus_rate,
    COALESCE(c.validated_sport_commutes, 0) AS validated_sport_commutes,

    CASE
        WHEN NOT s.salary_validated
            THEN 'salary_to_validate'
        WHEN p.criteria_status <> 'defined'
          OR p.min_validated_commute_days_per_year IS NULL
            THEN 'policy_to_confirm'
        WHEN COALESCE(c.validated_sport_commutes, 0)
             < p.min_validated_commute_days_per_year
            THEN 'commute_threshold_not_met'
        ELSE 'eligible_pending_hr_approval'
    END AS bonus_status,

    CASE
        WHEN s.salary_validated
         AND p.criteria_status = 'defined'
         AND p.min_validated_commute_days_per_year IS NOT NULL
         AND p.bonus_rate IS NOT NULL
         AND COALESCE(c.validated_sport_commutes, 0)
             >= p.min_validated_commute_days_per_year
        THEN ROUND(s.annual_gross_salary * p.bonus_rate, 2)
        ELSE NULL::NUMERIC
    END AS estimated_bonus_gross,

    p.criteria_status AS policy_status

FROM benefits.employee_compensation AS s
JOIN benefits.benefit_policy AS p
    ON p.benefit_code = 'sport_commute_bonus'
LEFT JOIN validated_commutes AS c
    ON c.employee_id = s.employee_id
   AND c.reference_year = s.reference_year;


-- =========================================================
-- 3. Évaluation candidate des journées bien-être
--    Les critères doivent être définis avant le calcul.
--    Une candidature n'est pas une validation RH.
-- =========================================================

CREATE OR REPLACE VIEW benefits.v_wellbeing_eligibility_review AS
WITH employee_years AS (
    SELECT
        employee_id,
        EXTRACT(YEAR FROM activity_date)::SMALLINT AS reference_year
    FROM benefits.wellbeing_activity_declarations

    UNION

    SELECT employee_id, reference_year
    FROM benefits.wellbeing_day_entitlements
),
activity_stats AS (
    SELECT
        employee_id,
        EXTRACT(YEAR FROM activity_date)::SMALLINT AS reference_year,

        COUNT(*) FILTER (
            WHERE validation_status = 'validated'
              AND outside_work = TRUE
        )::INTEGER AS validated_activities,

        COALESCE(
            SUM(duration_min) FILTER (
                WHERE validation_status = 'validated'
                  AND outside_work = TRUE
            ),
            0
        )::INTEGER AS validated_duration_min,

        COUNT(DISTINCT DATE_TRUNC('month', activity_date)) FILTER (
            WHERE validation_status = 'validated'
              AND outside_work = TRUE
        )::INTEGER AS active_months

    FROM benefits.wellbeing_activity_declarations
    GROUP BY
        employee_id,
        EXTRACT(YEAR FROM activity_date)::SMALLINT
)
SELECT
    ey.employee_id,
    ey.reference_year,
    COALESCE(d.department, 'Non renseigné') AS department,

    COALESCE(a.validated_activities, 0) AS validated_activities,
    COALESCE(a.validated_duration_min, 0) AS validated_duration_min,
    COALESCE(a.active_months, 0) AS active_months,

    p.min_validated_activities_per_year,
    p.min_validated_activity_duration_min_per_year,
    p.min_active_months_per_year,
    p.criteria_status AS policy_status,

    CASE
        WHEN p.criteria_status <> 'defined'
          OR p.min_validated_activities_per_year IS NULL
          OR p.min_validated_activity_duration_min_per_year IS NULL
          OR p.min_active_months_per_year IS NULL
            THEN 'policy_to_confirm'

        WHEN COALESCE(a.validated_activities, 0)
                 >= p.min_validated_activities_per_year
         AND COALESCE(a.validated_duration_min, 0)
                 >= p.min_validated_activity_duration_min_per_year
         AND COALESCE(a.active_months, 0)
                 >= p.min_active_months_per_year
            THEN 'eligible_candidate_pending_hr_approval'

        ELSE 'below_threshold'
    END AS candidate_status

FROM employee_years AS ey

LEFT JOIN activity_stats AS a
    ON a.employee_id = ey.employee_id
   AND a.reference_year = ey.reference_year

LEFT JOIN dbt_marts.dim_employee AS d
    ON d.employee_id = ey.employee_id

JOIN benefits.benefit_policy AS p
    ON p.benefit_code = 'wellbeing_days';


-- =========================================================
-- 4. Synthèse des candidatures par département
--    Sans salaires ni données individuelles détaillées.
-- =========================================================

CREATE OR REPLACE VIEW benefits.v_wellbeing_eligibility_department_summary AS
SELECT
    reference_year,
    department,
    COUNT(*) AS employees_reviewed,

    COUNT(*) FILTER (
        WHERE candidate_status = 'eligible_candidate_pending_hr_approval'
    ) AS candidates_pending_hr_review,

    COUNT(*) FILTER (
        WHERE candidate_status = 'below_threshold'
    ) AS employees_below_threshold,

    COUNT(*) FILTER (
        WHERE candidate_status = 'policy_to_confirm'
    ) AS employees_awaiting_policy

FROM benefits.v_wellbeing_eligibility_review
GROUP BY reference_year, department;


-- =========================================================
-- 5. Contrôle des accès Metabase
-- =========================================================

REVOKE ALL ON benefits.v_wellbeing_eligibility_review
    FROM PUBLIC, sportpulse_bi;

GRANT USAGE ON SCHEMA benefits TO sportpulse_bi;

GRANT SELECT ON
    benefits.v_commute_bonus_budget_summary,
    benefits.v_commute_bonus_department_summary,
    benefits.v_wellbeing_company_summary,
    benefits.v_wellbeing_department_summary,
    benefits.v_wellbeing_eligibility_department_summary
TO sportpulse_bi;

REVOKE ALL ON benefits.employee_compensation
    FROM PUBLIC, sportpulse_bi;

REVOKE ALL ON benefits.v_commute_bonus_estimates
    FROM PUBLIC, sportpulse_bi;

COMMIT;
