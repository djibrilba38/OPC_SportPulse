-- Étape 12 — Source analytique Metabase : UNIQUEMENT les activités RH validées par code.
-- Les 1 803 activités Silver/Gold restent dans l'historique synthétique, non admissibles.
-- Aucune donnée salariale personnelle n'est publiée dans ces deux vues.
BEGIN;
CREATE OR REPLACE VIEW benefits.v_sp12_qualifying_activities AS
SELECT activity_id, employee_id, activity_date, activity_type, duration_min,
       outside_work, reviewed_at, validation_status
FROM benefits.wellbeing_activity_declarations
WHERE validation_status='validated'
  AND outside_work=TRUE
  AND reviewed_by LIKE 'admin-code:%'
  AND deleted_at IS NULL;

CREATE OR REPLACE VIEW benefits.v_sp12_wellbeing_by_year AS
SELECT employee_id,
       EXTRACT(YEAR FROM activity_date)::integer AS reference_year,
       COUNT(*)::integer AS validated_activities,
       SUM(duration_min)::integer AS validated_minutes,
       CASE WHEN COUNT(*) >= 15 THEN 5 ELSE 0 END AS days_potential
FROM benefits.v_sp12_qualifying_activities
GROUP BY employee_id,EXTRACT(YEAR FROM activity_date)::integer;
COMMIT;
-- Limite : agrégation par année civile. Ajuster une vue paramétrée pour une année sociale décalée.
