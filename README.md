# Audit offres Forster

Analyse automatique des offres PDF (offre de prix, analyse matière, consultation vitrages) pour détecter
les erreurs d'étude et de prix, à partir d'une base de règles qui s'enrichit à chaque validation humaine.

Le modèle Claude n'est pas ré-entraîné : l'« apprentissage » vit dans la base (règles versionnées,
prix de référence, consignes données à Claude, historique des décisions).

## Lancer en local

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # puis renseigner ANTHROPIC_API_KEY
python -m app.db.seed              # crée la base, charge les règles et les prix
uvicorn main:app --reload
```

- Interface : http://127.0.0.1:8000/
- API documentée et testable (Swagger) : http://127.0.0.1:8000/docs
- Tests : `pytest` (aucun appel API, base temporaire)

`python -m app.db.seed` peut être relancé sans risque : il ajoute les nouvelles règles du code, crée une
nouvelle version des règles modifiées dans le code, et **ne touche pas** aux règles que vous avez modifiées
dans l'application (il les liste).

## Utilisation : le cycle d'apprentissage

1. **Analyser** : déposez une offre PDF. Claude extrait les données (30 s à 2 min), puis le moteur les
   compare aux règles actives et aux prix de référence.
2. **Traiter chaque anomalie** (onglet Résultat) :
   - **Valider** : vraie erreur. La règle est confirmée (compteur ✓). Une remarque de Claude validée devient
     une consigne « à signaler » pour les prochaines analyses ; un article inconnu validé devient un prix de référence.
   - **Rejeter** : faux positif. L'application **propose** : exception pour cet article, nouveau seuil,
     désactivation de la règle, ou consigne « ne pas signaler » pour une remarque de Claude.
     Rien n'est modifié tant que vous ne cliquez pas sur une proposition.
   - **Corriger** : saisissez la bonne valeur. Si elle contredit une règle ou un prix existant, le conflit
     est affiché (« la règle dit 5,9, votre correction dit 6 : laquelle est correcte ? ») avec la
     proposition de mise à jour.
3. **Règles** (onglet Règles) : liste des règles actives avec leurs décisions (✓ validées, ✗ rejetées,
   ✎ corrigées), historique complet des versions, désactivation et réactivation (motif obligatoire).
   Aucune règle n'est jamais supprimée : chaque changement crée une nouvelle version et archive l'ancienne.

Un même PDF n'est pas réanalysé par erreur (pas de second appel payant) : cochez « Réanalyser » pour forcer.

### Premier essai réel

`tests/fixtures/devis_fictif.pdf` est une offre **fictive** au format Forster, avec 6 erreurs volontaires
(décrites en tête de `tests/fixtures/generer_devis_fictif.py`). L'analyser dans l'interface vérifie
l'extraction réelle par Claude de bout en bout sans utiliser de données client.

### Tester sans appel API

Onglet Analyser → « Tester sans appel API » avec `tests/fixtures/extraction_cr4_fictive.json` (ou
`POST /analyses/simulation`) : tout le pipeline comparaison → anomalies → décisions fonctionne sans coût.

## Coût API

Modèle : `claude-sonnet-5-5` (2 $ par million de tokens en entrée, 10 $ en sortie).
Chaque page de PDF est lue en texte **et** en image : compter environ 3 000 à 4 500 tokens d'entrée par page,
plus le prompt et le schéma (environ 4 000 tokens). La sortie JSON fait 5 000 à 12 000 tokens selon le nombre
de lignes.

| Offre | Entrée | Sortie | Coût estimé |
|---|---|---|---|
| 3 pages (une porte) | ~15 000 tokens | ~5 000 tokens | ≈ 0,08 $ |
| 8 pages (5 positions) | ~35 000 tokens | ~10 000 tokens | ≈ 0,17 $ |

Quelques dizaines d'offres par mois : environ 3 à 8 $ par mois. Ce sont des estimations ; le coût réel est
enregistré pour chaque analyse :
- dans l'interface (en-tête : coût total et moyen par PDF ; fiche de l'analyse : tokens et coût) ;
- via `GET /statistiques` ;
- dans les logs du serveur, une ligne par extraction :
  `Extraction 'offre.pdf' : 8 page(s), 1 appel(s), scanné=False, tokens entrée=34512 sortie=9876, coût≈0.1678 $`

Un PDF de plus de 100 pages est découpé en plusieurs appels (réglable : `MAX_PAGES_PAR_APPEL`).

## Ajouter une règle ou un prix à la main

### Une règle

Par l'interface (onglet Règles → « Créer une règle à la main »), par Swagger (`POST /regles`), ou en ligne de commande :

```bash
curl -X POST http://127.0.0.1:8000/regles -H "Content-Type: application/json" -d '{
  "code": "PAUMELLES_PORTE_HAUTE",
  "description": "4 paumelles par vantail au-delà de 2 500 mm",
  "type": "etude", "famille": "ferrure", "gravite": "erreur",
  "condition": {"controle": "quantite_par_vantail", "references": ["907662"]},
  "seuil": 4
}'
```

Le champ `condition.controle` choisit le contrôle exécuté (liste dans `app/services/comparaison.py`, dictionnaire
`CONTROLES`) ; `seuil` et les autres clés de `condition` le paramètrent. Deux types particuliers :
- `consigne_ia` : texte transmis à Claude à chaque extraction, ex.
  `{"controle": "consigne_ia", "sens": "signaler", "texte": "Signaler toute porte CR4 sans cylindre hors lot"}` ;
- `remise_tarif` : remise par famille sur le tarif (utilisée quand la comparaison au tarif sera activée).

Modifier une règle : `PUT /regles/{code}` avec un `motif` (nouvelle version) ; désactiver :
`POST /regles/{code}/desactiver` avec un `motif`.

Pour une règle qui doit faire partie de l'installation de base, l'ajouter aussi dans
`app/services/regles_initiales.py` puis relancer `python -m app.db.seed`.

### Un prix de référence

Onglet Prix → « Ajouter ou remplacer un prix net », ou :

```bash
curl -X POST http://127.0.0.1:8000/prix -H "Content-Type: application/json" \
  -d '{"reference": "907350", "prix": 53.24, "famille": "ferrure", "unite": "pce"}'
```

L'ancien prix de l'article est archivé, pas supprimé.

## Prix de référence : tarif FFR + prix nets observés

Deux fichiers dans `data/` :
- `Tarif FFR 062026.json` (nom réglable par `FICHIER_PRIX` dans `.env`) : le tarif public FFR
  (3 691 articles, dont 91 « sur demande »). L'export brut de l'Excel est accepté tel quel et converti
  au chargement ; un fichier déjà converti par `import_tarif` fonctionne aussi ;
- `prix_nets_observes.json` (ou « Prix nets observes.json ») : les prix nets relevés dans les devis validés.

Contrôles de prix actuels :
- `PRIX_NUL` : prix manquant ou à zéro. 0 € accepté pour un article hors lot ; article hors tarif à 0 € non
  listé hors lot : simple rappel ; article au tarif à 0 € : erreur ;
- `ECART_PRIX_REF` (alerte) : écart au-delà de 15 % par rapport au prix net observé dans un devis validé ;
- article présent au tarif mais « sur demande » : signalé pour information ;
- article absent du tarif et de la base : proposé comme nouveau prix de référence.

La comparaison au tarif remisé est prête mais **désactivée** (`"comparer_tarif": false` dans la condition
de `ECART_PRIX_REF`) tant que les remises ne sont pas validées : règles `REMISE_PROFILE` 35 %,
`REMISE_FERRURE` 30 %, `REMISE_ACCESSOIRE` 30 %, `REMISE_JOINT` 30 % (valeurs constatées sur le devis 2026-XAG-01076).
Le tarif est rapproché du devis par le code article (`531.731 ZM` ↔ `531.731F.51.A`, `FFR102FND` ↔ `FFR.102F.ND.A`)
et la finition (ZM = 51, GF/INOX = 62), et ramené à l'unité du devis quand il est exprimé par unité de vente (UV).

Nouveau tarif : déposer l'export JSON dans `data/`, mettre `FICHIER_PRIX=<nom du fichier sans .json>` dans
`.env`, puis `python -m app.db.seed` (les prix modifiés archivent l'ancienne version).

## Organisation du code

```
main.py                         application FastAPI, page d'accueil, gestion des erreurs
app/api/routes.py               endpoints (analyses, anomalies, règles, prix, statistiques)
app/static/index.html           interface (une page, sans dépendance)
app/services/extraction.py      PDF -> JSON via l'API Claude (sortie structurée stricte)
app/services/comparaison.py     moteur de contrôles déterministe (sans IA)
app/services/regles_initiales.py règles issues des validations (avec leur origine)
app/services/apprentissage.py   décisions, propositions, conflits, versions de règles et de prix
app/services/analyse.py         orchestration d'une analyse
app/services/import_tarif.py    lecture et conversion du tarif
app/models/models.py            tables SQLAlchemy
app/db/                         connexion (SQLite, compatible PostgreSQL) et initialisation
tests/                          tests du moteur et de l'API, fixtures fictives
```

Passer à PostgreSQL : `DATABASE_URL=postgresql+psycopg://utilisateur:motdepasse@localhost/audit` dans `.env`
(installer `psycopg`). Le champ `utilisateur_id` présent sur toutes les tables permettra d'isoler les données
par utilisateur ou par équipe.

## Données sensibles

- `.env` (clé API), la base SQLite et les PDF (hors fixtures fictives) sont exclus de git. **Le fichier `.env` se crée
  uniquement sur votre PC** (copie de `.env.example`) : ne jamais le créer ni le modifier sur le site GitHub.
- Le tarif `data/Tarif FFR 062026.json` et `data/prix_nets_observes.json` sont versionnés dans le repo (choix du
  propriétaire) : garder le repo **privé**.
- Les logs ne contiennent que des métadonnées (nom du fichier, pages, tokens, coût), jamais le contenu des PDF.
- Le schéma de la base a changé depuis les premières versions : si vous aviez déjà une base `audit_offres.db`,
  supprimez-la puis relancez `python -m app.db.seed`.
