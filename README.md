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

## Prix de référence : tarif FFR + prix nets observés

Le fichier `data/Tarif FFR 062026.json` (nom réglable par `FICHIER_PRIX` dans `.env`) contient :
- `articles` : le tarif public FFR (3 691 articles, dont 91 « sur demande ») ;
- `prix_nets_observes` : les prix nets relevés dans les devis validés.

Contrôles de prix actuels :
- `PRIX_NUL` (erreur) : chaque article et chaque position doit avoir un prix, ni manquant ni à zéro ;
- `ECART_PRIX_REF` (alerte) : écart au-delà de 15 % par rapport au prix net observé dans un devis validé ;
- article présent au tarif mais « sur demande » : signalé pour information ;
- article absent du tarif et de la base : proposé comme nouveau prix de référence.

La comparaison au tarif remisé est prête mais **désactivée** (`"comparer_tarif": false` dans la condition
de `ECART_PRIX_REF`) tant que les remises ne sont pas validées : règles `REMISE_PROFILE` 35 %,
`REMISE_FERRURE` 30 %, `REMISE_ACCESSOIRE` 30 %, `REMISE_JOINT` 30 % (valeurs constatées sur le devis 2026-XAG-01076).
Le tarif est rapproché du devis par le code article (`531.731 ZM` ↔ `531.731F.51.A`) et la finition
(ZM = 51, GF/INOX = 62), et ramené à l'unité du devis quand il est exprimé par unité de vente (UV).

Nouveau tarif (export JSON de l'Excel) :

```bash
python -m app.services.import_tarif export.json --nom "Tarif FFR 012027" --nets "data/Tarif FFR 062026.json"
# puis FICHIER_PRIX=Tarif FFR 012027 dans .env, et :
python -m app.db.seed   # les prix modifiés archivent l'ancienne version
```

## Données sensibles

- `.env` (clé API), la base SQLite, les PDF et les fichiers de prix réels `data/*.json` sont exclus de git.
- Sans fichier de prix réel, l'initialisation charge `data/tarif.exemple.json` (prix fictifs).
- Le schéma de la base a changé (tarif) : si vous aviez déjà lancé `python -m app.db.seed`, supprimez
  `audit_offres.db` puis relancez-le.
- Les logs ne contiennent que des métadonnées (nom du fichier, pages, tokens, coût), jamais le contenu des PDF.
