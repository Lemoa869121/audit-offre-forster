"""
Tests du moteur de comparaison (sans API ni base de données).
Lancer : pytest
La fixture extraction_fictive.json contient des erreurs volontaires (données fictives).
"""
import copy
import json
from pathlib import Path

from app.services.comparaison import comparer
from app.services.regles_initiales import REGLES_INITIALES

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "extraction_fictive.json").read_text(encoding="utf-8"))

REGLES_ACTIVES = [dict(r, id=i) for i, r in enumerate(REGLES_INITIALES, start=1) if r.get("statut", "actif") == "actif"]

PRIX = [
    {"reference_produit": "100.001 ZM", "prix_reference": 30.0},
    {"reference_produit": "900001", "prix_reference": 20.0},
    {"reference_produit": "901327", "prix_reference": 4.39},
    {"reference_produit": "987009", "prix_reference": 13.55},
    {"reference_produit": "907340", "prix_reference": 4.28},
    {"reference_produit": "905386_25", "prix_reference": 2.62},
]


def _regle(code):
    return next(r for r in REGLES_ACTIVES if r["code"] == code)


def _codes(anomalies, nature="ecart"):
    return sorted(a.code_regle for a in anomalies if a.nature == nature)


def _verifier(donnees, code):
    """Anomalies produites par une seule règle (hors observations IA de la fixture)."""
    anomalies, _ = comparer(donnees, [_regle(code)], PRIX)
    return [a for a in anomalies if a.nature != "observation_ia"]


def _ligne(reference, prix):
    return {"famille": "profile", "reference": reference, "designation": "test", "prix_unitaire": prix}


# --- Écarts de prix (exigence : 20 % détecté avec tolérance 15 %) ---------------------

def test_ecart_prix_20pct_detecte_avec_tolerance_15():
    donnees = {"analyse_matiere": {"lignes": [_ligne("100.001 ZM", 36.0)]}}
    anomalies, _ = comparer(donnees, [_regle("ECART_PRIX_REF")], PRIX, tolerance_globale_pct=15)
    assert len(anomalies) == 1
    assert anomalies[0].gravite == "alerte"
    assert anomalies[0].ecart_pct == 20.0


def test_ecart_prix_10pct_non_detecte():
    donnees = {"analyse_matiere": {"lignes": [_ligne("100.001 ZM", 33.0)]}}
    anomalies, _ = comparer(donnees, [_regle("ECART_PRIX_REF")], PRIX, tolerance_globale_pct=15)
    assert anomalies == []


def test_tolerance_par_article_prioritaire():
    prix = [{"reference_produit": "100.001 ZM", "prix_reference": 30.0, "tolerance_pct": 5}]
    donnees = {"analyse_matiere": {"lignes": [_ligne("100.001 ZM", 33.0)]}}  # +10 %
    anomalies, _ = comparer(donnees, [_regle("ECART_PRIX_REF")], prix, tolerance_globale_pct=15)
    assert len(anomalies) == 1


def test_reference_trouvee_sans_suffixe():
    """'905386_40' doit retrouver la référence '905386_25' par son code de base."""
    donnees = {"analyse_matiere": {"lignes": [_ligne("905386_40", 2.62)]}}
    anomalies = _verifier(donnees, "ECART_PRIX_REF")
    assert anomalies == []


def test_reference_inconnue_proposee_comme_prix():
    donnees = {"analyse_matiere": {"lignes": [_ligne("123456", 9.99)]}}
    anomalies = _verifier(donnees, "ECART_PRIX_REF")
    assert [a.nature for a in anomalies] == ["proposition_prix"]


# --- Contrôles unitaires ------------------------------------------------------------

def test_prix_nul():
    anomalies = _verifier(FIXTURE, "PRIX_NUL")
    assert [a.reference_produit for a in anomalies] == ["999999"]


def test_somme_positions_correcte_puis_fausse():
    assert _verifier(FIXTURE, "SOMME_POSITIONS") == []
    donnees = copy.deepcopy(FIXTURE)
    donnees["offre"]["total_ht"]["valeur"] = 2100.0
    assert len(_verifier(donnees, "SOMME_POSITIONS")) == 1


def test_paumelles_par_vantail():
    assert _verifier(FIXTURE, "PAUMELLES_PAR_VANTAIL") == []  # 6 = 2 vantaux x 3
    donnees = copy.deepcopy(FIXTURE)
    donnees["analyse_matiere"]["lignes"][2]["besoin"] = 4
    anomalies = _verifier(donnees, "PAUMELLES_PAR_VANTAIL")
    assert anomalies[0].valeur_trouvee == 4 and anomalies[0].valeur_attendue == 6


def test_axe_poignee_manquant():
    anomalies = _verifier(FIXTURE, "AXE_POIGNEE_PAR_VANTAIL")
    assert len(anomalies) == 1 and anomalies[0].valeur_attendue == 2


def test_parclose_vs_perimetre():
    assert _verifier(FIXTURE, "PARCLOSE_VS_PERIMETRE") == []
    donnees = copy.deepcopy(FIXTURE)
    donnees["analyse_matiere"]["lignes"][1]["besoin"] = 15.0  # -27 %
    assert len(_verifier(donnees, "PARCLOSE_VS_PERIMETRE")) == 1


def test_coherence_parclose_detecte_acier_vs_aluminium():
    anomalies = _verifier(FIXTURE, "COHERENCE_PARCLOSE")
    assert len(anomalies) == 1
    assert "acier" in anomalies[0].message and "aluminium" in anomalies[0].message


def test_total_offre_vs_matiere():
    anomalies = _verifier(FIXTURE, "TOTAL_OFFRE_VS_MATIERE")
    assert len(anomalies) == 1 and anomalies[0].gravite == "erreur"


def test_bloc_descriptif_manquant_sur_porte_seulement():
    anomalies = _verifier(FIXTURE, "BLOC_DESCRIPTIF_PORTE")
    assert len(anomalies) == 1 and "002" in anomalies[0].message  # 003 est un fixe : pas concerné


def test_chute_conditionnement_info():
    anomalies = _verifier(FIXTURE, "CHUTE_CONDITIONNEMENT")
    assert [(a.reference_produit, a.gravite) for a in anomalies] == [("988001", "info")]


# --- Pipeline complet sur la fixture -------------------------------------------------

def test_pipeline_complet_fixture():
    anomalies, indicateurs = comparer(FIXTURE, REGLES_ACTIVES, PRIX)
    assert _codes(anomalies) == sorted([
        "AXE_POIGNEE_PAR_VANTAIL", "BLOC_DESCRIPTIF_PORTE", "CHUTE_CONDITIONNEMENT",
        "COHERENCE_PARCLOSE", "ECART_PRIX_REF", "PRIX_NUL", "TOTAL_OFFRE_VS_MATIERE",
    ])
    assert len(_codes(anomalies, "proposition_prix")) == 1      # 988001 inconnu
    assert len([a for a in anomalies if a.nature == "observation_ia"]) == 1
    assert anomalies[0].gravite == "erreur"                       # tri : erreurs d'abord
    assert indicateurs["nb_vantaux"] == 2
    assert indicateurs["ratio_offre_sur_matiere"] == round(2000 / 1018.24, 4)


def test_regle_inconnue_et_consigne_ia_ignorees():
    regles = [{"code": "X", "type": "etude", "famille": "global", "condition": {"controle": "inexistant"}},
              _regle("CONSIGNE_PLAQUE_987081")]
    anomalies, _ = comparer({"observations": []}, regles, PRIX)
    assert anomalies == []


def test_filtre_famille():
    anomalies, _ = comparer(FIXTURE, REGLES_ACTIVES, PRIX, familles=["joint"])
    assert all(a.famille in {"joint", "global", "offre"} for a in anomalies)
    assert not any(a.reference_produit == "100.001 ZM" for a in anomalies)  # profilé filtré


# --- Tarif FFR (prix public remisé) ---------------------------------------------------

from app.services.comparaison import code_base, taille_uv  # noqa: E402
from app.services.import_tarif import convertir, lire_prix  # noqa: E402

TARIF = [
    {"type_prix": "tarif", "reference_produit": "531.731F.50.A", "designation": "531.731 x 1.50 unico 6000",
     "famille": "profile", "prix_reference": 45.26, "unite": "m"},
    {"type_prix": "tarif", "reference_produit": "531.731F.51.A", "designation": "531.731 x1.50 ZM unico 6000",
     "famille": "profile", "prix_reference": 47.90, "unite": "m"},
    {"type_prix": "tarif", "reference_produit": "907.662F.ND.A", "designation": "907662 paumelle à souder",
     "famille": "ferrure", "prix_reference": 62.83, "unite": "UV"},  # unité NON apprise : déduite
    {"type_prix": "tarif", "reference_produit": "905.386F.ND.A", "designation": "905386 joint de vitrage",
     "famille": "joint", "prix_reference": 3.08, "unite": "m", "tarif_par": "unite"},
    {"type_prix": "tarif", "reference_produit": "076.851F.62.B", "designation": "76.851 thermfix",
     "famille": "profile", "prix_reference": None, "unite": "m"},
]
REMISES = [_regle(c) for c in ("REMISE_PROFILE", "REMISE_FERRURE", "REMISE_ACCESSOIRE", "REMISE_JOINT")]


ECART_AVEC_TARIF = dict(_regle("ECART_PRIX_REF"), condition={"controle": "ecart_prix_reference", "comparer_tarif": True})


def _prix_tarif(ligne, regle=ECART_AVEC_TARIF):
    anomalies, _ = comparer({"analyse_matiere": {"lignes": [ligne]}}, [regle] + REMISES, TARIF)
    return anomalies


def test_par_defaut_tarif_sert_seulement_a_verifier_presence():
    """Remises pas encore validées : un prix très éloigné du tarif ne déclenche rien."""
    ligne = {"famille": "profile", "reference": "531.731 ZM", "designation": "UNICO", "prix_unitaire": 99.0}
    assert _prix_tarif(ligne, _regle("ECART_PRIX_REF")) == []


def test_par_defaut_article_absent_du_tarif_propose():
    ligne = {"famille": "accessoire", "reference": "123456", "designation": "inconnu", "prix_unitaire": 5.0}
    assert [a.nature for a in _prix_tarif(ligne, _regle("ECART_PRIX_REF"))] == ["proposition_prix"]


def test_prix_manquant_detecte():
    donnees = {"analyse_matiere": {"lignes": [
        {"famille": "joint", "reference": "905386_25", "designation": "joint", "prix_unitaire": None, "total": None, "a_verifier": True},
    ]}, "offre": {"positions": [{"numero": "001", "prix_unitaire": 100.0, "total": None}]}}
    messages = [a.message for a in _verifier(donnees, "PRIX_NUL")]
    assert messages == ["Position 001 : prix manquant.", "Article 905386_25 (joint) : prix manquant ou illisible."]


def test_code_base_devis_et_tarif():
    assert code_base("531.731 ZM") == code_base("531.731F.51.A") == "531731"
    assert code_base("905386_25") == code_base("905.386F.ND.A") == "905386"
    assert code_base("F15.1031.50.A") == "F151031"


def test_taille_uv():
    assert taille_uv({"conditionnement": "UV 25 m"}) == 25
    assert taille_uv({"quantite_texte": "6 UV á 2 pce (12)"}) == 2
    assert taille_uv({"conditionnement": "barre 6 m"}) is None


def test_profile_zm_conforme_avec_remise_35():
    # 47,90 x (1 - 35 %) = 31,135 -> 31,14 conforme
    assert _prix_tarif({"famille": "profile", "reference": "531.731 ZM", "designation": "UNICO", "prix_unitaire": 31.14}) == []


def test_profile_zm_trop_cher_detecte():
    a = _prix_tarif({"famille": "profile", "reference": "531.731 ZM", "designation": "UNICO", "prix_unitaire": 40.0})
    assert len(a) == 1 and a[0].gravite == "alerte" and a[0].valeur_attendue == 31.135


def test_variante_non_precisee_supposee_brute():
    a = _prix_tarif({"famille": "profile", "reference": "531.731", "designation": "UNICO", "prix_unitaire": 40.0})
    assert len(a) == 1 and "variante supposée" in a[0].message and a[0].valeur_attendue == round(45.26 * 0.65, 4)


def test_unite_de_vente_deduite():
    # 62,83 € par UV de 2 -> 31,415 €/pce -> remise 30 % -> 21,99 conforme
    ligne = {"famille": "ferrure", "reference": "907662", "designation": "paumelle", "prix_unitaire": 21.99,
             "conditionnement": "UV 2 pce"}
    assert _prix_tarif(ligne) == []


def test_joint_tarif_par_unite():
    # 3,08 €/m x 0,70 = 2,156 ; 2,62 est 21,5 % au-dessus -> alerte
    a = _prix_tarif({"famille": "joint", "reference": "905386_25", "designation": "joint", "prix_unitaire": 2.62,
                     "conditionnement": "UV 25 m"})
    assert len(a) == 1 and round(a[0].ecart_pct) == 22


def test_prix_net_observe_prioritaire_sur_tarif():
    net = [{"reference_produit": "905386_25", "prix_reference": 2.62}]
    ligne = {"famille": "joint", "reference": "905386_25", "designation": "joint", "prix_unitaire": 2.62}
    anomalies, _ = comparer({"analyse_matiere": {"lignes": [ligne]}}, [_regle("ECART_PRIX_REF")] + REMISES, TARIF + net)
    assert anomalies == []


def test_prix_sur_demande_signale_en_info():
    a = _prix_tarif({"famille": "profile", "reference": "076.851", "designation": "thermfix", "prix_unitaire": 99.0})
    assert len(a) == 1 and a[0].gravite == "info" and "sur demande" in a[0].message


def test_import_tarif():
    lignes = [
        {"Article": "531.731F.51.A", "Desigfnation": "531.731 x1.50 ZM unico 6000", "Tarif 2026": "47,90 €", "colonne_6": ""},
        {"Article": "531.731F.51.A", "Desigfnation": "531.731 x1.50 ZM unico 6000", "Tarif 2026": "47,90 €", "colonne_6": ""},
        {"Article": "988.001F.ND.A", "Desigfnation": "988001 bande d'étanchéité", "Tarif 2026": "1 068,72 €"},
        {"Article": "076.851F.62.B", "Desigfnation": "76.851 thermfix", "Tarif 2026": "Sur Demande"},
    ]
    articles, stats = convertir(lignes, "Tarif test")
    assert len(articles) == 3 and stats["doublons"] == 1 and stats["sur_demande"] == 1
    assert [a["famille"] for a in articles] == ["profile", "joint", "profile"]
    assert lire_prix("1 068,72 €") == 1068.72
