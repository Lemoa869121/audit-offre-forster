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
