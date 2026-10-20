"""
Conversion d'un tarif fournisseur (export JSON de l'Excel FFR) vers le fichier de prix de l'application.

Usage :
    python -m app.services.import_tarif <export_tarif.json> --nom "Tarif FFR 062026"

Produit data/<nom>.json : les articles du tarif (type "tarif") + les prix nets déjà observés
dans les devis validés (type "net", conservés depuis le fichier précédent s'il existe).
Charger ensuite en base avec : python -m app.db.seed

Colonnes attendues dans l'export : "Article", "Desigfnation" (ou "Designation"), "Tarif 2026"
(ou toute colonne commençant par "Tarif"). Les colonnes vides "colonne_N" sont ignorées.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DOSSIER_DATA = Path(__file__).resolve().parents[2] / "data"

# Classement par famille (indicatif : lors d'une analyse, c'est la famille de la ligne du devis
# qui détermine la remise appliquée).
_JOINT = re.compile(r"joint|étanchéité|etancheite|recouvrement|epdm|butyl|\bseal", re.IGNORECASE)
_FERRURE = re.compile(
    r"paumelle|gâche|gache|serrure|ferme-porte|crémone|cremone|cylindre|verrou|charni|pivot|attache|"
    r"béquille|bequille|têtière|tetiere|hinge|lock", re.IGNORECASE)


def deviner_famille(article: str, designation: str) -> str:
    if _JOINT.search(designation):
        return "joint"
    if _FERRURE.search(designation):
        return "ferrure"
    if article[:1] in "0123456":  # codes profilés FFR : 0xx à 6xx
        return "profile"
    return "accessoire"


def lire_prix(texte) -> float | None:
    """'1 234,56 €' -> 1234.56 ; 'Sur Demande' / '' -> None."""
    brut = re.sub(r"[€\s  ]", "", str(texte or "")).replace(",", ".")
    try:
        return float(brut)
    except ValueError:
        return None


def convertir(lignes: list[dict], nom: str) -> tuple[list[dict], dict]:
    """Renvoie (articles, statistiques). Les doublons identiques sont fusionnés."""
    articles: dict[str, dict] = {}
    stats = {"lignes": len(lignes), "sur_demande": 0, "doublons": 0, "conflits": []}
    for ligne in lignes:
        article = str(ligne.get("Article") or "").strip()
        if not article:
            continue
        designation = str(ligne.get("Desigfnation") or ligne.get("Designation") or "").strip()
        colonne_prix = next((k for k in ligne if k.lower().startswith("tarif")), None)
        prix = lire_prix(ligne.get(colonne_prix)) if colonne_prix else None
        if prix is None:
            stats["sur_demande"] += 1
        entree = {
            "type_prix": "tarif", "reference_produit": article, "designation": designation,
            "famille": deviner_famille(article, designation), "prix_reference": prix,
            "unite": "m" if deviner_famille(article, designation) == "profile" else "UV",
            "source": nom,
        }
        if article in articles:
            stats["doublons"] += 1
            if articles[article]["prix_reference"] != prix:
                stats["conflits"].append(article)  # même article, prix différents : on garde le premier
            continue
        articles[article] = entree
    return list(articles.values()), stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="Export JSON du tarif")
    parser.add_argument("--nom", required=True, help='Nom du tarif, ex. "Tarif FFR 062026"')
    parser.add_argument("--nets", type=Path, help="Fichier dont reprendre les prix nets observés (défaut : data/<nom>.json existant)")
    args = parser.parse_args()

    articles, stats = convertir(json.loads(args.export.read_text(encoding="utf-8")), args.nom)
    sortie = DOSSIER_DATA / f"{args.nom}.json"
    source_nets = args.nets or sortie
    nets = []
    if source_nets.exists():
        precedent = json.loads(source_nets.read_text(encoding="utf-8"))
        nets = precedent.get("prix_nets_observes", []) if isinstance(precedent, dict) else precedent

    sortie.write_text(json.dumps(
        {"nom": args.nom, "articles": articles, "prix_nets_observes": nets},
        ensure_ascii=False, indent=1,
    ), encoding="utf-8")
    print(f"{len(articles)} articles ({stats['sur_demande']} sur demande, {stats['doublons']} doublons fusionnés, "
          f"{len(stats['conflits'])} conflit(s)) + {len(nets)} prix nets -> {sortie}")


if __name__ == "__main__":
    main()
