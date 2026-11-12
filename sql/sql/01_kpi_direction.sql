-- Dashboard "Pilotage RH et Financier" / 2026.
-- Distingue candidats potentiels, primes réellement validées et activité déclarée.
SELECT social_year, employee_count AS salaries,
       sporting_commute_candidates AS candidats_prime,
       ROUND(100.0 * sporting_commute_candidates / NULLIF(employee_count, 0), 1) AS taux_candidats_pct,
       commute_to_review_count AS trajets_a_revoir,
       gold_synthetic_activities AS activites_synthetiques,
       portal_declared_activities AS activites_portail,
       latest_candidate_bonus_budget_eur AS primes_potentielles_eur,
       latest_validated_candidate_count AS beneficiaires_prime_valides,
       latest_validated_bonus_budget_eur AS primes_validees_eur,
       latest_capacity_cost_eur AS cout_bien_etre_simule_eur,
       latest_total_illustrative_eur AS cout_total_illustratif_eur,
       latest_bonus_rate * 100 AS taux_prime_pct
FROM powerbi.v_executive_year
WHERE social_year = 2026;
