-- Évolution des budgets calculés : points historiques réels, non simulés.
SELECT calculated_at::date AS jour_calcul,
       scenario_id,source_hash_prefix,rules_version,
       bonus_rate * 100 AS taux_pct,
       candidate_bonus_budget_eur AS budget_prime_eur,
       combined_illustrative_cost_eur AS budget_total_eur,
       is_active_import,is_latest
FROM powerbi.v_scenario_history
WHERE social_year=2026
ORDER BY calculated_at,scenario_id;
