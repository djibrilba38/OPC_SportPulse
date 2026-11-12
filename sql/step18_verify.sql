-- Contrôles lecture seule à exécuter après step18_powerbi_views.sql.
-- Ne pas confondre origine Gold synthétique et déclarations du portail.
SELECT 'effectif_rh' AS controle,COUNT(*)::bigint AS resultat
FROM rh.employee_versions e JOIN rh.import_batches b ON b.import_id=e.import_id
WHERE b.active
UNION ALL
SELECT 'activites_gold',COUNT(*) FROM dbt_marts.fact_activity
UNION ALL
SELECT 'annees_bi',COUNT(*) FROM powerbi.v_reporting_years
UNION ALL
SELECT 'lignes_resume_bi',COUNT(*) FROM powerbi.v_executive_year
UNION ALL
SELECT 'lignes_scenarios_bi',COUNT(*) FROM powerbi.v_scenario_history;

SELECT social_year,employee_count,gold_synthetic_activities,
       portal_declared_activities,latest_bonus_rate,
       latest_candidate_bonus_budget_eur,latest_total_illustrative_eur
FROM powerbi.v_executive_year ORDER BY social_year;

-- Un seul scénario 'dernier' pour chaque année sociale active.
SELECT social_year,COUNT(*) FILTER(WHERE is_latest) AS latest_count
FROM powerbi.v_scenario_history GROUP BY social_year ORDER BY social_year;

-- Les deux sources sportives ne sont jamais cachées dans un seul compte.
SELECT social_year,data_origin,SUM(activity_count)::bigint AS activity_count
FROM powerbi.v_activity_monthly
GROUP BY social_year,data_origin ORDER BY social_year,data_origin;
