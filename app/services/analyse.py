"""
Orchestration d'une analyse : PDF -> extraction (Claude) -> comparaison (règles + prix en base) -> enregistrement.

Une analyse peut aussi partir d'un JSON déjà extrait (« simulation ») : utile pour tester le pipeline
sans appel API, ou rejouer une extraction après avoir ajouté des règles.
"""
from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import TOLERANCE_PRIX_PCT
from app.models import AnalysisHistory, Anomaly, Gravite, NatureAnomalie, TypeRegle
from app.services import extraction
from app.services.apprentissage import (
    ErreurMetier, anomalie_vers_dict, iso, famille_enum, prix_actifs, prix_vers_dict, regle_vers_dict, regles_actives,
)
from app.services.comparaison import comparer

logger = logging.getLogger(__name__)


def consignes_ia(regles: list[dict]) -> list[str]:
    """Textes des règles « consigne_ia » actives, ajoutés au prompt d'extraction."""
    return [r["condition"]["texte"] for r in regles
            if (r.get("condition") or {}).get("controle") == "consigne_ia" and r["condition"].get("texte")]


def compter_a_verifier(objet) -> int:
    if isinstance(objet, dict):
        return int(objet.get("a_verifier") is True) + sum(compter_a_verifier(v) for v in objet.values())
    if isinstance(objet, list):
        return sum(compter_a_verifier(v) for v in objet)
    return 0


def analyse_existante(db: Session, empreinte: str) -> AnalysisHistory | None:
    return db.scalar(select(AnalysisHistory).where(AnalysisHistory.hash_pdf == empreinte).order_by(AnalysisHistory.id.desc()))


def executer_analyse(db: Session, nom_fichier: str, contenu_pdf: bytes | None = None, donnees: dict | None = None,
                     familles: list[str] | None = None, forcer: bool = False) -> AnalysisHistory:
    """
    contenu_pdf : PDF à extraire via Claude ; donnees : extraction déjà faite (simulation).
    familles    : filtre métier facultatif (profile, ferrure, accessoire, joint).
    forcer      : réanalyser même si ce fichier a déjà été analysé (sinon erreur 409, pour éviter un coût API inutile).
    """
    brut = contenu_pdf if contenu_pdf is not None else json.dumps(donnees, sort_keys=True).encode()
    empreinte = hashlib.sha256(brut).hexdigest()
    existante = analyse_existante(db, empreinte)
    if existante and not forcer:
        raise ErreurMetier(
            f"Ce fichier a déjà été analysé (analyse {existante.id} du {existante.date_analyse:%d/%m/%Y}). "
            "Cochez « réanalyser » pour relancer l'extraction (nouvel appel API).", 409)

    regles = [regle_vers_dict(r) for r in regles_actives(db)]
    prix = [prix_vers_dict(p) for p in prix_actifs(db)]

    analyse = AnalysisHistory(nom_pdf=nom_fichier, hash_pdf=empreinte)
    if contenu_pdf is not None:
        resultat = extraction.extraire_pdf(contenu_pdf, nom_fichier, consignes_ia(regles))
        donnees = resultat.donnees
        analyse.modele, analyse.pdf_scanne = resultat.modele, resultat.pdf_scanne
        analyse.tokens_entree, analyse.tokens_sortie = resultat.tokens_entree, resultat.tokens_sortie
        analyse.cout_estime_usd = resultat.cout_usd
    else:
        analyse.modele = "simulation (sans appel API)"

    anomalies, indicateurs = comparer(donnees, regles, prix, TOLERANCE_PRIX_PCT, familles)
    analyse.reference_affaire = ((donnees.get("document") or {}).get("reference_affaire") or None)
    analyse.donnees_extraites = {"extraction": donnees, "indicateurs": indicateurs, "familles": familles}
    analyse.anomalies_detectees = [a.to_dict() for a in anomalies]  # instantané au moment de l'analyse
    for a in anomalies:
        analyse.anomalies.append(Anomaly(
            regle_id=a.regle_id, code_regle=a.code_regle, nature=NatureAnomalie(a.nature), type=TypeRegle(a.type),
            famille=famille_enum(a.famille), gravite=Gravite(a.gravite), message=a.message,
            reference_produit=a.reference_produit, valeur_trouvee=a.valeur_trouvee, valeur_attendue=a.valeur_attendue,
            ecart_pct=a.ecart_pct, texte_source=a.texte_source,
        ))
    analyse.decision_utilisateur = "en_cours" if anomalies else "termine"
    db.add(analyse)
    db.flush()
    logger.info("Analyse %s '%s' : %d anomalie(s), %d règle(s) actives, coût≈%.4f $",
                analyse.id, nom_fichier, len(anomalies), len(regles), analyse.cout_estime_usd)
    return analyse


def resume_analyse(analyse: AnalysisHistory) -> dict:
    statuts = [a.statut.value for a in analyse.anomalies]
    return {
        "id": analyse.id, "nom_pdf": analyse.nom_pdf, "reference_affaire": analyse.reference_affaire,
        "date_analyse": iso(analyse.date_analyse),
        "modele": analyse.modele, "pdf_scanne": analyse.pdf_scanne,
        "tokens_entree": analyse.tokens_entree, "tokens_sortie": analyse.tokens_sortie,
        "cout_estime_usd": analyse.cout_estime_usd, "decision": analyse.decision_utilisateur,
        "nb_anomalies": len(statuts), "nb_en_attente": statuts.count("en_attente"),
        "nb_erreurs": sum(a.gravite == Gravite.erreur for a in analyse.anomalies),
    }


def detail_analyse(analyse: AnalysisHistory, avec_donnees: bool = False) -> dict:
    donnees = analyse.donnees_extraites or {}
    detail = resume_analyse(analyse) | {
        "indicateurs": donnees.get("indicateurs"),
        "nb_donnees_a_verifier": compter_a_verifier(donnees.get("extraction")),
        "anomalies": [anomalie_vers_dict(a) for a in sorted(
            analyse.anomalies, key=lambda a: ({"erreur": 0, "alerte": 1, "info": 2}[a.gravite.value], a.id))],
    }
    if avec_donnees:
        detail["donnees_extraites"] = donnees.get("extraction")
    return detail
