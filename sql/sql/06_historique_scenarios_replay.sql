-- Auditabilité : conserve les anciens imports RH et versions de règles.
-- Ne jamais sommer les scénarios : ce sont des alternatives de calcul.
WITH h AS (
  SELECT social_year,calculated_at,scenario_id,import_id,
         source_hash_prefix,rules_version,bonus_rate*100 AS taux_pct,
         candidate_bonus_budget_eur,combined_illustrative_cost_eur,
         is_active_import,is_latest,status,
         LAG(candidate_bonus_budget_eur) OVER (
           PARTITION BY social_year ORDER BY calculated_at,scenario_id
         ) AS prime_precedente_eur
  FROM powerbi.v_scenario_history
  WHERE social_year=2026
)
SELECT *,
       candidate_bonus_budget_eur - prime_precedente_eur AS ecart_vs_scenario_precedent_eur
FROM h
ORDER BY calculated_at DESC,scenario_id DESC;
