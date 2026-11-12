
{% macro drop_star_foreign_keys() %}
DO $$
DECLARE
    constraint_record RECORD;
BEGIN
    IF to_regclass('dbt_marts.fact_activity') IS NOT NULL THEN
        FOR constraint_record IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = to_regclass('dbt_marts.fact_activity')
              AND contype = 'f'
        LOOP
            EXECUTE format(
                'ALTER TABLE dbt_marts.fact_activity DROP CONSTRAINT %I',
                constraint_record.conname
            );
        END LOOP;
    END IF;
END $$;
{% endmacro %}


{% macro create_star_constraints() %}
DO $$
BEGIN
    -- Verifier que les quatre modèles existent.
    IF to_regclass('dbt_marts.dim_employee') IS NULL
       OR to_regclass('dbt_marts.dim_activity_type') IS NULL
       OR to_regclass('dbt_marts.dim_date') IS NULL
       OR to_regclass('dbt_marts.fact_activity') IS NULL THEN
        RAISE EXCEPTION
            'Un ou plusieurs modèles du schéma en étoile sont absents.';
    END IF;

    -- Clé primaire de la dimension employé.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass('dbt_marts.dim_employee')
          AND conname = 'pk_dim_employee'
    ) THEN
        ALTER TABLE dbt_marts.dim_employee
            ADD CONSTRAINT pk_dim_employee
            PRIMARY KEY (employee_id);
    END IF;

    -- Clé primaire de la dimension type d'activité.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass('dbt_marts.dim_activity_type')
          AND conname = 'pk_dim_activity_type'
    ) THEN
        ALTER TABLE dbt_marts.dim_activity_type
            ADD CONSTRAINT pk_dim_activity_type
            PRIMARY KEY (activity_type);
    END IF;

    -- Clé primaire de la dimension date.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass('dbt_marts.dim_date')
          AND conname = 'pk_dim_date'
    ) THEN
        ALTER TABLE dbt_marts.dim_date
            ADD CONSTRAINT pk_dim_date
            PRIMARY KEY (date_key);
    END IF;

    -- La date complète doit être unique.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass('dbt_marts.dim_date')
          AND conname = 'uq_dim_date_full_date'
    ) THEN
        ALTER TABLE dbt_marts.dim_date
            ADD CONSTRAINT uq_dim_date_full_date
            UNIQUE (full_date);
    END IF;

    -- Clé primaire de la table de faits.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass('dbt_marts.fact_activity')
          AND conname = 'pk_fact_activity'
    ) THEN
        ALTER TABLE dbt_marts.fact_activity
            ADD CONSTRAINT pk_fact_activity
            PRIMARY KEY (activity_id);
    END IF;

    -- Relation fait -> dimension employé.
    ALTER TABLE dbt_marts.fact_activity
        ADD CONSTRAINT fk_fact_activity_employee
        FOREIGN KEY (employee_id)
        REFERENCES dbt_marts.dim_employee (employee_id);

    -- Relation fait -> dimension type d'activité.
    ALTER TABLE dbt_marts.fact_activity
        ADD CONSTRAINT fk_fact_activity_activity_type
        FOREIGN KEY (activity_type)
        REFERENCES dbt_marts.dim_activity_type (activity_type);

    -- Relation fait -> dimension date.
    ALTER TABLE dbt_marts.fact_activity
        ADD CONSTRAINT fk_fact_activity_date
        FOREIGN KEY (date_key)
        REFERENCES dbt_marts.dim_date (date_key);
END $$;
{% endmacro %}
