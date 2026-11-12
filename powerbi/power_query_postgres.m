// Exemple Power Query M à utiliser dans Power BI Desktop (Windows).
// Paramètres à créer : ServerName (texte), DatabaseName (texte).
// Depuis Power BI Desktop tournant sur la même machine que PostgreSQL :
//   ServerName = "localhost:5433"
//   DatabaseName = "sportpulse"
// Sur Windows séparé du Mac : localhost désigne Windows, PAS le Mac ;
// prévoir une connexion réseau privée ou un tunnel SSH (pas d'ouverture publique de 5433).
// Chaque requête suivante doit être créée séparément dans l'Éditeur avancé.

// Requête : resume_annuel
let
    Source = PostgreSQL.Database(ServerName, DatabaseName),
    Donnees = Source{[Schema="powerbi",Item="v_executive_year"]}[Data]
in
    Donnees

// Requête : activites_mensuelles
let
    Source = PostgreSQL.Database(ServerName, DatabaseName),
    Donnees = Source{[Schema="powerbi",Item="v_activity_monthly"]}[Data]
in
    Donnees

// Requête : effectifs_departements
let
    Source = PostgreSQL.Database(ServerName, DatabaseName),
    Donnees = Source{[Schema="powerbi",Item="v_employee_department"]}[Data]
in
    Donnees

// Requête : scenarios_budget
let
    Source = PostgreSQL.Database(ServerName, DatabaseName),
    Donnees = Source{[Schema="powerbi",Item="v_scenario_history"]}[Data]
in
    Donnees

// Requête : bienetre_annuel
let
    Source = PostgreSQL.Database(ServerName, DatabaseName),
    Donnees = Source{[Schema="powerbi",Item="v_wellbeing_year"]}[Data]
in
    Donnees

// Requête : executions_pipeline
let
    Source = PostgreSQL.Database(ServerName, DatabaseName),
    Donnees = Source{[Schema="powerbi",Item="v_pipeline_status"]}[Data]
in
    Donnees
