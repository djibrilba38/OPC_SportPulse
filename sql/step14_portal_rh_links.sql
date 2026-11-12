-- SportPulse AI / Étape 14 — liens de comptes portail au référentiel RH versionné.
-- À appliquer après step11_kestra_finance.sql. Aucune suppression de données.
BEGIN;
CREATE TABLE IF NOT EXISTS rh.portal_account_links (
    import_id TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    portal_account_id BIGINT NOT NULL CHECK (portal_account_id > 0),
    linked_by_admin_id BIGINT NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (import_id, employee_id),
    UNIQUE (import_id, portal_account_id),
    CONSTRAINT fk_portal_link_employee_version
      FOREIGN KEY (import_id, employee_id)
      REFERENCES rh.employee_versions(import_id, employee_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS ix_portal_links_employee ON rh.portal_account_links(employee_id);
REVOKE ALL ON rh.portal_account_links FROM PUBLIC;
COMMIT;
-- NB: SQLite conserve les comptes de connexion; PostgreSQL impose ici une FK vers la
-- version RH. Impossible d'imposer une FK inter-moteurs SQLite -> PostgreSQL.
