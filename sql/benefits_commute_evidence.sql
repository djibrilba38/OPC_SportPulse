
BEGIN;

CREATE TABLE IF NOT EXISTS benefits.commute_evidence (
    commute_id BIGINT PRIMARY KEY
        REFERENCES benefits.sport_commute_declarations(commute_id)
        ON DELETE CASCADE,

    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,

    gps_samples_count INTEGER NOT NULL
        CHECK (gps_samples_count >= 2),

    gps_distance_m NUMERIC(10, 2) NOT NULL
        CHECK (gps_distance_m > 0),

    gps_accuracy_p95_m NUMERIC(8, 2),

    office_geofence_verified BOOLEAN NOT NULL DEFAULT FALSE,
    arrival_qr_verified BOOLEAN NOT NULL DEFAULT FALSE,
    plausibility_passed BOOLEAN NOT NULL DEFAULT FALSE,

    verification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (verification_status IN (
            'pending',
            'requires_review',
            'validated',
            'rejected'
        )),

    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    reviewer_note TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (ended_at > started_at),

    CHECK (
        verification_status <> 'validated'
        OR (
            office_geofence_verified
            AND arrival_qr_verified
            AND plausibility_passed
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_commute_evidence_status
    ON benefits.commute_evidence (
        verification_status,
        ended_at
    );

-- Pas d'accès général aux preuves de trajet.
REVOKE ALL ON benefits.commute_evidence FROM PUBLIC;

-- Le compte BI ne doit pas lire les preuves individuelles.
REVOKE ALL ON benefits.commute_evidence FROM sportpulse_bi;

COMMIT;
