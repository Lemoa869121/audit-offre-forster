"""
Règles issues du premier cycle d'apprentissage (devis 2026-XAG-01076, validé le 16/10/2026).

Fichier de données pur (aucune dépendance base) : utilisé par le script
d'initialisation (app/db/seed.py) et par les tests.

Les règles rejetées sont conservées avec statut "archive" et leur motif :
c'est l'historique qui explique pourquoi elles n'existent plus.
"""

SOURCE = "2026-XAG-01076 (validation du 16/10/2026)"
SOURCE_2 = "2026-XAG-00525 (validation du 21/10/2026)"
SOURCE_3 = "2026-XAG-00525 offre modifiée (validation du 21/10/2026)"

CLASSES_SECURITE = ["CR4", "FB6"]
MOTIF_TUBE_RR = r"^RR\d+/20/2$"  # tube acier RR <profondeur>/20/2, toujours fourni par le métallier

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
        "description": ("Chaque article doit avoir un prix : aucun prix unitaire ni total manquant ou à zéro (offre et analyse matière). "
                        "0 € accepté pour un article hors lot ; article hors tarif à 0 € non listé hors lot : simple rappel."),
        "type": "prix", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "prix_nul", "motifs_hors_lot": [MOTIF_TUBE_RR]},
        "source": SOURCE_2,
        "motif_changement": "B2/B15 : un article hors lot peut être à 0 €. C3 : article hors tarif à 0 € non listé hors lot -> simple rappel.",
        "seuil": None,
    },
    {
        "code": "ECART_PRIX_REF",
        "description": ("Alerte si un prix unitaire s'écarte du prix net observé au-delà de la tolérance. "
                        "Comparaison au tarif FFR remisé désactivée pour l'instant (comparer_tarif = false) : "
                        "le tarif sert à vérifier que l'article existe et a un prix."),
        "type": "prix", "famille": "global", "gravite": "alerte",
        "condition": {"controle": "ecart_prix_reference", "comparer_tarif": False},
        "seuil": None,  # None = tolérance globale (TOLERANCE_PRIX_PCT, 15 % par défaut)
    },
    # Remises sur le tarif FFR, déduites du devis 2026-XAG-01076 (contrôle "remise_tarif" :
    # paramètre lu par ECART_PRIX_REF, pas un contrôle en soi). Seuil = remise en %.
    # Non utilisées tant que ECART_PRIX_REF a comparer_tarif = false (remises à valider plus tard).
    {
        "code": "REMISE_PROFILE",
        "description": "Remise sur le tarif FFR pour les profilés (35 % constaté sur 531/533 ZM).",
        "type": "prix", "famille": "profile", "gravite": "info",
        "condition": {"controle": "remise_tarif"}, "seuil": 35,
    },
    {
        "code": "REMISE_FERRURE",
        "description": "Remise sur le tarif FFR pour les ferrures (30 % constaté).",
        "type": "prix", "famille": "ferrure", "gravite": "info",
        "condition": {"controle": "remise_tarif"}, "seuil": 30,
    },
    {
        "code": "REMISE_ACCESSOIRE",
        "description": "Remise sur le tarif FFR pour les accessoires (30 % constaté).",
        "type": "prix", "famille": "accessoire", "gravite": "info",
        "condition": {"controle": "remise_tarif"}, "seuil": 30,
    },
    {
        "code": "REMISE_JOINT",
        "description": "Remise sur le tarif FFR pour les joints (30 % constaté sur 988001/988051, À CONFIRMER pour les joints EPDM).",
        "type": "prix", "famille": "joint", "gravite": "info",
        "condition": {"controle": "remise_tarif"}, "seuil": 30,
    },
    {
        "code": "PAUMELLES_PAR_VANTAIL",
        "description": "3 paumelles par vantail (4 par vantail sur une porte CR4/FB6).",
        "type": "etude", "famille": "ferrure", "gravite": "erreur",
        "condition": {"controle": "quantite_par_vantail", "references": ["907662"], "mots_cles": ["paumelle"],
                      "classements": CLASSES_SECURITE, "seuil_classe": 4},
        "source": SOURCE_2,
        "motif_changement": "B3 : 4 paumelles par vantail est la règle pour une porte CR4/FB6.",
        "seuil": 3,
    },
    {
        "code": "GACHE_PAR_VANTAIL",
        "description": "1 gâche 987009 par vantail (portes sans serrure ISEO).",
        "type": "etude", "famille": "ferrure", "gravite": "erreur",
        "condition": {"controle": "quantite_par_vantail", "references": ["987009"], "exclure_si_ferrure": "ISEO"},
        "source": SOURCE_2,
        "motif_changement": "B4 : la règle vaut pour la gâche 987009 ; les serrures ISEO ont leur propre règle (GACHES_ISEO).",
        "seuil": 1,
    },
    {
        "code": "AXE_POIGNEE_PAR_VANTAIL",
        "description": "1 axe (carré) de poignée par vantail (hors CR4/FB6 : carré fourni par le métallier).",
        "type": "etude", "famille": "accessoire", "gravite": "erreur",
        "condition": {"controle": "quantite_par_vantail", "references": ["907340"], "mots_cles": ["handle shaft", "carré de poignée"],
                      "exclure_classements": CLASSES_SECURITE},
        "source": SOURCE_2,
        "motif_changement": "B5 : en CR4/FB6 le carré est fourni par le métallier.",
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
        "condition": {"controle": "chute_conditionnement", "exclure_familles": ["profile"]},
        "source": SOURCE_2,
        "motif_changement": "B12 : chutes des profilés calculées par le logiciel (barres de 6 m), contrôlées par BARRES_PROFILES.",
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
    # --- Règles apprises sur le devis 2026-XAG-00525 (porte CR4 / FB6) -------------------
    {
        "code": "GACHES_ISEO",
        "description": "Serrure ISEO : toujours 3 gâches ISEO (1 centrale, 1 haute, 1 basse) par serrure.",
        "type": "etude", "famille": "ferrure", "gravite": "erreur",
        "condition": {"controle": "gaches_par_serrure", "mot_serrure": "ISEO"},
        "seuil": 3, "source": SOURCE_2,
    },
    {
        "code": "COHERENCE_DIN",
        "description": "Serrures et gâches dans le même sens DIN que la porte.",
        "type": "etude", "famille": "ferrure", "gravite": "erreur",
        "condition": {"controle": "coherence_din", "familles": ["ferrure"]},
        "seuil": None, "source": SOURCE_2,
    },
    {
        "code": "CONFIG_VS_PV",
        "description": "Le texte du bloc descriptif (rapport d'essai) doit décrire la porte chiffrée : nombre de vantaux, sens d'ouverture.",
        "type": "etude", "famille": "offre", "gravite": "erreur",
        "condition": {"controle": "configuration_vs_pv"},
        "seuil": None, "source": SOURCE_2,
    },
    {
        "code": "HORS_LOT_FACTURE",
        "description": "Un élément listé « Fourniture hors lot » ne doit pas être facturé dans l'analyse matière.",
        "type": "etude", "famille": "offre", "gravite": "erreur",
        "condition": {"controle": "hors_lot_facture"},
        "seuil": None, "source": SOURCE_2,
    },
    {
        "code": "KIT_CR4",
        "description": "Porte CR4 : plat usiné FFR.102, bande TESA FFR.103, renfort FB4 983302, plaque RC 987081, béquilles 907350 et tube acier RR**/20/2.",
        "type": "etude", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "kit_classement", "classements": ["CR4"], "articles": [
            {"libelle": "plat acier usiné CR4", "references": ["FFR.102"]},
            {"libelle": "bande adhésive TESA ACX+", "references": ["FFR.103"]},
            {"libelle": "renfort FB4", "references": ["983302"]},
            {"libelle": "plaque de fixation RC", "references": ["987081"]},
            {"libelle": "béquilles", "references": ["907350"]},
            {"libelle": "tube acier RR**/20/2 (fourni par le métallier)", "motifs": [MOTIF_TUBE_RR]},
        ]},
        "seuil": None, "source": SOURCE_2,
    },
    {
        "code": "RENFORT_FB",
        "description": "Renfort 983302 = somme des besoins des profilés UNICO × 2 en FB6 (renforts doublés), × 1 en FB4.",
        "type": "etude", "famille": "profile", "gravite": "erreur",
        "condition": {"controle": "renfort_fb", "renfort": ["983302"], "mot_profile": "UNICO",
                      "facteurs": {"FB6": 2}, "facteur_defaut": 1},
        "seuil": 10, "source": SOURCE_2,  # tolérance en % (chutes)
    },
    {
        "code": "ARTICLE_A_REMPLACER",
        "description": "Articles à ne plus chiffrer, avec leur remplaçant.",
        "type": "etude", "famille": "global", "gravite": "erreur",
        "condition": {"controle": "article_a_remplacer", "remplacements": [
            {"reference": "975301", "remplacant": "985301", "motif": "joint de butée de porte"},
        ]},
        "seuil": None, "source": SOURCE_2,
    },
    {
        "code": "DISPONIBILITE",
        "description": "Signaler les articles non tenus en stock ou sur demande (délai), sauf articles hors lot.",
        "type": "etude", "famille": "global", "gravite": "info",
        "condition": {"controle": "disponibilite", "motifs_hors_lot": [MOTIF_TUBE_RR]},
        "seuil": None, "source": SOURCE_2,
    },
    {
        "code": "BARRES_PROFILES",
        "description": "Profilés vendus par barres de 6 m dont 5,90 m utilisables : nombre de barres suffisant pour le besoin.",
        "type": "etude", "famille": "profile", "gravite": "erreur",
        "condition": {"controle": "barres_profiles"},
        "seuil": 5.9, "source": SOURCE_2,
    },
    {
        "code": "CONSIGNE_VITRAGE_CR4_38",
        "description": "Le rapport d'essai CR4/FB6 autorise un vitrage de 38 mm, même si le texte mentionne un panneau de 57 mm.",
        "type": "etude", "famille": "vitrage", "gravite": "info",
        "condition": {
            "controle": "consigne_ia", "sens": "ignorer",
            "texte": ("Porte CR4/FB6 : un vitrage de 38 mm est autorisé par le rapport d'essai même si le texte "
                      "indique « panneau épaisseur 57 mm » : ne pas signaler cet écart."),
        },
        "seuil": None, "source": SOURCE_2,
    },
    # --- Règles apprises sur l'offre modifiée 2026-XAG-00525 ------------------------------
    {
        "code": "LISTE_VS_MATIERE",
        "description": "Tout article cité dans « Ferrures de fenêtre » ou « Article complémentaire » doit figurer dans l'analyse matière, sauf s'il est hors lot.",
        "type": "etude", "famille": "offre", "gravite": "erreur",
        "condition": {"controle": "liste_vs_matiere"},
        "seuil": None, "source": SOURCE_3,
    },
    {
        "code": "SERRURE_EXIT_ANTIPANIQUE",
        "description": "Porte antipanique : serrure ISEO en version « Exit ».",
        "type": "etude", "famille": "ferrure", "gravite": "erreur",
        "condition": {"controle": "serrure_antipanique", "mot_serrure": "ISEO"},
        "seuil": None, "source": SOURCE_3,
    },
    {
        "code": "CONSIGNE_EXIT_TEXTE_GENERAL",
        "description": "La mention « Exit » des rapports de conformité ne concerne que les portes antipanique (texte souvent général).",
        "type": "etude", "famille": "ferrure", "gravite": "info",
        "condition": {
            "controle": "consigne_ia", "sens": "ignorer",
            "texte": ("Les serrures « Exit » citées par les rapports de conformité ne sont exigées que pour une porte "
                      "antipanique ; le texte est souvent général : ne pas signaler une serrure non Exit sur une porte non antipanique."),
        },
        "seuil": None, "source": SOURCE_3,
    },
    {
        "code": "CONSIGNE_TEXTE_VS_CHIFFRAGE",
        "description": "Signaler les incohérences entre le texte descriptif et les articles chiffrés (l'erreur est le plus souvent dans le texte).",
        "type": "etude", "famille": "offre", "gravite": "info",
        "condition": {
            "controle": "consigne_ia", "sens": "signaler",
            "texte": ("Compare le texte descriptif (Inclus dans l'offre, Quincailleries, Configurations) aux articles chiffrés et "
                      "signale chaque incohérence dans observations (gravite \"alerte\"), en précisant que c'est le texte qui est "
                      "probablement à corriger (ex. crémone « haut + bas » annoncée, seule la tringle basse chiffrée)."),
        },
        "seuil": None, "source": SOURCE_3,
    },
    {
        "code": "HAUTEUR_MAX_PV",
        "description": "La hauteur du châssis ne doit pas dépasser la hauteur maximale du rapport d'essai.",
        "type": "etude", "famille": "offre", "gravite": "erreur",
        "condition": {"controle": "hauteur_max_pv"},
        "seuil": None, "source": SOURCE_3,
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
