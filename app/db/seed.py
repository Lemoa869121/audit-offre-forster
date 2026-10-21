"""
Initialisation de la base avec les règles et prix validés.
Usage : python -m app.db.seed

Prix : fichier data/<FICHIER_PRIX>.json (défaut "Tarif FFR 062026"), sinon data/tarif.exemple.json.
Deux formats acceptés (voir app/services/import_tarif.lire_fichier_prix) :
- export brut de l'Excel (liste de lignes "Article" / "Desigfnation" / "Tarif 2026"), converti à la volée ;
- fichier converti {"nom", "articles", "prix_nets_observes"}.
Prix nets observés supplémentaires : data/prix_nets_observes.json (facultatif, hors git).

Idempotent :
- règle déjà présente (même code) -> inchangée ;
- prix déjà présent avec le même montant -> inchangé ;
- prix déjà présent avec un montant différent (nouveau tarif) -> l'ancien est ARCHIVÉ,
  le nouveau ajouté (historique conservé, jamais d'écrasement).
"""
import os
from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.models import Famille, Gravite, PriceReference, Rule, Statut, TypeRegle
from app.services.import_tarif import lire_fichier_prix
from app.services.regles_initiales import REGLES_INITIALES, SOURCE

DOSSIER_DATA = Path(__file__).resolve().parents[2] / "data"
FICHIER_PRIX = os.getenv("FICHIER_PRIX", "Tarif FFR 062026")
PREFIXE_INIT = "[init]"


def _famille(valeur: str) -> Famille:
    return Famille.global_ if valeur == "global" else Famille(valeur)


def charger_regles(db) -> dict:
    """
    Règle absente -> créée (version 1).
    Règle présente et identique -> inchangée.
    Règle présente mais modifiée dans regles_initiales.py -> version courante archivée,
    nouvelle version créée avec son motif (jamais d'écrasement).
    Les versions écrites ici ont un motif préfixé "[init]". Si la version courante vient de l'application
    (exception, nouveau seuil, désactivation...), elle n'est PAS remplacée : la décision de l'utilisateur prime,
    et la règle est listée dans "ignorees" pour arbitrage.
    """
    stats = {"ajoutees": 0, "nouvelles_versions": 0, "ignorees": []}
    for r in REGLES_INITIALES:
        courante = db.scalars(select(Rule).where(Rule.code == r["code"]).order_by(Rule.version.desc())).first()
        if courante and (courante.description, courante.condition, courante.seuil) == (r["description"], r["condition"], r["seuil"]):
            continue
        if courante and not (courante.motif_changement or "").startswith(PREFIXE_INIT):
            stats["ignorees"].append(r["code"])  # modifiée dans l'application : on ne touche pas
            continue
        if courante:
            courante.statut = Statut.archive
            stats["nouvelles_versions"] += 1
        else:
            stats["ajoutees"] += 1
        db.add(Rule(
            code=r["code"], version=(courante.version + 1) if courante else 1, description=r["description"],
            type=TypeRegle(r["type"]), famille=_famille(r["famille"]), gravite=Gravite(r["gravite"]),
            condition=r["condition"], seuil=r["seuil"], statut=Statut(r.get("statut", "actif")),
            motif_changement=f"{PREFIXE_INIT} {r.get('motif_changement') or 'règle initiale'}",
            source_pdf=r.get("source", SOURCE), remplace_id=courante.id if courante else None,
        ))
    return stats


def _lire_fichier_prix() -> tuple[list[dict], str]:
    return lire_fichier_prix(DOSSIER_DATA, FICHIER_PRIX)


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
        if actuel and type_prix == "net" and actuel.source != p.get("source"):
            stats["inchanges"] += 1  # prix net corrigé dans l'application : la décision de l'utilisateur prime
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
    if r["ignorees"]:
        print(f"Règles modifiées dans l'application, non remplacées par le code : {', '.join(r['ignorees'])}")
    print(f"Règles : {r['ajoutees']} ajoutée(s), {r['nouvelles_versions']} nouvelle(s) version(s). "
          f"Prix depuis {s['source']} : {s['ajoutes']} ajouté(s), "
          f"{s['mis_a_jour']} mis à jour (ancien archivé), {s['inchanges']} inchangé(s).")
