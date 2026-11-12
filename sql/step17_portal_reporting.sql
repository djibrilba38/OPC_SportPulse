-- Étape 17 — vue agrégée de déclarations sportives POC, non paiements.
-- Appliquer APRÈS step7_activity_management.sql et step14_portal_rh_links.sql.
-- Ne modifie pas les 1 803 activités Gold historiques.
BEGIN;
CREATE INDEX IF NOT EXISTS ix_sp17_activity_active
  ON benefits.wellbeing_activity_declarations (employee_id, activity_date DESC)
  WHERE deleted_at IS NULL AND outside_work = TRUE;

CREATE OR REPLACE VIEW benefits.v_sportpulse_portal_poc AS
SELECT a.activity_id, a.employee_id, a.activity_date,
       a.activity_type, a.duration_min, a.distance_km, a.outside_work,
       a.validation_status,
       'portal_declared'::text AS source_kind
FROM benefits.wellbeing_activity_declarations a
WHERE a.deleted_at IS NULL AND a.outside_work IS TRUE
  AND a.validation_status <> 'rejected'
  AND EXISTS (
    SELECT 1 FROM rh.portal_account_links l
    JOIN rh.import_batches b ON b.import_id=l.import_id
    WHERE l.employee_id=a.employee_id AND b.active=TRUE
  );

COMMENT ON VIEW benefits.v_sportpulse_portal_poc IS
'Activités déclarées par comptes rattachés RH, utilisées pour KPI POC uniquement ; ne justifie ni prime versée ni droits RH accordés.';
COMMIT;
