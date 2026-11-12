#!/usr/bin/env bash
set -euo pipefail
# Only executed by the official PostgreSQL entrypoint on first volume initialization.
if ! psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tAc     "SELECT 1 FROM pg_database WHERE datname='metabase_app'" | grep -q '^1$'; then
  psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1     -c 'CREATE DATABASE metabase_app'
fi
