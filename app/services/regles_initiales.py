"""
Règles issues du premier cycle d'apprentissage (devis 2026-XAG-01076, validé le 16/10/2026).

Fichier de données pur (aucune dépendance base) : utilisé par le script
d'initialisation (app/db/seed.py) et par les tests.

Les règles rejetées sont conservées avec statut "archive" et leur motif :
c'est l'historique qui explique pourquoi elles n'existent plus.
"""

SOURCE = "2026-XAG-01076 (validation du 16/10/2026)"

REGLES_INITIALES: list[dict] = [
    # --- Règles actives --------------------------------------------------------
    {
        "code": "SOMME_POSITIONS",
        "description": "La somme des totaux des positions doit être égale au total HT de l'offre.",
        "type": "prix", "famille": "offre", "gravite": "erreur",
        "condition": {"controle": "somme_positions"},
        "seuil": 0.05,  # tolérance en € (arrondis)
    },
    {
        "code": "PRIX_NUL",
        "description": "Aucun prix unitaire ni total ne doit être à zéro (offre et analyse matière).",
        "type": "prix", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "prix_nul"},
        "seuil": None,
    },
    {
        "code": "ECART_PRIX_REF",
        "description": "Alerte si un prix unitaire s'écarte du prix de référence au-delà de la tolérance.",
        "type": "prix", "famille": "global", "gravite": "alerte",
        "condition": {"controle": "ecart_prix_reference"},
        "seuil": None,  # None = tolérance globale (TOLERANCE_PRIX_PCT, 15 % par défaut)
    },
    {
        "code": "PAUMELLES_PAR_VANTAIL",
        "description": "3 paumelles par vantail.",
        "type": "etude", "famille": "ferrure", "gravite": "erreur",
        "condition": {"controle": "quantite_par_vantail", "references": ["907662"], "mots_cles": ["paumelle"]},
        "seuil": 3,
    },
    {
        "code": "GACHE_PAR_VANTAIL",
        "description": "1 gâche par vantail.",
        "type": "etude", "famille": "ferrure", "gravite": "erreur",
        "condition": {"controle": "quantite_par_vantail", "references": ["987009"], "mots_cles": ["gâche", "gache"]},
        "seuil": 1,
    },
    {
        "code": "AXE_POIGNEE_PAR_VANTAIL",
        "description": "1 axe (carré) de poignée par vantail.",
        "type": "etude", "famille": "accessoire", "gravite": "erreur",
        "condition": {"controle": "quantite_par_vantail", "references": ["907340"], "mots_cles": ["handle shaft", "carré de poignée"]},
        "seuil": 1,
    },
    {
        "code": "PARCLOSE_VS_PERIMETRE",
        "description": "La longueur de parclose nécessaire doit correspondre au périmètre total des vitrages (± seuil %).",
        "type": "etude", "famille": "profile", "gravite": "erreur",
        "condition": {"controle": "longueur_vs_perimetre_vitrage", "references": ["901327"], "mots_cles": ["parclose"]},
        "seuil": 5,
    },
    {
        "code": "JOINT_VITRAGE_VS_PERIMETRE",
        "description": "La longueur de joint de vitrage nécessaire doit correspondre au périmètre total des vitrages (± seuil %).",
        "type": "etude", "famille": "joint", "gravite": "erreur",
        "condition": {"controle": "longueur_vs_perimetre_vitrage", "references": ["905386", "985302"], "mots_cles": ["joint de vitrage"]},
        "seuil": 5,
    },
    {
        "code": "COHERENCE_PARCLOSE",
        "description": "Le matériau de parclose doit être le même dans la configuration, la description du châssis et l'analyse matière.",
        "type": "etude", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "coherence_materiau_parclose"},
        "seuil": None,
    },
    {
        "code": "TOTAL_OFFRE_VS_MATIERE",
        "description": "Le total HT de l'offre de prix doit être égal au total de l'analyse matière.",
        "type": "prix", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "total_offre_vs_matiere"},
        "seuil": 1.0,  # tolérance en €
    },
    {
        "code": "BLOC_DESCRIPTIF_PORTE",
        "description": "Toute position comportant une porte doit avoir son bloc descriptif (type, configurations, inclus, hors gamme).",
        "type": "etude", "famille": "offre", "gravite": "erreur",
        "condition": {"controle": "bloc_descriptif_porte"},
        "seuil": None,
    },
    {
        "code": "CHUTE_CONDITIONNEMENT",
        "description": "Signaler pour information les articles dont la quantité commandée dépasse fortement le besoin (conditionnement).",
        "type": "etude", "famille": "global", "gravite": "info",
        "condition": {"controle": "chute_conditionnement"},
        "seuil": 35,  # % de chute
    },
    {
        "code": "CONSIGNE_PLAQUE_987081",
        "description": "La plaque de fixation 987081 RC/E30/EW30 est normale dans une série Standard.",
        "type": "etude", "famille": "accessoire", "gravite": "info",
        "condition": {
            "controle": "consigne_ia",
            "sens": "ignorer",
            "texte": "La plaque de fixation 987081 (RC/E30/EW30) est normale même en série Standard : ne pas la signaler.",
        },
        "seuil": None,
    },
    # --- Règles proposées puis rejetées (historique) ----------------------------
    {
        "code": "TOTAL_LIGNE_CALCUL",
        "description": "Total de ligne matière = quantité commandée × prix unitaire.",
        "type": "prix", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "total_ligne"},
        "seuil": None,
        "statut": "archive",
        "motif_changement": "Rejetée : ne pas recalculer les lignes. Remplacée par PRIX_NUL (erreur) et ECART_PRIX_REF (simple alerte).",
    },
    {
        "code": "SOUS_TOTAUX_FAMILLES",
        "description": "Sous-totaux par famille et total matière cohérents.",
        "type": "prix", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "sous_totaux"},
        "seuil": None,
        "statut": "archive",
        "motif_changement": "Rejetée par l'utilisateur lors de la validation initiale.",
    },
    {
        "code": "DEDUCTIONS_VITRAGE",
        "description": "Déductions vitrage : fixe = cote − 76 mm ; vantail = largeur − 196 mm, hauteur − 186 mm.",
        "type": "etude", "famille": "vitrage", "gravite": "erreur",
        "condition": {"controle": "deductions_vitrage"},
        "seuil": None,
        "statut": "archive",
        "motif_changement": "Rejetée par l'utilisateur lors de la validation initiale.",
    },
]
