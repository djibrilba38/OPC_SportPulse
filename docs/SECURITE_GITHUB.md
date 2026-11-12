# Avant la publication sur GitHub

## Sécurité des fichiers

- [ ] Vérifier `git status --short` et `git status --ignored --short`.
- [ ] Contrôler qu'aucun `.env`, `.env.aws`, `.sqlite`, `.xlsx`, fichier RH, historique de run ou snapshot n'est suivi.
- [ ] Ne pas ajouter les clés AWS, Mistral, Slack, Grafana, PostgreSQL ou les tokens du portail dans les commits.
- [ ] Si des clés ont déjà été commitées ailleurs, les **révoquer** : le simple `.gitignore` ne nettoie pas l'historique Git.
- [ ] Exécuter les tests et inspecter le diff avant push.
- [ ] Exécuter un scanner de secrets tel que gitleaks/trufflehog si disponible.
- [ ] Préférer un dépôt **privé** tant que la classification des données n'est pas validée.

## Validation Git locale

```bash
git init
git add .
git status --short
git diff --cached --stat
# Inspecter soigneusement, puis :
git commit -m "chore: prepare secure SportPulse AI POC repository"
```

Les sources privées destinées au flow Kestra doivent être déposées **localement** dans `data/private_rh/`, jamais dans Git.
