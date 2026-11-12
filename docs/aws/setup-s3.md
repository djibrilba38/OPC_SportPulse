# Passer SportPulse AI sur Amazon S3 réel

Ce mode compose démarre uniquement l'application. Il ne démarre pas MinIO et ne crée pas les buckets AWS automatiquement.

## 1. Créer les buckets

Dans la console Amazon S3, sélectionnez la région `eu-west-3` (Paris) ou la région exigée par votre projet. Créez deux buckets avec des noms uniques, par exemple :

- `sportpulse-ai-raw-<AWS_ACCOUNT_ID>`
- `sportpulse-ai-artifacts-<AWS_ACCOUNT_ID>`

Remplacez le suffixe par votre ID de compte à 12 chiffres, puis vérifiez que les noms sont disponibles. Gardez **Block all public access** activé, utilisez **Bucket owner enforced** pour Object Ownership et conservez le chiffrement par défaut activé. Ne rendez pas ces buckets publics.

## 2. Donner des permissions minimales

Dans IAM, créez une politique à partir de `docs/aws/iam-policy-sportpulse-s3.json`. Remplacez les deux occurrences de `REPLACE_WITH_RAW_BUCKET_NAME` par le nom exact du bucket brut et les deux occurrences de `REPLACE_WITH_ARTIFACTS_BUCKET_NAME` par le nom exact du bucket artefacts.

Attachez cette politique à une identité dédiée à SportPulse. Elle autorise la vérification des buckets, l'envoi d'objets et l'abandon d'un multipart upload, sans autoriser la suppression, la lecture du contenu ou l'accès à d'autres buckets. Si l'architecture évolue, ajustez les actions au besoin réel.

Pour un environnement déployé sur AWS, préférez un rôle IAM à des clés longues durées. Pour un POC local, utilisez une identité dédiée avec permissions minimales et une clé non-root. Ne créez pas de clés d'accès pour l'utilisateur root du compte AWS.

## 3. Préparer la configuration locale

Depuis la racine du dépôt :

```bash
cp .env.aws.example .env.aws
```

Éditez `.env.aws` et renseignez les identifiants de l'identité IAM dédiée, la région et les deux noms exacts de buckets. Si AWS vous a fourni des identifiants temporaires, renseignez aussi `AWS_SESSION_TOKEN`. Gardez le fichier `.env.aws` hors de Git.

Le fichier `compose.aws.yaml` force `S3_ENDPOINT_URL` à une chaîne vide. C'est intentionnel : Boto3 utilise alors l'endpoint AWS standard, pas `http://minio:9000`.

## 4. Démarrer le mode AWS

Arrêtez d'abord le service local qui occupe le port 8000, si la stack MinIO tourne encore :

```bash
docker compose stop app
```

Puis construisez et démarrez l'application AWS :

```bash
docker compose -f compose.aws.yaml up --build -d
```

Contrôlez les logs et la connexion aux deux buckets :

```bash
docker compose -f compose.aws.yaml logs --tail=100 app
curl -i http://localhost:8000/health/storage
```

La route de stockage doit répondre `200` avec `"status":"ok"`. Si elle renvoie `503`, vérifiez les noms exacts, la région, le statut des clés et les permissions IAM.

## 5. Charger uniquement les fichiers de démonstration

Avant d'ingérer des fichiers RH réels, testez la configuration avec les données synthétiques déjà présentes :

```bash
docker compose -f compose.aws.yaml run --rm app \
  python -m sportpulse.cli ingest --input-dir /app/data/source
```

Vérifiez dans la console Amazon S3 les objets présents dans le bucket brut sous `source_files/` et le manifeste dans le bucket artefacts sous `ingestion-manifests/`.

## Sécurité et coûts

- N'envoyez pas encore de vrais fichiers RH ou des données personnelles ; validez d'abord les droits, la pseudonymisation, la région et la rétention.
- Gardez le blocage d'accès public activé et ne mettez jamais de clés dans le code ou les journaux.
- Le stockage, les requêtes, les transferts et certains journaux AWS peuvent entraîner des coûts. Supprimez les ressources de démonstration uniquement après avoir confirmé qu'elles ne sont plus nécessaires.
- Ne partagez jamais `AWS_SECRET_ACCESS_KEY` ou `AWS_SESSION_TOKEN` dans une conversation ou une capture d'écran.
