-- Comparaison de populations : les séries ne sont pas un entonnoir cumulatif.
-- Seuil des 15 activités : POTENTIEL ; seuls les droits officialisés font foi.
SELECT e.social_year, e.employee_count AS salaries_rh,
       e.sporting_commute_candidates AS candidats_prime_trajets,
       e.latest_validated_candidate_count AS primes_validees,
       e.latest_synthetic_wellbeing_eligible AS eligibles_bien_etre_synthetiques,
       w.portal_participating_employees AS salaries_portail_actifs,
       w.portal_activities_outside_work AS activites_portail_hors_travail,
       w.portal_employees_15plus AS salaries_portail_15plus,
       w.portal_potential_wellbeing_days AS jours_potentiels_portail,
       w.official_eligible_employees AS salaries_eligibles_officiellement,
       w.officially_entitled_days AS jours_officiellement_attribues,
       w.used_days_recorded AS jours_utilises
FROM powerbi.v_executive_year e
JOIN powerbi.v_wellbeing_year w USING(social_year)
WHERE e.social_year=2026;
 