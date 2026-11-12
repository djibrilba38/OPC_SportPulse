-- Lecture seule : sécurité, effectifs et séparation des sources.
SELECT 'rh.active_employees' AS metric, COUNT(*) AS n
FROM rh.employee_versions e JOIN rh.import_batches b USING(import_id)
WHERE b.active=TRUE AND b.reference_year=2026
UNION ALL
SELECT 'rh.linked_portal_accounts', COUNT(*)
FROM rh.portal_account_links l JOIN rh.import_batches b USING(import_id)
WHERE b.active=TRUE AND b.reference_year=2026
UNION ALL
SELECT 'benefits.portal_active_declarations', COUNT(*)
FROM benefits.wellbeing_activity_declarations
WHERE deleted_at IS NULL AND outside_work=TRUE AND validation_status <> 'rejected'
UNION ALL
SELECT 'dbt.synthetic_gold_activities', COUNT(*)
FROM dbt_marts.fact_activity;
