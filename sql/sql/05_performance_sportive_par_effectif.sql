-- Activités/100 salariés : comparaison plus juste que le nombre brut par département.
-- Les deux origines de données restent explicitement séparées.
SELECT a.social_year,a.department,a.data_origin,
       d.employee_count AS effectif_departement,
       SUM(a.activity_count) AS activites,
       ROUND(100.0 * SUM(a.activity_count) / NULLIF(d.employee_count,0),1)
          AS activites_pour_100_salaries,
       ROUND(SUM(a.distance_km),2) AS kilometres,
       ROUND(SUM(a.duration_min)/60.0,1) AS heures_activite
FROM powerbi.v_activity_monthly a
JOIN powerbi.v_employee_department d
  ON d.social_year=a.social_year AND d.department=a.department
WHERE a.social_year=2026
GROUP BY a.social_year,a.department,a.data_origin,d.employee_count
ORDER BY a.data_origin,activites_pour_100_salaries DESC;
