-- Exécuter UNE FOIS sur PostgreSQL (idempotent); vérifier la sauvegarde avant application.
-- Les événements sont créés dans la même transaction que la déclaration RH.
CREATE SCHEMA IF NOT EXISTS benefits;
CREATE TABLE IF NOT EXISTS benefits.sportpulse_notifications_outbox (
    event_key TEXT PRIMARY KEY,
    activity_id BIGINT NOT NULL UNIQUE REFERENCES benefits.wellbeing_activity_declarations(activity_id),
    employee_id TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','retry','sent','failed','dry_run')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    slack_channel TEXT,
    slack_ts TEXT,
    message_text TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sp_notifications_status
    ON benefits.sportpulse_notifications_outbox(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_sp_notifications_employee
    ON benefits.sportpulse_notifications_outbox(employee_id, created_at DESC);

-- Source PostgreSQL pour Metabase : affichage immédiat des nouvelles déclarations.
-- Ne compte PAS comme 15 activités validées avant décision RH.
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
FROM benefits.wellbeing_activity_declarations AS a
LEFT JOIN benefits.sportpulse_notifications_outbox AS n ON n.activity_id = a.activity_id;
