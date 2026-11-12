-- Étape 9 : vues AGRÉGÉES calendrier pour Metabase.
-- Prérequiert les migrations SQL des étapes 6 et 7.
-- Ces vues exposent les déclarations RH PostgreSQL uniquement (jamais SQLite démo).
-- IMPORTANT : elles sont basées sur l'année CALENDAIRE ; pour une année sociale
-- commençant un autre mois que janvier, utiliser l'API /admin/reporting.
-- Restreindre les droits Metabase aux responsables autorisés ; ne pas publier ces vues.
BEGIN;
CREATE OR REPLACE VIEW benefits.v_sportpulse_monthly_reporting AS
SELECT DATE_TRUNC('month', a.activity_date::timestamp)::date AS month_start,
       a.activity_type,
       a.validation_status,
       COALESCE(n.status, 'not_shared') AS slack_status,
       COUNT(*)::integer AS activity_count,
       COALESCE(SUM(a.duration_min),0)::bigint AS duration_min_total,
       ROUND(COALESCE(SUM(a.distance_km),0)::numeric, 2) AS distance_km_total
FROM benefits.wellbeing_activity_declarations a
LEFT JOIN benefits.sportpulse_notifications_outbox n ON n.activity_id=a.activity_id
WHERE a.deleted_at IS NULL
GROUP BY DATE_TRUNC('month',a.activity_date::timestamp)::date,
         a.activity_type,a.validation_status,COALESCE(n.status,'not_shared');

CREATE OR REPLACE VIEW benefits.v_sportpulse_wellbeing_potential_by_year AS
WITH per_employee AS (
    SELECT EXTRACT(YEAR FROM a.activity_date)::integer AS reference_year,
           a.employee_id,
           COUNT(*) FILTER (WHERE a.validation_status='validated' AND a.outside_work)::integer AS valid_count
    FROM benefits.wellbeing_activity_declarations a
    WHERE a.deleted_at IS NULL
    GROUP BY EXTRACT(YEAR FROM a.activity_date)::integer,a.employee_id
)
SELECT reference_year,
       COUNT(*)::integer AS employees_with_declarations,
       COUNT(*) FILTER (WHERE valid_count>=15)::integer AS employees_potentially_eligible,
       5 * COUNT(*) FILTER (WHERE valid_count>=15)::integer AS wellbeing_days_potential
FROM per_employee
GROUP BY reference_year;
COMMIT;
