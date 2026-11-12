-- À exécuter une fois avant d'ingérer les salariés RH réels.
-- 'Other' = sport RH absent / non modélisé ; aucune pratique n'est inventée.
BEGIN;
DO $$
DECLARE constraint_name TEXT;
BEGIN
  IF to_regclass('staging.silver_employees') IS NOT NULL THEN
    FOR constraint_name IN
      SELECT conname FROM pg_constraint
      WHERE conrelid='staging.silver_employees'::regclass AND contype='c'
        AND pg_get_constraintdef(oid) LIKE '%declared_activity%'
    LOOP
      EXECUTE format('ALTER TABLE staging.silver_employees DROP CONSTRAINT %I',constraint_name);
    END LOOP;
    ALTER TABLE staging.silver_employees
      ADD CONSTRAINT silver_employees_declared_activity_valid
      CHECK (declared_activity IN ('Running','Walking','Cycling','Other'));
  END IF;
END $$;
COMMIT;
