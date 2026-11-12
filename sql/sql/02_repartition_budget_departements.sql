-- Indicateurs normés par département ; agrégats financiers supprimés si effectif < 5.
SELECT d.social_year, d.department, d.employee_count AS effectif,
       d.sporting_commute_candidates AS candidats_prime,
       ROUND(100.0 * d.sporting_commute_candidates / NULLIF(d.employee_count, 0), 1)
           AS part_candidats_pct,
       d.commute_to_review_count AS trajets_a_verifier,
       CASE WHEN d.finance_suppressed_small_group THEN NULL
            ELSE ROUND(COALESCE(d.candidate_salary_mass_eur_aggregated,0)
                        * e.latest_bonus_rate, 2) END AS budget_prime_potentiel_eur,
       d.finance_suppressed_small_group AS financier_masque
FROM powerbi.v_employee_department d
JOIN powerbi.v_executive_year e USING(social_year)
WHERE d.social_year = 2026
ORDER BY budget_prime_potentiel_eur DESC NULLS LAST, d.department;
