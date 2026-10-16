# Audit offres Forster

Analyse automatique des offres PDF (offre de prix, analyse matière, consultation vitrages) pour détecter
les erreurs d'étude et de prix, à partir d'une base de règles qui s'enrichit à chaque validation humaine.

Le modèle Claude n'est pas ré-entraîné : l'« apprentissage » vit dans la base (règles versionnées,
prix de référence, consignes, historique des décisions).

> Phase 1 en cours. Ce README sera complété à l'étape 4 (endpoints, coûts API, ajout manuel de règles et de prix).

## État d'avancement

- [x] Étape 1 : structure du projet, modèles SQLAlchemy (règles versionnées, prix, analyses, anomalies)
- [x] Étape 2 : extraction PDF via l'API Claude (sortie JSON stricte, `texte_source`, `a_verifier`)
- [x] Étape 3 : moteur de comparaison + règles issues de la première validation + tests
- [ ] Étape 4 : endpoints FastAPI (upload, analyse, validation, gestion des règles), interface minimale

## Lancer en local

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # puis renseigner ANTHROPIC_API_KEY
python -m app.db.seed              # crée la base et charge les règles et prix initiaux
uvicorn main:app --reload          # http://127.0.0.1:8000/docs
pytest                             # tests du moteur de comparaison
```

## Données sensibles

- `.env` (clé API), la base SQLite, les PDF et `data/prix_initiaux.json` (prix réels) sont exclus de git.
- Sans `data/prix_initiaux.json`, l'initialisation charge `data/prix_initiaux.exemple.json` (prix fictifs).
- Les logs ne contiennent que des métadonnées (nom du fichier, pages, tokens, coût), jamais le contenu des PDF.
