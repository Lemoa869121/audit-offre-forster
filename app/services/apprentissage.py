"""
Apprentissage : ce que deviennent les décisions de l'utilisateur sur les anomalies.

Principes (cahier des charges) :
- Validé  : anomalie confirmée. Règle existante -> confirmée (compteur). Observation de Claude ->
            nouvelle consigne « à signaler ». Article inconnu -> ajouté comme prix de référence.
- Rejeté  : faux positif. Le système PROPOSE (exception sur l'article, nouveau seuil, désactivation,
            consigne « ne pas signaler ») ; rien n'est modifié tant que l'utilisateur n'applique pas.
- Corrigé : l'utilisateur donne la bonne valeur. Si elle contredit une règle ou un prix existant,
            le conflit est affiché (« la règle dit X, votre correction dit Y ») et une proposition
            permet de trancher ; jamais d'écrasement silencieux.
- Toute modification de règle crée une NOUVELLE VERSION et archive l'ancienne (aucune suppression).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisHistory, Anomaly, Famille, Gravite, NatureAnomalie, PriceReference, Rule, Statut, StatutAnomalie, TypeRegle,
)
from app.services.comparaison import CONTROLES

CONTROLES_SANS_CODE = {"consigne_ia", "remise_tarif"}


class ErreurMetier(Exception):
    """Erreur à afficher telle quelle à l'utilisateur (code HTTP associé)."""

    def __init__(self, message: str, code_http: int = 400):
        super().__init__(message)
        self.code_http = code_http


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Date ISO avec fuseau : SQLite rend les dates sans fuseau, elles sont enregistrées en UTC."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def famille_enum(valeur: str | None) -> Famille:
    if not valeur or valeur == "global":
        return Famille.global_
    return Famille(valeur)


# --- Conversion vers dict (moteur de comparaison et réponses API) -----------------------

def regle_vers_dict(r: Rule) -> dict:
    return {
        "id": r.id, "code": r.code, "version": r.version, "description": r.description,
        "type": r.type.value, "famille": r.famille.value, "gravite": r.gravite.value,
        "condition": r.condition, "seuil": r.seuil, "statut": r.statut.value,
        "source_pdf": r.source_pdf, "motif_changement": r.motif_changement, "remplace_id": r.remplace_id,
        "date_creation": iso(r.date_creation),
    }


def prix_vers_dict(p: PriceReference) -> dict:
    return {
        "id": p.id, "type_prix": p.type_prix, "reference_produit": p.reference_produit, "designation": p.designation,
        "famille": p.famille.value, "prix_reference": p.prix_reference, "unite": p.unite,
        "conditionnement": p.conditionnement, "tarif_par": p.tarif_par, "taille_uv": p.taille_uv,
        "tolerance_pct": p.tolerance_pct, "statut": p.statut.value, "source": p.source,
        "date_maj": iso(p.date_maj),
    }


def anomalie_vers_dict(a: Anomaly) -> dict:
    return {
        "id": a.id, "analyse_id": a.analyse_id, "regle_id": a.regle_id, "code_regle": a.code_regle,
        "nature": a.nature.value, "type": a.type.value, "famille": a.famille.value, "gravite": a.gravite.value,
        "message": a.message, "reference_produit": a.reference_produit, "valeur_trouvee": a.valeur_trouvee,
        "valeur_attendue": a.valeur_attendue, "ecart_pct": a.ecart_pct, "texte_source": a.texte_source,
        "statut": a.statut.value, "valeur_corrigee": a.valeur_corrigee, "commentaire": a.commentaire,
        "date_decision": iso(a.date_decision),
    }


def regles_actives(db: Session) -> list[Rule]:
    return list(db.scalars(select(Rule).where(Rule.statut == Statut.actif).order_by(Rule.code)))


def prix_actifs(db: Session) -> list[PriceReference]:
    return list(db.scalars(select(PriceReference).where(PriceReference.statut == Statut.actif)))


def regle_active(db: Session, code: str) -> Rule:
    regle = db.scalar(select(Rule).where(Rule.code == code, Rule.statut == Statut.actif))
    if regle is None:
        raise ErreurMetier(f"Aucune règle active avec le code {code}.", 404)
    return regle


def statistiques_regles(db: Session) -> dict[str, dict]:
    """Nombre de confirmations / rejets / corrections par code de règle (toutes versions confondues)."""
    lignes = db.execute(
        select(Anomaly.code_regle, Anomaly.statut, func.count()).where(Anomaly.code_regle.is_not(None))
        .group_by(Anomaly.code_regle, Anomaly.statut)
    ).all()
    stats: dict[str, dict] = {}
    for code, statut, nombre in lignes:
        stats.setdefault(code, {s.value: 0 for s in StatutAnomalie})[statut.value] = nombre
    return stats


# --- Opérations sur les règles (toujours versionnées) -------------------------------------

def _verifier_condition(condition: dict) -> None:
    controle = (condition or {}).get("controle")
    if controle not in CONTROLES and controle not in CONTROLES_SANS_CODE:
        connus = ", ".join(sorted(set(CONTROLES) | CONTROLES_SANS_CODE))
        raise ErreurMetier(f"Contrôle « {controle} » inconnu. Contrôles disponibles : {connus}.")


def creer_regle(db: Session, donnees: dict, source: str | None = None) -> Rule:
    code = re.sub(r"[^A-Z0-9_]", "_", (donnees.get("code") or "").upper()).strip("_")
    if not code:
        raise ErreurMetier("Le code de la règle est obligatoire (ex. JOINT_BUTEE_PORTE).")
    if db.scalar(select(Rule).where(Rule.code == code)):
        raise ErreurMetier(f"Une règle {code} existe déjà : modifiez-la (nouvelle version) plutôt que d'en créer une autre.", 409)
    _verifier_condition(donnees.get("condition"))
    regle = Rule(
        code=code, version=1, description=donnees["description"], type=TypeRegle(donnees.get("type", "etude")),
        famille=famille_enum(donnees.get("famille")), gravite=Gravite(donnees.get("gravite", "erreur")),
        condition=donnees["condition"], seuil=donnees.get("seuil"), statut=Statut.actif,
        source_pdf=source or donnees.get("source_pdf"), motif_changement=donnees.get("motif_changement"),
    )
    db.add(regle)
    db.flush()
    return regle


def nouvelle_version(db: Session, code: str, modifications: dict, motif: str, statut: Statut = Statut.actif) -> Rule:
    """Archive la version active et crée la suivante avec les modifications (description, condition, seuil, gravite...)."""
    if not (motif or "").strip():
        raise ErreurMetier("Indiquez le motif du changement : il est conservé dans l'historique de la règle.")
    courante = regle_active(db, code)
    if "condition" in modifications:
        _verifier_condition(modifications["condition"])
    courante.statut = Statut.archive
    regle = Rule(
        code=courante.code, version=courante.version + 1,
        description=modifications.get("description", courante.description),
        type=TypeRegle(modifications["type"]) if "type" in modifications else courante.type,
        famille=famille_enum(modifications["famille"]) if "famille" in modifications else courante.famille,
        gravite=Gravite(modifications["gravite"]) if "gravite" in modifications else courante.gravite,
        condition=copy.deepcopy(modifications.get("condition", courante.condition)),
        seuil=modifications.get("seuil", courante.seuil),
        statut=statut, source_pdf=modifications.get("source_pdf", courante.source_pdf),
        motif_changement=motif.strip(), remplace_id=courante.id,
    )
    db.add(regle)
    db.flush()
    return regle


def desactiver_regle(db: Session, code: str, motif: str) -> Rule:
    """Désactivation manuelle : nouvelle version au statut archivé (la règle reste consultable)."""
    if not (motif or "").strip():
        raise ErreurMetier("Indiquez pourquoi vous désactivez cette règle : le motif est conservé dans l'historique.")
    return nouvelle_version(db, code, {}, f"Désactivée : {motif.strip()}", statut=Statut.archive)


def reactiver_regle(db: Session, code: str, motif: str) -> Rule:
    if db.scalar(select(Rule).where(Rule.code == code, Rule.statut == Statut.actif)):
        raise ErreurMetier(f"La règle {code} est déjà active.", 409)
    derniere = db.scalar(select(Rule).where(Rule.code == code).order_by(Rule.version.desc()))
    if derniere is None:
        raise ErreurMetier(f"Aucune règle avec le code {code}.", 404)
    if not (motif or "").strip():
        raise ErreurMetier("Indiquez le motif de la réactivation.")
    regle = Rule(
        code=code, version=derniere.version + 1, description=derniere.description, type=derniere.type,
        famille=derniere.famille, gravite=derniere.gravite, condition=copy.deepcopy(derniere.condition),
        seuil=derniere.seuil, statut=Statut.actif, source_pdf=derniere.source_pdf,
        motif_changement=f"Réactivée : {motif.strip()}", remplace_id=derniere.id,
    )
    db.add(regle)
    db.flush()
    return regle


# --- Opérations sur les prix (toujours versionnées) ---------------------------------------

def prix_net_actif(db: Session, reference: str) -> PriceReference | None:
    return db.scalar(select(PriceReference).where(
        PriceReference.type_prix == "net", PriceReference.reference_produit == reference,
        PriceReference.statut == Statut.actif))


def enregistrer_prix_net(db: Session, reference: str, prix: float, famille: str | None, unite: str | None,
                         source: str | None, designation: str | None = None) -> PriceReference:
    """Ajoute un prix net ; s'il en existe déjà un pour cet article, il est archivé (historique)."""
    if prix is None or prix <= 0:
        raise ErreurMetier("Le prix de référence doit être un nombre positif.")
    actuel = prix_net_actif(db, reference)
    if actuel:
        actuel.statut = Statut.archive
    nouveau = PriceReference(
        type_prix="net", reference_produit=reference, designation=designation or (actuel.designation if actuel else None),
        famille=famille_enum(famille) if famille not in (None, "offre", "vitrage") else (actuel.famille if actuel else Famille.global_),
        prix_reference=prix, unite=unite or (actuel.unite if actuel else "pce"),
        conditionnement=actuel.conditionnement if actuel else None, source=source,
    )
    db.add(nouveau)
    db.flush()
    return nouveau


# --- Décisions sur les anomalies -----------------------------------------------------------

def _proposition(action: str, libelle: str, **donnees) -> dict:
    return {"action": action, "libelle": libelle, "donnees": donnees}


def _consigne(code: str, texte: str, sens: str, famille: str, type_: str, source: str) -> dict:
    return {
        "code": code, "description": texte, "type": type_, "famille": famille, "gravite": "info",
        "condition": {"controle": "consigne_ia", "sens": sens, "texte": texte}, "seuil": None, "source_pdf": source,
    }


def _propositions_rejet(db: Session, anomalie: Anomaly, source: str) -> list[dict]:
    propositions = []
    motif = anomalie.commentaire or "faux positif"
    if anomalie.nature == NatureAnomalie.observation_ia:
        texte = f"Ne pas signaler ce type de remarque : « {anomalie.message} »" + (f" ({anomalie.commentaire})" if anomalie.commentaire else "")
        propositions.append(_proposition(
            "creer_regle", "Créer une consigne pour que Claude ne signale plus ce point",
            regle=_consigne(f"CONSIGNE_IGNORER_{anomalie.id}", texte, "ignorer", anomalie.famille.value, anomalie.type.value, source),
        ))
        return propositions
    if anomalie.nature == NatureAnomalie.proposition_prix or not anomalie.code_regle:
        return propositions  # rien à changer : le prix n'est simplement pas ajouté
    regle = db.scalar(select(Rule).where(Rule.code == anomalie.code_regle, Rule.statut == Statut.actif))
    if regle is None:
        return propositions
    if anomalie.reference_produit:
        condition = copy.deepcopy(regle.condition)
        exceptions = condition.setdefault("exceptions_references", [])
        if anomalie.reference_produit not in exceptions:
            exceptions.append(anomalie.reference_produit)
        propositions.append(_proposition(
            "nouvelle_version_regle",
            f"Ne plus appliquer {regle.code} à l'article {anomalie.reference_produit} (exception)",
            code=regle.code, modifications={"condition": condition},
            motif=f"Exception {anomalie.reference_produit} après rejet (analyse {anomalie.analyse_id}) : {motif}",
        ))
    if regle.seuil is not None:
        propositions.append(_proposition(
            "nouvelle_version_regle", f"Changer le seuil de {regle.code} (actuellement {regle.seuil:g})",
            code=regle.code, modifications={"seuil": None}, a_saisir="seuil",
            motif=f"Seuil modifié après rejet (analyse {anomalie.analyse_id}) : {motif}",
        ))
    propositions.append(_proposition(
        "desactiver_regle", f"Désactiver la règle {regle.code}", code=regle.code,
        motif=f"Rejetée (analyse {anomalie.analyse_id}) : {motif}",
    ))
    return propositions


def _propositions_correction(db: Session, anomalie: Anomaly, source: str) -> tuple[list[dict], str | None]:
    valeur = anomalie.valeur_corrigee
    if valeur is None:
        return [], None
    if anomalie.type == TypeRegle.prix and anomalie.reference_produit:
        actuel = prix_net_actif(db, anomalie.reference_produit)
        conflit = None
        if actuel and actuel.prix_reference != valeur:
            conflit = (f"Le prix de référence actuel de {anomalie.reference_produit} est {actuel.prix_reference:.2f} €, "
                       f"votre correction dit {valeur:.2f} € : lequel est correct ?")
        return [_proposition(
            "enregistrer_prix", f"Enregistrer {valeur:.2f} € comme prix de référence de {anomalie.reference_produit}",
            reference=anomalie.reference_produit, prix=valeur, famille=anomalie.famille.value, source=source,
        )], conflit
    regle = db.scalar(select(Rule).where(Rule.code == anomalie.code_regle, Rule.statut == Statut.actif)) if anomalie.code_regle else None
    if regle is not None and regle.seuil is not None:
        conflit = None
        if regle.seuil != valeur:
            conflit = (f"La règle {regle.code} dit {regle.seuil:g}, votre correction dit {valeur:g} : laquelle est correcte ? "
                       f"(« {regle.description} »)")
        return [_proposition(
            "nouvelle_version_regle", f"Passer le seuil de {regle.code} de {regle.seuil:g} à {valeur:g}",
            code=regle.code, modifications={"seuil": valeur},
            motif=f"Correction utilisateur (analyse {anomalie.analyse_id}) : {anomalie.commentaire or 'nouvelle valeur'}",
        )], conflit
    return [], None


def decider(db: Session, anomalie_id: int, decision: str, valeur_corrigee: float | None = None,
            commentaire: str | None = None) -> dict:
    """Enregistre la décision et renvoie ce qui a été appliqué, les propositions et un éventuel conflit."""
    anomalie = db.get(Anomaly, anomalie_id)
    if anomalie is None:
        raise ErreurMetier(f"Anomalie {anomalie_id} introuvable.", 404)
    try:
        statut = StatutAnomalie(decision)
    except ValueError:
        raise ErreurMetier("Décision attendue : valide, rejete ou corrige.") from None
    if statut == StatutAnomalie.corrige and valeur_corrigee is None:
        raise ErreurMetier("Pour « corrigé », indiquez la bonne valeur.")

    anomalie.statut, anomalie.valeur_corrigee = statut, valeur_corrigee
    anomalie.commentaire, anomalie.date_decision = commentaire, _maintenant()
    source = f"{anomalie.analyse.nom_pdf} (analyse {anomalie.analyse_id})"
    applique, propositions, conflit = [], [], None

    if statut == StatutAnomalie.valide:
        if anomalie.nature == NatureAnomalie.observation_ia:
            texte = (commentaire or f"Signaler ce type de point : « {anomalie.message} »").strip()
            regle = creer_regle(db, _consigne(f"CONSIGNE_SIGNALER_{anomalie.id}", texte, "signaler",
                                              anomalie.famille.value, anomalie.type.value, source))
            applique.append(f"Consigne {regle.code} créée : Claude signalera ce type de point.")
        elif anomalie.nature == NatureAnomalie.proposition_prix and anomalie.reference_produit and anomalie.valeur_trouvee:
            prix = enregistrer_prix_net(db, anomalie.reference_produit, anomalie.valeur_trouvee, anomalie.famille.value,
                                        None, source)
            applique.append(f"Prix de référence {prix.reference_produit} = {prix.prix_reference:.2f} € enregistré.")
        elif anomalie.code_regle:
            applique.append(f"Règle {anomalie.code_regle} confirmée.")
    elif statut == StatutAnomalie.rejete:
        propositions = _propositions_rejet(db, anomalie, source)
    else:
        propositions, conflit = _propositions_correction(db, anomalie, source)

    _maj_decision_analyse(db, anomalie.analyse)
    db.flush()
    return {"anomalie": anomalie_vers_dict(anomalie), "applique": applique, "propositions": propositions, "conflit": conflit}


def appliquer(db: Session, proposition: dict) -> str:
    """Applique une proposition renvoyée par decider() (action explicite de l'utilisateur)."""
    action, d = proposition.get("action"), proposition.get("donnees") or {}
    if action == "creer_regle":
        regle = creer_regle(db, d["regle"])
        return f"Règle {regle.code} créée."
    if action == "nouvelle_version_regle":
        modifications = dict(d.get("modifications") or {})
        if d.get("a_saisir") and modifications.get(d["a_saisir"]) is None:
            raise ErreurMetier(f"Indiquez la nouvelle valeur ({d['a_saisir']}).")
        regle = nouvelle_version(db, d["code"], modifications, d.get("motif", ""))
        return f"Règle {regle.code} : version {regle.version} créée, version précédente archivée."
    if action == "desactiver_regle":
        regle = desactiver_regle(db, d["code"], d.get("motif", "désactivation manuelle"))
        return f"Règle {regle.code} désactivée (version {regle.version})."
    if action == "enregistrer_prix":
        prix = enregistrer_prix_net(db, d["reference"], d["prix"], d.get("famille"), d.get("unite"), d.get("source"))
        return f"Prix de référence {prix.reference_produit} = {prix.prix_reference:.2f} € enregistré (ancien prix archivé s'il existait)."
    raise ErreurMetier(f"Action « {action} » inconnue.")


def _maj_decision_analyse(db: Session, analyse: AnalysisHistory) -> None:
    statuts = [a.statut for a in analyse.anomalies]
    analyse.decision_utilisateur = (
        "termine" if statuts and StatutAnomalie.en_attente not in statuts else "en_cours" if statuts else None
    )
