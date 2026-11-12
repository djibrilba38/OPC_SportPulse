-- Étape 7 : modification / suppression logique des déclarations d'activités.
-- À exécuter après sql/step6_activity_notifications.sql ; idempotent.
-- Sauvegarder PostgreSQL avant tout changement de schéma.
BEGIN;
ALTER TABLE benefits.wellbeing_activity_declarations
  ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS distance_km NUMERIC(8,3),
  ADD COLUMN IF NOT EXISTS comment TEXT,
  ADD COLUMN IF NOT EXISTS portal_revision INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS benefits.sportpulse_activity_audit (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  activity_id BIGINT NOT NULL REFERENCES benefits.wellbeing_activity_declarations(activity_id),
  employee_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('update','delete')),
  revision INTEGER NOT NULL,
  before_json JSONB NOT NULL,
  after_json JSONB,
  performed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_sp_activity_audit
  ON benefits.sportpulse_activity_audit(employee_id,activity_id,performed_at DESC);

CREATE TABLE IF NOT EXISTS benefits.sportpulse_activity_slack_actions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  activity_id BIGINT NOT NULL REFERENCES benefits.wellbeing_activity_declarations(activity_id),
  revision INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('update','delete')),
  channel TEXT NOT NULL,
  slack_ts TEXT NOT NULL,
  payload_json JSONB,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  claimed_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(activity_id,revision)
);
CREATE INDEX IF NOT EXISTS ix_sp_activity_slack_actions
  ON benefits.sportpulse_activity_slack_actions(status,next_attempt_at);

-- Ne pas afficher une activité supprimée dans la vue live Metabase.
-- Note : une activité initialement pending et supprimée passe en rejected,
-- elle ne doit pas entrer dans les comptes d'activités validées.
CREATE OR REPLACE VIEW benefits.v_sportpulse_activity_live_report AS
SELECT a.activity_id,
       a.employee_id,
       a.activity_date,
       a.activity_type,
       a.duration_min,
       a.validation_status,
       a.outside_work,
       n.event_key,
       COALESCE(n.status, 'not_shared') AS slack_status,
       n.created_at AS slack_enqueued_at,
       n.published_at AS slack_published_at
FROM benefits.wellbeing_activity_declarations a
LEFT JOIN benefits.sportpulse_notifications_outbox n ON n.activity_id=a.activity_id
WHERE a.deleted_at IS NULL;
COMMIT;
