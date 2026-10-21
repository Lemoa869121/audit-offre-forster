"""
Routes FastAPI. Documentation interactive : http://127.0.0.1:8000/docs

Analyses   : POST /analyses (PDF), POST /analyses/simulation (JSON déjà extrait), GET /analyses, GET /analyses/{id}
Anomalies  : POST /anomalies/{id}/decision, POST /anomalies/appliquer
Règles     : GET /regles, GET /regles/{code}, POST /regles, PUT /regles/{code},
             POST /regles/{code}/desactiver, POST /regles/{code}/reactiver
Prix       : GET /prix, POST /prix
Suivi      : GET /statistiques
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models import AnalysisHistory, Anomaly, PriceReference, Rule, Statut
from app.services import analyse as service_analyse
from app.services import apprentissage as appr

router = APIRouter()

FAMILLES = {"profile", "ferrure", "accessoire", "joint"}
TAILLE_MAX_PDF = 32 * 1024 * 1024  # limite de requête de l'API Claude


def _familles(texte: str | None) -> list[str] | None:
    valeurs = [f.strip().lower() for f in (texte or "").split(",") if f.strip()]
    inconnues = set(valeurs) - FAMILLES
    if inconnues:
        raise appr.ErreurMetier(f"Famille(s) inconnue(s) : {', '.join(sorted(inconnues))}. Valeurs possibles : {', '.join(sorted(FAMILLES))}.")
    return valeurs or None


def _analyse(db: Session, analyse_id: int) -> AnalysisHistory:
    analyse = db.get(AnalysisHistory, analyse_id)
    if analyse is None:
        raise appr.ErreurMetier(f"Analyse {analyse_id} introuvable.", 404)
    return analyse


# --- Analyses ----------------------------------------------------------------------------

@router.post("/analyses", tags=["Analyses"], summary="Analyser un PDF (extraction Claude + comparaison aux règles)")
async def analyser_pdf(
    fichier: UploadFile = File(..., description="Offre PDF"),
    familles: str = Form("", description="Filtre facultatif, ex. « profile,joint » (vide = tout)"),
    forcer: bool = Form(False, description="Réanalyser un PDF déjà analysé (nouvel appel API)"),
    db: Session = Depends(get_db),
):
    contenu = await fichier.read()
    if not contenu:
        raise appr.ErreurMetier("Le fichier est vide.")
    if len(contenu) > TAILLE_MAX_PDF:
        raise appr.ErreurMetier("PDF trop volumineux (32 Mo maximum par envoi à l'API Claude).", 413)
    if not contenu.startswith(b"%PDF"):
        raise appr.ErreurMetier("Ce fichier n'est pas un PDF.", 415)
    analyse = service_analyse.executer_analyse(db, fichier.filename or "document.pdf", contenu_pdf=contenu,
                                               familles=_familles(familles), forcer=forcer)
    db.commit()
    return service_analyse.detail_analyse(analyse, avec_donnees=True)


class Simulation(BaseModel):
    nom: str = Field("simulation.json", description="Nom affiché dans l'historique")
    donnees: dict[str, Any] = Field(..., description="JSON au format de l'extraction (voir tests/fixtures)")
    familles: list[str] | None = None
    forcer: bool = False


@router.post("/analyses/simulation", tags=["Analyses"], summary="Analyser un JSON déjà extrait (sans appel API)")
def analyser_simulation(corps: Simulation, db: Session = Depends(get_db)):
    analyse = service_analyse.executer_analyse(db, corps.nom, donnees=corps.donnees,
                                               familles=_familles(",".join(corps.familles or [])), forcer=corps.forcer)
    db.commit()
    return service_analyse.detail_analyse(analyse, avec_donnees=True)


@router.get("/analyses", tags=["Analyses"], summary="Historique des analyses")
def lister_analyses(limite: int = Query(50, le=500), db: Session = Depends(get_db)):
    analyses = db.scalars(select(AnalysisHistory).order_by(AnalysisHistory.id.desc()).limit(limite))
    return [service_analyse.resume_analyse(a) for a in analyses]


@router.get("/analyses/{analyse_id}", tags=["Analyses"], summary="Détail d'une analyse et de ses anomalies")
def detail_analyse(analyse_id: int, avec_donnees: bool = False, db: Session = Depends(get_db)):
    return service_analyse.detail_analyse(_analyse(db, analyse_id), avec_donnees=avec_donnees)


# --- Décisions sur les anomalies -----------------------------------------------------------

class Decision(BaseModel):
    decision: Literal["valide", "rejete", "corrige"]
    valeur_corrigee: float | None = Field(None, description="Obligatoire pour « corrige »")
    commentaire: str | None = None


@router.post("/anomalies/{anomalie_id}/decision", tags=["Anomalies"],
             summary="Valider, rejeter ou corriger une anomalie (renvoie propositions et conflits)")
def decider(anomalie_id: int, corps: Decision, db: Session = Depends(get_db)):
    resultat = appr.decider(db, anomalie_id, corps.decision, corps.valeur_corrigee, corps.commentaire)
    db.commit()
    return resultat


class Application(BaseModel):
    proposition: dict[str, Any] = Field(..., description="Une proposition renvoyée par /decision (champ seuil complété si demandé)")


@router.post("/anomalies/appliquer", tags=["Anomalies"], summary="Appliquer une proposition (modification explicite)")
def appliquer(corps: Application, db: Session = Depends(get_db)):
    message = appr.appliquer(db, corps.proposition)
    db.commit()
    return {"message": message}


# --- Règles ---------------------------------------------------------------------------------

@router.get("/regles", tags=["Règles"], summary="Lister les règles (actives par défaut) avec leurs statistiques")
def lister_regles(statut: Literal["actif", "archive", "tous"] = "actif", type: str | None = None,
                  famille: str | None = None, db: Session = Depends(get_db)):
    requete = select(Rule).order_by(Rule.code, Rule.version)
    if statut != "tous":
        requete = requete.where(Rule.statut == Statut(statut))
    stats = appr.statistiques_regles(db)
    regles = [appr.regle_vers_dict(r) | {"decisions": stats.get(r.code, {})} for r in db.scalars(requete)]
    return [r for r in regles if (not type or r["type"] == type) and (not famille or r["famille"] == famille)]


@router.get("/regles/{code}", tags=["Règles"], summary="Historique complet des versions d'une règle")
def historique_regle(code: str, db: Session = Depends(get_db)):
    versions = list(db.scalars(select(Rule).where(Rule.code == code.upper()).order_by(Rule.version)))
    if not versions:
        raise appr.ErreurMetier(f"Aucune règle avec le code {code}.", 404)
    return {"code": versions[0].code, "versions": [appr.regle_vers_dict(v) for v in versions],
            "decisions": appr.statistiques_regles(db).get(versions[0].code, {})}


class NouvelleRegle(BaseModel):
    code: str = Field(..., examples=["JOINT_BUTEE_PORTE"])
    description: str
    type: Literal["etude", "prix"] = "etude"
    famille: str = "global"
    gravite: Literal["info", "alerte", "erreur"] = "erreur"
    condition: dict[str, Any] = Field(..., examples=[{"controle": "quantite_par_vantail", "references": ["907662"]}])
    seuil: float | None = None
    source_pdf: str | None = None


@router.post("/regles", tags=["Règles"], summary="Créer une règle")
def creer_regle(corps: NouvelleRegle, db: Session = Depends(get_db)):
    regle = appr.creer_regle(db, corps.model_dump(), corps.source_pdf or "saisie manuelle")
    db.commit()
    return appr.regle_vers_dict(regle)


class ModificationRegle(BaseModel):
    motif: str = Field(..., description="Pourquoi la règle change (conservé dans l'historique)")
    description: str | None = None
    gravite: Literal["info", "alerte", "erreur"] | None = None
    condition: dict[str, Any] | None = None
    seuil: float | None = None


@router.put("/regles/{code}", tags=["Règles"], summary="Modifier une règle (crée une nouvelle version)")
def modifier_regle(code: str, corps: ModificationRegle, db: Session = Depends(get_db)):
    modifications = corps.model_dump(exclude={"motif"}, exclude_unset=True)
    regle = appr.nouvelle_version(db, code.upper(), modifications, corps.motif)
    db.commit()
    return appr.regle_vers_dict(regle)


class Motif(BaseModel):
    motif: str


@router.post("/regles/{code}/desactiver", tags=["Règles"], summary="Désactiver une règle (droit à l'erreur)")
def desactiver_regle(code: str, corps: Motif, db: Session = Depends(get_db)):
    regle = appr.desactiver_regle(db, code.upper(), corps.motif)
    db.commit()
    return appr.regle_vers_dict(regle)


@router.post("/regles/{code}/reactiver", tags=["Règles"], summary="Réactiver une règle désactivée")
def reactiver_regle(code: str, corps: Motif, db: Session = Depends(get_db)):
    regle = appr.reactiver_regle(db, code.upper(), corps.motif)
    db.commit()
    return appr.regle_vers_dict(regle)


# --- Prix -----------------------------------------------------------------------------------

@router.get("/prix", tags=["Prix"], summary="Rechercher un prix de référence (net ou tarif)")
def chercher_prix(recherche: str = Query("", description="Référence ou mot de la désignation"),
                  type_prix: Literal["net", "tarif", "tous"] = "tous", limite: int = Query(50, le=500),
                  db: Session = Depends(get_db)):
    requete = select(PriceReference).where(PriceReference.statut == Statut.actif)
    if type_prix != "tous":
        requete = requete.where(PriceReference.type_prix == type_prix)
    if recherche:
        motif = f"%{recherche}%"
        requete = requete.where(or_(PriceReference.reference_produit.ilike(motif), PriceReference.designation.ilike(motif)))
    return [appr.prix_vers_dict(p) for p in db.scalars(requete.order_by(PriceReference.reference_produit).limit(limite))]


class NouveauPrix(BaseModel):
    reference: str = Field(..., examples=["531.731 ZM"])
    prix: float = Field(..., description="Prix net HT unitaire")
    famille: Literal["profile", "ferrure", "accessoire", "joint"]
    unite: str = Field("pce", examples=["m", "pce"])
    designation: str | None = None
    source: str = "saisie manuelle"


@router.post("/prix", tags=["Prix"], summary="Ajouter ou remplacer un prix net de référence (l'ancien est archivé)")
def ajouter_prix(corps: NouveauPrix, db: Session = Depends(get_db)):
    prix = appr.enregistrer_prix_net(db, corps.reference, corps.prix, corps.famille, corps.unite, corps.source, corps.designation)
    db.commit()
    return appr.prix_vers_dict(prix)


# --- Suivi ------------------------------------------------------------------------------------

@router.get("/statistiques", tags=["Suivi"], summary="Coûts API, analyses et décisions")
def statistiques(db: Session = Depends(get_db)):
    nb, cout, t_in, t_out = db.execute(select(
        func.count(AnalysisHistory.id), func.coalesce(func.sum(AnalysisHistory.cout_estime_usd), 0),
        func.coalesce(func.sum(AnalysisHistory.tokens_entree), 0), func.coalesce(func.sum(AnalysisHistory.tokens_sortie), 0),
    )).one()
    nb_api = db.scalar(select(func.count(AnalysisHistory.id)).where(AnalysisHistory.tokens_entree > 0)) or 0
    decisions = dict(db.execute(select(Anomaly.statut, func.count()).group_by(Anomaly.statut)).all())
    return {
        "nb_analyses": nb, "nb_analyses_avec_api": nb_api,
        "cout_total_usd": round(cout, 4), "cout_moyen_par_pdf_usd": round(cout / nb_api, 4) if nb_api else None,
        "tokens_entree": t_in, "tokens_sortie": t_out,
        "anomalies": {s.value: n for s, n in decisions.items()},
        "regles_actives": db.scalar(select(func.count(Rule.id)).where(Rule.statut == Statut.actif)),
        "prix_actifs": db.scalar(select(func.count(PriceReference.id)).where(PriceReference.statut == Statut.actif)),
    }
