-- Simulation WHAT-IF : n'insère AUCUN nouveau scénario historique.
-- Hypothèses fixes : même référentiel RH, mêmes candidats, même coût bien-être.
WITH base AS (
  SELECT social_year, latest_bonus_rate AS taux_actuel,
         latest_candidate_bonus_budget_eur AS budget_actuel,
         latest_capacity_cost_eur AS cout_bien_etre
  FROM powerbi.v_executive_year WHERE social_year = 2026
), series AS (
  SELECT generate_series(3,8)::numeric AS taux_pct
)
SELECT b.social_year, s.taux_pct AS taux_prime_pct,
       ROUND((b.budget_actuel / NULLIF(b.taux_actuel,0)) * s.taux_pct / 100,2)
           AS budget_prime_simule_eur,
       b.cout_bien_etre AS cout_bien_etre_constant_eur,
       ROUND((b.budget_actuel / NULLIF(b.taux_actuel,0)) * s.taux_pct / 100
             + b.cout_bien_etre,2) AS cout_total_simule_eur,
       ROUND((b.budget_actuel / NULLIF(b.taux_actuel,0)) * s.taux_pct / 100
             - b.budget_actuel,2) AS variation_prime_vs_actuel_eur,
       (s.taux_pct = b.taux_actuel * 100) AS scenario_reference
FROM base b CROSS JOIN series s
ORDER BY s.taux_pct;
