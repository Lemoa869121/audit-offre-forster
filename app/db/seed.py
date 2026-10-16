"""
Initialisation de la base avec les règles et prix validés.
Usage : python -m app.db.seed

Idempotent : une règle (par code) ou un prix (par référence) déjà présent n'est pas recréé.
Prix : data/prix_initiaux.json (réel, hors git) sinon data/prix_initiaux.exemple.json.
"""
import json
from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.models import Famille, Gravite, PriceReference, Rule, Statut, TypeRegle
from app.services.regles_initiales import REGLES_INITIALES, SOURCE

DOSSIER_DATA = Path(__file__).resolve().parents[2] / "data"


def _famille(valeur: str) -> Famille:
    return Famille.global_ if valeur == "global" else Famille(valeur)


def charger_regles(db) -> int:
    ajoutees = 0
    for r in REGLES_INITIALES:
        if db.scalar(select(Rule).where(Rule.code == r["code"])):
            continue
        db.add(Rule(
            code=r["code"], version=1, description=r["description"],
            type=TypeRegle(r["type"]), famille=_famille(r["famille"]), gravite=Gravite(r["gravite"]),
            condition=r["condition"], seuil=r["seuil"],
            statut=Statut(r.get("statut", "actif")), motif_changement=r.get("motif_changement"),
            source_pdf=SOURCE,
        ))
        ajoutees += 1
    return ajoutees


def charger_prix(db) -> tuple[int, str]:
    fichier = DOSSIER_DATA / "prix_initiaux.json"
    if not fichier.exists():
        fichier = DOSSIER_DATA / "prix_initiaux.exemple.json"
    ajoutes = 0
    for p in json.loads(fichier.read_text(encoding="utf-8")):
        if db.scalar(select(PriceReference).where(PriceReference.reference_produit == p["reference_produit"])):
            continue
        db.add(PriceReference(
            reference_produit=p["reference_produit"], designation=p.get("designation"),
            famille=_famille(p["famille"]), prix_reference=p["prix_reference"], unite=p["unite"],
            conditionnement=p.get("conditionnement"), tolerance_pct=p.get("tolerance_pct"),
            source=p.get("source"),
        ))
        ajoutes += 1
    return ajoutes, fichier.name


if __name__ == "__main__":
    init_db()
    with SessionLocal() as db:
        nb_regles = charger_regles(db)
        nb_prix, source = charger_prix(db)
        db.commit()
    print(f"{nb_regles} règle(s) et {nb_prix} prix ajoutés (prix depuis {source}).")
