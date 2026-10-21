"""
Tests de l'API de bout en bout : upload -> extraction (simulée) -> comparaison -> décisions -> règles versionnées.
Aucun appel à l'API Claude : l'extraction est remplacée par les fixtures JSON.
"""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from app import config
from app.db import seed
from app.db.database import SessionLocal, engine
from app.models import Base
from app.services import extraction
from app.services.apprentissage import enregistrer_prix_net

FIXTURES = Path(__file__).parent / "fixtures"
CR4 = json.loads((FIXTURES / "extraction_cr4_fictive.json").read_text(encoding="utf-8"))
STANDARD = json.loads((FIXTURES / "extraction_fictive.json").read_text(encoding="utf-8"))
PDF_FICTIF = (FIXTURES / "devis_fictif.pdf").read_bytes()


@pytest.fixture
def client():
    """Base vierge + règles initiales + quelques prix nets fictifs."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed.charger_regles(db)
        for ref, prix, famille in (("100.001 ZM", 30.0, "profile"), ("900001", 20.0, "ferrure"),
                                   ("901327", 4.39, "profile"), ("987009", 13.55, "ferrure")):
            enregistrer_prix_net(db, ref, prix, famille, "pce", "test")
        db.commit()
    with TestClient(main.app) as c:
        yield c


def _simuler(client, donnees, nom="test.json", forcer=True):
    reponse = client.post("/analyses/simulation", json={"nom": nom, "donnees": donnees, "forcer": forcer})
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _anomalie(analyse, code):
    return next(a for a in analyse["anomalies"] if a["code_regle"] == code)


def _decider(client, anomalie_id, decision, **champs):
    reponse = client.post(f"/anomalies/{anomalie_id}/decision", json={"decision": decision, **champs})
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _appliquer(client, proposition):
    reponse = client.post("/anomalies/appliquer", json={"proposition": proposition})
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["message"]


# --- Pipeline --------------------------------------------------------------------------------

def test_page_et_sante(client):
    assert "Audit des offres" in client.get("/").text
    assert client.get("/health").json()["statut"] == "ok"


def test_pipeline_simulation_cr4(client):
    analyse = _simuler(client, CR4)
    codes = {a["code_regle"] for a in analyse["anomalies"]}
    assert {"COHERENCE_DIN", "CONFIG_VS_PV", "HORS_LOT_FACTURE", "KIT_CR4", "ARTICLE_A_REMPLACER", "BARRES_PROFILES"} <= codes
    assert analyse["decision"] == "en_cours" and analyse["nb_en_attente"] == analyse["nb_anomalies"]
    assert analyse["anomalies"][0]["gravite"] == "erreur"  # erreurs en premier


def test_upload_pdf_extraction_simulee_et_couts(client, monkeypatch):
    def extraction_simulee(contenu, nom, consignes):
        assert contenu.startswith(b"%PDF") and consignes  # les consignes apprises sont bien transmises
        return extraction.ResultatExtraction(copy.deepcopy(CR4), 3, False, "claude-sonnet-5-5", 30000, 8000, 0.14)
    monkeypatch.setattr(extraction, "extraire_pdf", extraction_simulee)

    reponse = client.post("/analyses", files={"fichier": ("devis_fictif.pdf", PDF_FICTIF, "application/pdf")})
    assert reponse.status_code == 200, reponse.text
    analyse = reponse.json()
    assert analyse["cout_estime_usd"] == 0.14 and analyse["tokens_entree"] == 30000
    assert analyse["donnees_extraites"]["document"]["reference_affaire"] == "2026-TEST-CR4"

    # même PDF : refusé (évite un nouvel appel payant) sauf si « forcer »
    doublon = client.post("/analyses", files={"fichier": ("devis_fictif.pdf", PDF_FICTIF, "application/pdf")})
    assert doublon.status_code == 409 and "déjà été analysé" in doublon.json()["detail"]
    force = client.post("/analyses", data={"forcer": "true"}, files={"fichier": ("devis_fictif.pdf", PDF_FICTIF, "application/pdf")})
    assert force.status_code == 200

    stats = client.get("/statistiques").json()
    assert stats["nb_analyses_avec_api"] == 2 and stats["cout_total_usd"] == 0.28 and stats["cout_moyen_par_pdf_usd"] == 0.14


def test_fichier_non_pdf_refuse(client):
    reponse = client.post("/analyses", files={"fichier": ("devis.txt", b"pas un pdf", "text/plain")})
    assert reponse.status_code == 415


def test_cle_api_manquante_message_clair(client, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
    reponse = client.post("/analyses", files={"fichier": ("devis_fictif.pdf", PDF_FICTIF, "application/pdf")})
    assert reponse.status_code == 503 and "ANTHROPIC_API_KEY" in reponse.json()["detail"]


def test_filtre_familles(client):
    reponse = client.post("/analyses/simulation", json={"nom": "f.json", "donnees": CR4, "familles": ["joint"], "forcer": True})
    familles = {a["famille"] for a in reponse.json()["anomalies"]}
    assert familles <= {"joint", "global", "offre"}
    assert client.post("/analyses/simulation", json={"donnees": CR4, "familles": ["vitre"]}).status_code == 400


# --- Décisions et apprentissage ----------------------------------------------------------------

def test_rejet_propose_exception_puis_regle_versionnee(client):
    analyse = _simuler(client, CR4)
    anomalie = _anomalie(analyse, "ARTICLE_A_REMPLACER")
    resultat = _decider(client, anomalie["id"], "rejete", commentaire="975301 accepté sur ce chantier")
    assert resultat["anomalie"]["statut"] == "rejete" and resultat["applique"] == []  # rien de modifié en silence
    exception = next(p for p in resultat["propositions"] if "exception" in p["libelle"])
    assert "version 2" in _appliquer(client, exception)
    historique = client.get("/regles/ARTICLE_A_REMPLACER").json()["versions"]
    assert [(v["version"], v["statut"]) for v in historique] == [(1, "archive"), (2, "actif")]
    assert historique[1]["condition"]["exceptions_references"] == ["975301"]
    assert "ARTICLE_A_REMPLACER" not in {a["code_regle"] for a in _simuler(client, CR4)["anomalies"]}


def test_correction_qui_contredit_une_regle_est_signalee(client):
    anomalie = _anomalie(_simuler(client, CR4), "BARRES_PROFILES")  # seuil 5,9 m utiles
    resultat = _decider(client, anomalie["id"], "corrige", valeur_corrigee=6, commentaire="6 m utiles")
    assert "BARRES_PROFILES dit 5.9, votre correction dit 6" in resultat["conflit"]
    assert client.get("/regles/BARRES_PROFILES").json()["versions"][-1]["seuil"] == 5.9  # pas encore modifiée
    _appliquer(client, resultat["propositions"][0])
    assert client.get("/regles/BARRES_PROFILES").json()["versions"][-1]["seuil"] == 6


def test_valider_une_observation_cree_une_consigne(client):
    observation = next(a for a in _simuler(client, STANDARD)["anomalies"] if a["nature"] == "observation_ia")
    resultat = _decider(client, observation["id"], "valide", commentaire="Toujours vérifier ce point")
    assert "Consigne" in resultat["applique"][0]
    consignes = [r for r in client.get("/regles").json() if r["code"].startswith("CONSIGNE_SIGNALER_")]
    assert consignes[0]["condition"]["texte"] == "Toujours vérifier ce point"


def test_valider_un_nouveau_prix_l_enregistre(client):
    proposition = next(a for a in _simuler(client, STANDARD)["anomalies"] if a["nature"] == "proposition_prix")
    _decider(client, proposition["id"], "valide")
    prix = client.get(f"/prix?recherche={proposition['reference_produit']}&type_prix=net").json()
    assert prix[0]["prix_reference"] == proposition["valeur_trouvee"]


def test_corriger_un_prix_signale_le_conflit_et_archive_l_ancien(client):
    anomalie = _anomalie(_simuler(client, STANDARD), "ECART_PRIX_REF")  # 100.001 ZM à 36 € pour 30 € en base
    resultat = _decider(client, anomalie["id"], "corrige", valeur_corrigee=36)
    assert "actuel de 100.001 ZM est 30.00 €" in resultat["conflit"]
    _appliquer(client, resultat["propositions"][0])
    assert client.get("/prix?recherche=100.001&type_prix=net").json()[0]["prix_reference"] == 36
    assert "ECART_PRIX_REF" not in {a["code_regle"] for a in _simuler(client, STANDARD)["anomalies"]
                                     if a["nature"] == "ecart"}


def test_analyse_terminee_quand_tout_est_traite(client):
    analyse = _simuler(client, CR4)
    for a in analyse["anomalies"]:
        _decider(client, a["id"], "valide")
    assert client.get(f"/analyses/{analyse['id']}").json()["decision"] == "termine"


# --- Gestion des règles ------------------------------------------------------------------------------

def test_desactiver_puis_reactiver_une_regle(client):
    assert client.post("/regles/KIT_CR4/desactiver", json={"motif": ""}).status_code == 400  # motif obligatoire
    assert client.post("/regles/KIT_CR4/desactiver", json={"motif": "test"}).status_code == 200
    assert "KIT_CR4" not in {r["code"] for r in client.get("/regles").json()}
    assert "KIT_CR4" not in {a["code_regle"] for a in _simuler(client, CR4)["anomalies"]}
    assert client.post("/regles/KIT_CR4/reactiver", json={"motif": "erreur de manipulation"}).status_code == 200
    versions = client.get("/regles/KIT_CR4").json()["versions"]
    assert [v["statut"] for v in versions] == ["archive", "archive", "actif"]


def test_creer_regle_controle_inconnu_ou_doublon(client):
    regle = {"code": "TEST_X", "description": "test", "condition": {"controle": "inexistant"}}
    assert client.post("/regles", json=regle).status_code == 400
    regle["condition"] = {"controle": "consigne_ia", "sens": "signaler", "texte": "Vérifier les seuils"}
    assert client.post("/regles", json=regle).status_code == 200
    assert client.post("/regles", json=regle).status_code == 409


def test_modifier_regle_exige_un_motif(client):
    assert client.put("/regles/PAUMELLES_PAR_VANTAIL", json={"motif": "", "seuil": 4}).status_code == 400
    r = client.put("/regles/PAUMELLES_PAR_VANTAIL", json={"motif": "test", "seuil": 4}).json()
    assert r["version"] == 2 and r["seuil"] == 4


def test_reinitialisation_ne_remplace_pas_une_regle_modifiee(client):
    client.put("/regles/PAUMELLES_PAR_VANTAIL", json={"motif": "réglage utilisateur", "seuil": 5})
    with SessionLocal() as db:
        stats = seed.charger_regles(db)  # même règle différente dans le code
        db.commit()
    assert "PAUMELLES_PAR_VANTAIL" in stats["ignorees"]
    assert client.get("/regles/PAUMELLES_PAR_VANTAIL").json()["versions"][-1]["seuil"] == 5


def test_ajout_manuel_de_prix(client):
    assert client.post("/prix", json={"reference": "907350", "prix": 53.24, "famille": "ferrure"}).status_code == 200
    assert client.post("/prix", json={"reference": "907350", "prix": -1, "famille": "ferrure"}).status_code == 400
    assert client.get("/prix?recherche=907350").json()[0]["prix_reference"] == 53.24
