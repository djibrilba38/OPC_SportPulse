
BEGIN;

-- ==========================================================
-- 1. Estimation individuelle privée de la prime
--    Cette vue ne constitue pas une validation de paiement.
-- ==========================================================

CREATE OR REPLACE VIEW benefits.v_commute_bonus_estimates AS
WITH validated_commutes AS (
    SELECT
        employee_id,
        EXTRACT(YEAR FROM commute_date)::SMALLINT AS reference_year,
        COUNT(*)::INTEGER AS validated_sport_commutes
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
        WHEN COALESCE(c.validated_sport_commutes, 0) = 0
            THEN 'no_validated_sport_commute'
        WHEN p.criteria_status <> 'defined'
            THEN 'policy_to_confirm'
        ELSE 'eligible_pending_hr_approval'
    END AS bonus_status,

    CASE
        WHEN s.salary_validated
         AND COALESCE(c.validated_sport_commutes, 0) > 0
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


-- ==========================================================
-- 2. Vue agrégée du budget potentiel annuel.
--    Aucun salaire individuel n'est exposé.
-- ==========================================================

CREATE OR REPLACE VIEW benefits.v_commute_bonus_budget_summary AS
SELECT
    reference_year,
    COUNT(*) AS employees_with_compensation_record,

    COUNT(*) FILTER (
        WHERE salary_validated
    ) AS validated_salary_count,

    COUNT(*) FILTER (
        WHERE validated_sport_commutes > 0
    ) AS employees_with_validated_sport_commute,

    COUNT(*) FILTER (
        WHERE bonus_status = 'salary_to_validate'
    ) AS pending_salary_validation_count,

    ROUND(
        SUM(COALESCE(estimated_bonus_gross, 0)),
        2
    ) AS estimated_bonus_budget_gross,

    MAX(policy_status) AS policy_status,

    CASE
        WHEN BOOL_OR(policy_status <> 'defined')
            THEN 'estimate_only_policy_to_confirm'
        ELSE 'estimate_only_hr_approval_required'
    END AS budget_status

FROM benefits.v_commute_bonus_estimates
GROUP BY reference_year;


-- ==========================================================
-- 3. Suivi des primes par département.
--    Volumes uniquement, pas de montant par département.
-- ==========================================================

CREATE OR REPLACE VIEW benefits.v_commute_bonus_department_summary AS
SELECT
    b.reference_year,
    COALESCE(d.department, 'Non renseigné') AS department,
    COUNT(*) AS employees_with_compensation_record,

    COUNT(*) FILTER (
        WHERE b.salary_validated
    ) AS validated_salary_count,

    COUNT(*) FILTER (
        WHERE b.validated_sport_commutes > 0
    ) AS employees_with_validated_sport_commute,

    COUNT(*) FILTER (
        WHERE b.bonus_status = 'salary_to_validate'
    ) AS pending_salary_validation_count,

    COUNT(*) FILTER (
        WHERE b.bonus_status = 'no_validated_sport_commute'
    ) AS employees_without_validated_sport_commute,

    MAX(b.policy_status) AS policy_status

FROM benefits.v_commute_bonus_estimates AS b
JOIN dbt_marts.dim_employee AS d
    ON d.employee_id = b.employee_id
GROUP BY
    b.reference_year,
    COALESCE(d.department, 'Non renseigné');


-- ==========================================================
-- 4. Suivi annuel des journées bien-être à l'échelle globale.
-- ==========================================================

CREATE OR REPLACE VIEW benefits.v_wellbeing_company_summary AS
SELECT
    reference_year,
    COUNT(*) AS employees_tracked,

    COUNT(*) FILTER (
        WHERE eligibility_status = 'eligible'
    ) AS eligible_employees,

    SUM(days_entitled) AS days_entitled,
    SUM(days_used) AS days_used,
    SUM(days_remaining) AS days_remaining,

    SUM(validated_outside_work_activities)
        AS validated_outside_work_activities

FROM benefits.v_wellbeing_year_status
GROUP BY reference_year;


-- ==========================================================
-- 5. Suivi des journées bien-être par département.
-- ==========================================================

CREATE OR REPLACE VIEW benefits.v_wellbeing_department_summary AS
SELECT
    w.reference_year,
    COALESCE(d.department, 'Non renseigné') AS department,
    COUNT(*) AS employees_tracked,

    COUNT(*) FILTER (
        WHERE w.eligibility_status = 'eligible'
    ) AS eligible_employees,

    SUM(w.days_entitled) AS days_entitled,
    SUM(w.days_used) AS days_used,
    SUM(w.days_remaining) AS days_remaining,

    SUM(w.validated_outside_work_activities)
        AS validated_outside_work_activities

FROM benefits.v_wellbeing_year_status AS w
JOIN dbt_marts.dim_employee AS d
    ON d.employee_id = w.employee_id
GROUP BY
    w.reference_year,
    COALESCE(d.department, 'Non renseigné');


-- ==========================================================
-- 6. Rôle de lecture dédié à Metabase.
--    Les vues individuelles et les tables salariales restent privées.
-- ==========================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'sportpulse_bi'
    ) THEN
        EXECUTE 'CREATE ROLE sportpulse_bi LOGIN';
    END IF;
END
$$;

ALTER ROLE sportpulse_bi LOGIN;

GRANT CONNECT ON DATABASE sportpulse TO sportpulse_bi;

GRANT USAGE ON SCHEMA dbt_marts TO sportpulse_bi;
GRANT SELECT ON ALL TABLES IN SCHEMA dbt_marts TO sportpulse_bi;

GRANT USAGE ON SCHEMA benefits TO sportpulse_bi;

REVOKE ALL ON ALL TABLES IN SCHEMA benefits FROM sportpulse_bi;
GRANT SELECT ON
    benefits.v_commute_bonus_budget_summary,
    benefits.v_commute_bonus_department_summary,
    benefits.v_wellbeing_company_summary,
    benefits.v_wellbeing_department_summary
TO sportpulse_bi;

-- Conserver les droits de lecture si dbt recrée ses tables.
ALTER DEFAULT PRIVILEGES
    FOR ROLE sportpulse
    IN SCHEMA dbt_marts
    GRANT SELECT ON TABLES TO sportpulse_bi;

COMMIT;
