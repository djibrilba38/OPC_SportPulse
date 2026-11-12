# Dictionnaire des données Power BI (sources agrégées)

| Vue PostgreSQL | Nom de table Power BI | Grain | Données importantes |
|---|---|---|---|
| `powerbi.v_executive_year` | `resume_annuel` | Une ligne / année sociale active | Effectif, Gold, portail, budgets du dernier scénario, droits RH enregistrés |
| `powerbi.v_activity_monthly` | `activites_mensuelles` | Année, mois, département, sport, origine | Nombre d'activités, distance, durée |
| `powerbi.v_employee_department` | `effectifs_departements` | Année, département | Effectif, candidats, masse salariale agrégée si groupe ≥5 |
| `powerbi.v_scenario_history` | `scenarios_budget` | Un scénario versionné (imports actifs **et archivés**) | Taux, règle, source, budget candidats, budget trajets validés, coût capacité bien-être |
| `powerbi.v_wellbeing_year` | `bienetre_annuel` | Année sociale | Activités portail, salariés à ≥15 activités, droits RH enregistrés |
| `powerbi.v_pipeline_status` | `executions_pipeline` | Une exécution technique | Pipeline, statut, durée |

## Définitions de gestion

- `gold_synthetique` : activité créée artificiellement à partir des fichiers du POC via Kestra. Aucune preuve d'activité physique réelle.
- `portail_declare` : activité du portail pour un compte RH explicitement lié, active, hors travail et non rejetée. Déclarée, **pas automatiquement approuvée**.
- `candidate_bonus_budget_eur` : calcul 5 % (ou un autre taux) de salaires bruts des **candidats**, sans accorder de prime.
- `validated_trips_and_salary_budget_eur` : estimation selon la présence de dossiers validés, ne constitue pas un paiement.
- `synthetic_wellbeing_eligible_count` : résultat basé sur des activités synthétiques et la règle de simulation, pas un droit accordé.
- `officially_entitled_days` : jours attribués et enregistrés dans le référentiel métier ; ils ne prouvent pas leur consommation.
- `combined_illustrative_cost_eur` : coût du scénario *candidats à la prime + capacité bien-être*. Ne pas additionner les scénarios entre eux.
- `activity_records_all_sources` : total d'enregistrements de deux origines. Ne peut pas être interprété comme un nombre d'activités physiquement certifiées.
- `social_start_month` : mois de début de l'année sociale défini dans `powerbi.settings`, par défaut janvier. Doit rester cohérent avec la configuration du portail et de Kestra.

## Contrôles / confidentialité

Les vues n'exposent ni noms, ni e-mails, ni salaires individuels, ni itinéraires GPS. Le référentiel salarial global et certains groupes agrégés restent des données RH sensibles ; les exports doivent être conservés localement dans un répertoire privé. N'accorder que les droits `sportpulse_powerbi_reader` au compte de connexion Power BI.
