#!/usr/bin/env bash
# Export sans installation de psql côté Mac, via le conteneur postgres Docker.
# Les résultats agrégés peuvent rester sensibles (budget RH global).
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/powerbi/exports}"
cd "$ROOT"
mkdir -p "$DEST"
chmod 700 "$DEST"

export_view() {
  local view="$1" filename="$2" tmp
  tmp="$(mktemp "$DEST/.${filename}.XXXXXXXX")"
  if docker compose exec -T postgres sh -c \
    'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\copy (SELECT * FROM powerbi.'"$view"') TO STDOUT WITH (FORMAT csv, HEADER true)"' \
      > "$tmp"; then
    mv -f "$tmp" "$DEST/$filename"
    printf 'OK %s (%s lignes hors en-tête)\n' "$filename" "$(($(wc -l < "$DEST/$filename") - 1))"
  else
    rm -f "$tmp"
    printf 'ECHEC export de powerbi.%s\n' "$view" >&2
    return 1
  fi
}

export_view v_executive_year resume_annuel.csv
export_view v_activity_monthly activites_mensuelles.csv
export_view v_employee_department effectifs_departements.csv
export_view v_scenario_history scenarios_budget.csv
export_view v_wellbeing_year bienetre_annuel.csv
export_view v_pipeline_status executions_pipeline.csv
printf '\nExports BI privés : %s\n' "$DEST"
