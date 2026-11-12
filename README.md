<<<<<<< HEAD
<div align="center">

# 🏃 SportPulse AI

### De l'activité sportive aux indicateurs RH : un POC traçable et rejouable

**FastAPI · Python · PostgreSQL · Kestra · dbt · AWS S3 · Slack · Metabase · Prometheus · Grafana**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Status](https://img.shields.io/badge/Statut-POC%20de%20d%C3%A9monstration-0F766E)

**Projet 12 · Data Engineering · Soutenance Option B**

[Présentation](#-présentation) · [Architecture](#-architecture) · [Installation](#-installation) · [Démo](#-démonstration-de-bout-en-bout) · [Tests](#-qualité-et-tests) · [Sécurité](#-sécurité-et-confidentialité)

</div>

---

## 🎯 Présentation

**SportPulse AI** est un POC de plateforme de données destiné à **évaluer** des avantages RH liés à l'activité physique et à encourager une pratique sportive régulière. Il combine une application web, une chaîne batch auditable, des notifications et des tableaux de bord.

Le POC répond à trois questions :

1. **Faisabilité** — peut-on collecter, contrôler et exploiter les données de bout en bout ?
2. **Données & sécurité** — quelles données collecter, comment en limiter l'exposition et tracer les opérations ?
3. **Impact financier** — combien coûteraient une prime de mobilité et des journées bien-être selon différents scénarios ?

> [!IMPORTANT]
> **Ce dépôt est un POC, pas un système de paie.** Les activités historiques utilisées pour la démonstration peuvent être **synthétiques**. Les trajets déclarés, budgets et journées bien-être sont **prévisionnels**, soumis à une validation RH. Aucune prime ni journée n'est attribuée ou versée automatiquement.

### Les deux avantages étudiés

| Dispositif | Règle de simulation | Limite métier |
|---|---|---|
| 🚲 **Prime de mobilité sportive** | **5 %** du salaire annuel brut ; test d'un scénario à **6 %** | Un trajet déclaré n'est pas une preuve de déplacement validée par les RH. |
| 🌿 **Journées bien-être** | Jusqu'à **5 jours/an** ; seuil expérimental de **15 activités/an** | Une activité déclarée n'ouvre pas automatiquement un droit. |

### Capacités du POC

- **Portail salarié** : authentification, déclaration d'activité, suivi des objectifs et itinéraires indicatifs.
- **Administration RH** : données agrégées, simulation de taux et historique des calculs.
- **Pipeline Kestra / S3 / Python / dbt** : préparation des sources, Bronze, Silver, contrôles qualité, Gold et reporting.
- **PostgreSQL** : données analytiques, règles métier, versions et vues BI.
- **Slack Worker** : outbox de notifications, reprise sur erreur, publication opt-in.
- **Metabase / exports Power BI** : tableaux de bord sportifs et financiers.
- **Prometheus + Grafana** : supervision HTTP de l'application (voir [périmètre réel](#-monitoring)).

---

## 🧭 Architecture

```mermaid
flowchart LR
    EMP[Collaborateurs] --> PORTAL[Portail salarié]
    PORTAL --> API[FastAPI]
    API --> PG[(PostgreSQL)]
    API --> DEMO[(SQLite démo / admin)]

    RH[Classeur RH et sport\nHORS GIT] --> KE[Kestra]
    KE --> BR[S3 Bronze\nSHA-256]
    BR --> SI[Python Silver\ncontrôles et rejets]
    SI --> PG
    PG --> DBT[dbt / modèles Gold]
    DBT --> PG

    PG --> MB[Metabase / vues BI]
    PG --> OUT[Outbox]
    OUT --> WORKER[Slack Worker]
    WORKER --> SLACK[Slack]

    API -. métriques HTTP .-> PROM[Prometheus]
    PROM --> GRAF[Grafana]

    classDef app fill:#E3F2FD,stroke:#2563EB,color:#172554;
    classDef data fill:#EDE9FE,stroke:#7C3AED,color:#312E81;
    classDef output fill:#D1FAE5,stroke:#059669,color:#064E3B;
    classDef batch fill:#FEF3C7,stroke:#D97706,color:#78350F;
    class API,PORTAL app;
    class PG,DEMO,OUT data;
    class KE,BR,SI,DBT batch;
    class MB,WORKER,SLACK,PROM,GRAF output;
```

**Deux flux distincts** :

- **Batch rejouable** : sources RH/sport → Kestra → S3 Bronze → Python Silver → PostgreSQL / dbt → vues analytiques → Metabase.
- **Déclaration applicative** : portail → FastAPI → stockage + outbox → Slack Worker → canal Slack ; les déclarations remontent aussi au reporting selon les vues utilisées.

📘 Détails techniques : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 🛠 Stack technique

| Couche | Outils | Rôle |
|---|---|---|
| Application | **FastAPI, Python 3.12** | Portail salarié, administration, API, métriques |
| Stockage métier | **PostgreSQL 16** | Sources consolidées, règles, scénarios, vues BI |
| État local de démonstration | **SQLite** | Comptes de démonstration et historique admin local |
| Orchestration | **Kestra** | Étapes, paramètres, exécutions et relances |
| Fichiers / stockage objet | **AWS S3** | Bronze, artefacts, empreintes SHA-256 |
| Transformation | **Python, dbt** | Silver, Gold, tests et datamarts |
| Notifications | **Slack API + Worker** | Messages sportifs asynchrones, mode dry-run/live |
| Visualisation | **Metabase** ; ressources Power BI | KPI sportifs, budget et scénarios |
| Observabilité | **Prometheus, Grafana** | Santé et métriques HTTP de FastAPI |
| Environnement | **Docker Compose** | Services isolés pour le POC |

---

## 🚀 Installation

### Prérequis

- **Docker Desktop** (ou Docker Engine avec `docker compose`).
- Une machine pouvant exécuter les services Java de Kestra/Metabase et PostgreSQL.
- **Python 3.12** recommandé pour les scripts locaux.
- **Identifiants AWS S3** et buckets existants si vous voulez exécuter **le pipeline batch complet**.
- Slack et Mistral sont **facultatifs** : le POC fonctionne avec notifications en `dry_run` et messages `template`.

### 1. Télécharger et configurer

```bash
git clone https://github.com/VOTRE_COMPTE/VOTRE_DEPOT.git
cd VOTRE_DEPOT
cp .env.example .env
cp .env.aws.example .env.aws
```

> [!CAUTION]
> **Ne lancez pas le projet avec les valeurs d'exemple.** Remplacez les mots de passe, secrets et identifiants `CHANGE_ME` / `REPLACE_WITH_...` dans `.env` et `.env.aws`. Le mot de passe de `DATABASE_URL` doit être identique à `POSTGRES_PASSWORD`.

Dans `.env`, configurez impérativement :

```dotenv
POSTGRES_PASSWORD=VOTRE_MOT_DE_PASSE_LONG
DATABASE_URL=postgresql://sportpulse:VOTRE_MOT_DE_PASSE_LONG@postgres:5432/sportpulse
KESTRA_DB_PASSWORD=VOTRE_MOT_DE_PASSE_KESTRA
SPORTPULSE_HOST_PROJECT_DIR=/chemin/absolu/vers/le/depot
SPORTPULSE_DOCKER_NETWORK=sportpulse-ai-step_default
SPORTPULSE_SLACK_MODE=dry_run
SPORTPULSE_MOTIVATION_BACKEND=template
```

En complément, générez des valeurs aléatoires robustes pour `SPORTPULSE_VALIDATION_SECRET`, `SPORTPULSE_AI_TOKEN`, `MB_ENCRYPTION_SECRET_KEY`, `GRAFANA_ADMIN_PASSWORD` et les secrets éventuellement utilisés par les QR codes.

Sous macOS/Linux, la commande `pwd` donne la valeur à placer dans `SPORTPULSE_HOST_PROJECT_DIR`. **Kestra lance des tâches via Docker** et utilise ce chemin hôte pour monter les fichiers : il doit donc être accessible à Docker. Ne modifiez pas le nom de réseau par défaut sans actualiser la variable correspondante.

### 2. Démarrer les services

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8000/health
```

| Service | URL locale | Usage |
|---|---|---|
| SportPulse AI | <http://localhost:8000/employee> | Portail salarié |
| Administration RH | <http://localhost:8000/admin> | Simulation des avantages |
| Reporting applicatif | <http://localhost:8000/admin/reporting> | Indicateurs RH |
| API / docs | <http://localhost:8000/docs> | Routes documentées |
| Kestra | <http://localhost:8080> | Exécutions du workflow |
| Metabase | <http://localhost:3000> | Reporting BI |
| Grafana | <http://localhost:3001> | Monitoring |
| Prometheus | <http://localhost:9090> | Métriques |

**PostgreSQL** est exposé au Mac sur `127.0.0.1:5433` et sur `postgres:5432` *entre conteneurs*. Deux conteneurs PostgreSQL distincts sont utilisés : l'un pour SportPulse, l'autre pour l'état de Kestra.

**Aucun compte administrateur n'est créé par défaut.** Pour en créer un localement :

```bash
docker compose exec app python -m sportpulse.admin_portal create-admin --email admin@example.org
```

Pour créer une invitation **fictive** à usage unique :

```bash
docker compose exec app python -m sportpulse.employee_portal create-invite \
  --employee-id DEMO-001 --name "Camille Démo" --demo
```

N'insérez jamais ces invitations ni les mots de passe dans le dépôt.


> [!NOTE]
> Sur un **nouveau volume PostgreSQL**, un script crée la base interne `metabase_app`. Sur une installation existante, les volumes ne sont pas réinitialisés. Les vues BI et les schémas métier dépendent des migrations / exécutions de pipeline, qui ne sont pas créées par la simple commande `docker compose up`.

### 3. Préparer des données **fictives** pour le batch

Le workflow versionné `kestra/flow/sportpulse_pipeline_complet.yml` attend deux fichiers locaux :

```text
data/private_rh/Donnees_RH.xlsx
data/private_rh/Donnees_Sportive.xlsx
```

Les classeurs RH d'origine sont **volontairement absents du dépôt**. Pour essayer le workflow sur des données fictives, utilisez le générateur fourni :

Exécutez ce script **sur votre Mac** avec Python et `openpyxl` (le Dockerfile applicatif ne copie volontairement pas le dossier `scripts`) :

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_demo_rh.py
```

Ce script produit des données **entièrement synthétiques** ; il n'écrase aucun fichier existant sans l'option `--force`. Il ne faut jamais committer les fichiers générés.

### 4. Initialiser les schémas et importer le workflow

- Les migrations SQL se trouvent dans `sql/` (`init_schema.sql`, tables `benefits`, tables `rh`, vues analytiques et `powerbi`). Leur ordre et leurs prérequis sont documentés dans les en-têtes des scripts ; appliquez les migrations adaptées à votre environnement **avant** d'exécuter le pipeline.
- Le workflow est dans [`kestra/flow/sportpulse_pipeline_complet.yml`](kestra/flow/sportpulse_pipeline_complet.yml). Importez-le dans Kestra, après configuration du réseau Docker et d'AWS S3.
- Les traitements Bronze/Silver/Gold nécessitent des buckets S3 accessibles et les autorisations correspondantes : voir [`docs/aws/setup-s3.md`](docs/aws/setup-s3.md).
- Exécution de démonstration : `reference_year=2026`, `bonus_rate=0.05`, `rules_version=rules_v1`, puis `bonus_rate=0.06`, `rules_version=rules_v2`.

Une commande SQL de contrôle peut s'exécuter ainsi :

```bash
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/init_schema.sql
```

**Ne rejouez pas aveuglément toutes les migrations sur une base RH existante** : vérifiez les dépendances et effectuez une sauvegarde au préalable.

### 5. Connecter Metabase à la bonne base

Metabase utilise `metabase_app` pour **ses propres métadonnées**. Les **KPI SportPulse** sont dans la base PostgreSQL `sportpulse` : ajoutez-la depuis *Administration → Bases de données*.

- Hôte : `postgres` · Port : `5432`
- Base métier : `sportpulse` · Utilisateur : compte autorisé sur les vues
- Vue de consolidation : `powerbi.v_activity_monthly`

Contrôle des activités Gold + portail :

```sql
SELECT data_origin, SUM(activity_count) AS total_activites
FROM powerbi.v_activity_monthly
WHERE social_year = 2026
GROUP BY data_origin
ORDER BY data_origin;
```

La vue doit avoir été créée par les migrations et alimentée par l'import / le portail. Ne confondez pas **la base de métadonnées Metabase** avec **la source analytique PostgreSQL**.

---

## 🎬 Démonstration de bout en bout

Deux scénarios servent à vérifier les compétences attendues lors de la soutenance.

### A — Rejouer un scénario financier

1. Depuis l'administration RH ou Kestra, constater la situation de référence au taux **5 %**.
2. Relancer un calcul au taux **6 %**, avec une **nouvelle version des règles**.
3. Consulter l'historique des scénarios ; comparer budgets calculés, dates et paramètres.
4. Rafraîchir le reporting et vérifier la prise en compte du nouveau scénario.

> Un taux saisi dans un formulaire n'est **pas** une preuve de recalcul : utiliser la fonction « Calculer et enregistrer » / lancer le workflow, puis vérifier son historique.

### B — Créer une activité et observer sa diffusion

1. Ouvrir <http://localhost:8000/employee> avec **un compte de démonstration**.
2. Enregistrer une nouvelle activité fictive et noter son identifiant.
3. Vérifier sa persistance dans PostgreSQL (ou dans la SQLite locale selon le parcours).
4. Inspecter les événements de l'outbox et les logs du worker :

   ```bash
   docker compose logs --since=5m -f slack-worker
   ```

5. Vérifier la publication **uniquement si** `SPORTPULSE_SLACK_MODE=live` et que le bot Slack est autorisé ; sinon tester le `dry_run`.
6. Rafraîchir Metabase ; vérifier l'incrément du nombre d'activités sur les cartes alimentées par les vues incluant `portail_declare`.

**Sécurité de la démo** : utiliser des noms et activités fictifs ; ne pas projeter de clé API, salaire ou donnée RH individuelle.

---

## 📊 Reporting et indicateurs

Les principaux indicateurs sont : nombre d'activités, salariés actifs, distance, durée, fréquence, répartition par type/département, éligibilité potentielle, journées bien-être, coût simulé et budget de prime selon le taux/version.

- **Couche Gold** : tables et vues PostgreSQL produites par la chaîne de transformation.
- **Couche BI** : vues `powerbi.*` pour relier activités historiques et déclarations live.
- **Metabase** : tableaux de bord à brancher sur la **bonne base**, en vérifiant le rafraîchissement / éventuel cache.
- **Power BI** : dictionnaire, mesures DAX, requêtes et exports dans [`powerbi/`](powerbi/).

Les valeurs des captures de démonstration ne sont **pas codées en dur** dans les requêtes du dépôt : elles dépendent des données et de l'historique chargés.

## 📈 Monitoring

Prometheus collecte les métriques HTTP exposées sur `/metrics` par FastAPI. Grafana affiche notamment : disponibilité de l'API, volume de requêtes, proportion d'erreurs HTTP, latence et réponses par code/endpoint.

> [!NOTE]
> Ce POC ne dispose **pas encore** d'exporters complets pour PostgreSQL, Kestra, Metabase et les états internes du worker. Les logs Docker et les traces Kestra permettent des contrôles complémentaires, mais ne remplacent pas une supervision exhaustive en production.

```bash
curl -fsS http://localhost:8000/health
docker compose logs --tail=100 app
docker compose logs --tail=100 kestra
docker compose logs --tail=100 slack-worker
```

## ✅ Qualité et tests

Exécuter les tests Python hors Docker :

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
PYTHONPATH=src python -m pytest -q
```

Valider les modèles dbt, **après** préparation de la base de données :

```bash
docker compose --profile tools run --rm dbt build --project-dir /usr/app --profiles-dir /usr/app
```

Les tests de bout en bout (Kestra/S3/PostgreSQL/Slack) exigent des services, des identifiants et des données de démonstration ; ils ne sont pas couverts par une simple compilation Python.

## 🔐 Sécurité et confidentialité

- Données RH et salaires : **jamais dans Git**, jamais dans les messages Slack, accès restreints.
- Données de démonstration : identifiées comme synthétiques ; aucune attribution automatique d'avantage.
- Configuration : secrets dans `.env` / `.env.aws` **ignorés par Git** ; seuls les modèles `*.example` sont versionnés.
- Notifications : **`dry_run` par défaut** ; possibilité d'utiliser des templates sans requête IA externe.
- Mistral : l'API est optionnelle ; si elle échoue (ex. HTTP 401), un message prédéfini est utilisé. **Le RAG lexical du portail est distinct** de la fonction de motivation Slack.
- Réseau : services exposés en `127.0.0.1` sur le poste de démonstration.
- **Kestra a accès au socket Docker** dans ce POC : c'est un privilège élevé, à isoler et à revoir avant production.

✅ Checklist de publication : [`docs/SECURITE_GITHUB.md`](docs/SECURITE_GITHUB.md).

## 📁 Structure du dépôt

```text
SportPulse_AI_GitHub/
├── src/sportpulse/           # API, portails, modules métier, Slack, RAG
├── tests/                    # Tests unitaires et tests API
├── kestra/flow/              # Workflow du pipeline versionné
├── dbt/                      # Modèles et tests dbt
├── sql/                      # Schémas, migrations, vues BI et contrôles
├── powerbi/                  # Documentation, DAX et requêtes Power BI
├── monitoring/               # Configuration Prometheus et Grafana
├── docs/                     # Architecture, AWS et sécurité GitHub
├── scripts/                  # Initialisation et génération RH fictive
├── data/source/              # Exemples CSV fictifs (uniquement)
├── data/private_rh/          # Sources privées, ignorées par Git
├── compose.yaml
├── Dockerfile
├── .env.example
└── .env.aws.example
```

## ⚠️ Limites connues

- Le traitement historique est **synthétique** et dépend de l'apport de deux fichiers RH/sport **hors Git** et d'un stockage S3 autorisé.
- Les validations de trajets, de salaires et de droits restent du ressort des RH ; aucun calcul n'est un bulletin de paie.
- Les comptes de démonstration SQLite ne doivent pas être utilisés comme mécanisme de production multi-instance.
- La supervision est ciblée sur FastAPI et ses métriques HTTP ; exporters additionnels nécessaires pour couvrir le reste du système.
- Les secrets et les données locales ont été retirés de l'archive distribuée ; une instance fraîche n'affiche **pas automatiquement les KPI historiques** de la machine de développement.
- Metabase exige une connexion métier séparée de sa base de métadonnées, ainsi que les migrations SQL et les sources effectivement chargées.
- Le dossier documentaire `knowledge/` est vide dans cette publication ; la fonctionnalité RAG nécessite des documents autorisés et sa propre validation.

---

<div align="center">

### 🎓 SportPulse AI · Projet 12 · POC Data Engineering

*Collecter avec rigueur · Transformer avec traçabilité · Restituer avec prudence*

</div>
=======
# OPC_SportPulse
SportPulse AI est un POC de plateforme de données destiné à évaluer des avantages RH liés à l'activité physique et à encourager une pratique sportive régulière. Il combine une application web, une chaîne batch auditable, des notifications et des tableaux de bord.
>>>>>>> c1601b50a57a4222ad3a8bdd531550f07ce64e11
