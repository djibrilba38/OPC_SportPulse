-- SportPulse 15 — Vues POC SANS QR, en lecture depuis les déclarations non supprimées.
-- Les résultats n'accordent aucune prime ou journée bien-être automatiquement.
-- N'écrase pas les tables Bronze/Silver/Gold, l'historique ou les anciennes vues.
BEGIN;
CREATE OR REPLACE VIEW benefits.v_sp15_poc_activities AS
SELECT employee_id, activity_id, activity_date, activity_type, duration_min,
       distance_km, outside_work, validation_status
FROM benefits.wellbeing_activity_declarations
WHERE deleted_at IS NULL;

CREATE OR REPLACE VIEW benefits.v_sp15_poc_wellbeing_year AS
SELECT employee_id,
       EXTRACT(YEAR FROM activity_date)::integer AS reference_year,
       COUNT(*)::integer AS declared_activities,
       SUM(duration_min)::integer AS declared_minutes,
       CASE WHEN COUNT(*) >= 15 THEN 5 ELSE 0 END AS days_potential,
       FALSE AS rights_granted
FROM benefits.v_sp15_poc_activities
WHERE outside_work = TRUE
GROUP BY employee_id, EXTRACT(YEAR FROM activity_date)::integer;
COMMIT;
-- Pour année sociale non calendaire, créer une vue dédiée ou appliquer la période depuis FastAPI.
-- Les 1803 activités synthétiques du pipeline Kestra restent dans dbt_marts.fact_activity,
-- séparées des déclarations de comptes salariés pour éviter les doubles comptages.
