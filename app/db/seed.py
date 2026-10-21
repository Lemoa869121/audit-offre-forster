"""
Initialisation de la base avec les règles et prix validés.
Usage : python -m app.db.seed

Prix : fichier data/<FICHIER_PRIX>.json (réel, hors git, défaut "Tarif FFR 062026"),
sinon data/tarif.exemple.json (prix fictifs). Format :
    {"nom": "...", "articles": [...prix tarif...], "prix_nets_observes": [...prix nets...]}

Idempotent :
- règle déjà présente (même code) -> inchangée ;
- prix déjà présent avec le même montant -> inchangé ;
- prix déjà présent avec un montant différent (nouveau tarif) -> l'ancien est ARCHIVÉ,
  le nouveau ajouté (historique conservé, jamais d'écrasement).
"""
import json
import os
from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.models import Famille, Gravite, PriceReference, Rule, Statut, TypeRegle
from app.services.regles_initiales import REGLES_INITIALES, SOURCE

DOSSIER_DATA = Path(__file__).resolve().parents[2] / "data"
FICHIER_PRIX = os.getenv("FICHIER_PRIX", "Tarif FFR 062026")


def _famille(valeur: str) -> Famille:
    return Famille.global_ if valeur == "global" else Famille(valeur)


def charger_regles(db) -> dict:
    """
    Règle absente -> créée (version 1).
    Règle présente et identique -> inchangée.
    Règle présente mais modifiée dans regles_initiales.py -> version courante archivée,
    nouvelle version créée avec son motif (jamais d'écrasement).
    Une règle désactivée manuellement en base n'est pas réactivée.
    """
    stats = {"ajoutees": 0, "nouvelles_versions": 0}
    for r in REGLES_INITIALES:
        versions = db.scalars(select(Rule).where(Rule.code == r["code"]).order_by(Rule.version.desc())).all()
        courante = versions[0] if versions else None
        if courante and (courante.description, courante.condition, courante.seuil) == (r["description"], r["condition"], r["seuil"]):
            continue
        statut = Statut(r.get("statut", "actif"))
        if courante:
            if courante.statut == Statut.archive and statut == Statut.actif and not courante.motif_changement:
                continue  # désactivée à la main : on respecte ce choix
            courante.statut = Statut.archive
            stats["nouvelles_versions"] += 1
        else:
            stats["ajoutees"] += 1
        db.add(Rule(
            code=r["code"], version=(courante.version + 1) if courante else 1, description=r["description"],
            type=TypeRegle(r["type"]), famille=_famille(r["famille"]), gravite=Gravite(r["gravite"]),
            condition=r["condition"], seuil=r["seuil"], statut=statut,
            motif_changement=r.get("motif_changement"), source_pdf=r.get("source", SOURCE),
            remplace_id=courante.id if courante else None,
        ))
    return stats


def _lire_fichier_prix() -> tuple[list[dict], str]:
    fichier = DOSSIER_DATA / f"{FICHIER_PRIX}.json"
    if not fichier.exists():
        fichier = DOSSIER_DATA / "tarif.exemple.json"
    contenu = json.loads(fichier.read_text(encoding="utf-8"))
    prix = contenu.get("articles", []) + [dict(p, type_prix="net") for p in contenu.get("prix_nets_observes", [])]
    return prix, fichier.name


def charger_prix(db) -> dict:
    prix, source = _lire_fichier_prix()
    existants = {
        (p.type_prix, p.reference_produit): p
        for p in db.scalars(select(PriceReference).where(PriceReference.statut == Statut.actif))
    }
    stats = {"ajoutes": 0, "mis_a_jour": 0, "inchanges": 0, "source": source}
    for p in prix:
        type_prix = p.get("type_prix", "net")
        actuel = existants.get((type_prix, p["reference_produit"]))
        if actuel and actuel.prix_reference == p["prix_reference"]:
            stats["inchanges"] += 1
            continue
        if actuel:  # nouveau montant : on archive l'ancien
            actuel.statut = Statut.archive
            stats["mis_a_jour"] += 1
        else:
            stats["ajoutes"] += 1
        db.add(PriceReference(
            type_prix=type_prix, reference_produit=p["reference_produit"], designation=p.get("designation"),
            famille=_famille(p["famille"]), prix_reference=p["prix_reference"], unite=p["unite"],
            conditionnement=p.get("conditionnement"), tarif_par=p.get("tarif_par"), taille_uv=p.get("taille_uv"),
            tolerance_pct=p.get("tolerance_pct"), source=p.get("source"),
        ))
    return stats


if __name__ == "__main__":
    init_db()
    with SessionLocal() as db:
        r = charger_regles(db)
        s = charger_prix(db)
        db.commit()
    print(f"Règles : {r['ajoutees']} ajoutée(s), {r['nouvelles_versions']} nouvelle(s) version(s). "
          f"Prix depuis {s['source']} : {s['ajoutes']} ajouté(s), "
          f"{s['mis_a_jour']} mis à jour (ancien archivé), {s['inchanges']} inchangé(s).")
