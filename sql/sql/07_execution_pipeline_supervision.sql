-- Temps d'exécution et statuts, à afficher sous forme de tableau opérationnel.
SELECT pipeline, status, started_at, finished_at,
       ROUND(duration_seconds/60.0,2) AS duree_minutes,
       CASE WHEN finished_at IS NULL THEN 'EN_COURS_OU_INCOMPLET'
            WHEN UPPER(status) IN ('SUCCESS','SUCCEEDED','COMPLETED','PASS') THEN 'SUCCES'
            ELSE 'A_VERIFIER' END AS diagnostic_bi
FROM powerbi.v_pipeline_status
ORDER BY started_at DESC
LIMIT 50;
