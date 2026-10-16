"""
Moteur de comparaison : données extraites d'un PDF + règles actives + prix de référence
-> liste d'anomalies.

Choix : les contrôles sont du code Python déterministe (pas d'appel IA ici).
- Gratuit, instantané, testable, et le même PDF donne toujours le même résultat.
- Chaque règle en base pointe vers un contrôle via condition["controle"] ;
  son seuil et ses paramètres (références, mots-clés) viennent de la base,
  donc on ajuste une règle sans toucher au code.
- Ce module ne dépend pas de la base : il reçoit des dict, ce qui le rend
  simple à tester (tests/test_comparaison.py).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)


@dataclass
class AnomalieDetectee:
    nature: str            # ecart | observation_ia | proposition_prix
    type: str              # etude | prix
    famille: str
    gravite: str           # info | alerte | erreur
    message: str
    code_regle: str | None = None
    regle_id: int | None = None
    reference_produit: str | None = None
    valeur_trouvee: float | None = None
    valeur_attendue: float | None = None
    ecart_pct: float | None = None
    texte_source: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Contexte:
    prix_references: dict[str, dict]   # clé = référence normalisée
    tolerance_globale_pct: float


# --- Utilitaires ---------------------------------------------------------------

def normaliser_ref(ref: str | None) -> str:
    """'531.731  zm' -> '531.731 ZM'"""
    return " ".join((ref or "").upper().split())


def ref_de_base(ref: str | None) -> str:
    """Code article sans suffixe : '531.731 ZM' -> '531.731', '905386_25' -> '905386'."""
    return normaliser_ref(ref).replace("_", " ").split(" ")[0] if ref else ""


def trouver_prix_reference(ref: str | None, ctx: Contexte) -> dict | None:
    """Correspondance exacte d'abord, puis sur le code de base."""
    exact = ctx.prix_references.get(normaliser_ref(ref))
    if exact:
        return exact
    base = ref_de_base(ref)
    for cle, prix in ctx.prix_references.items():
        if ref_de_base(cle) == base:
            return prix
    return None


def _lignes_matiere(donnees: dict) -> list[dict]:
    return (donnees.get("analyse_matiere") or {}).get("lignes") or []


def _positions(donnees: dict) -> list[dict]:
    return (donnees.get("offre") or {}).get("positions") or []


def _valeur(bloc: dict | None) -> float | None:
    return bloc.get("valeur") if isinstance(bloc, dict) else None


def nombre_vantaux(donnees: dict) -> int:
    return int(sum((p.get("quantite") or 1) * (p.get("nb_vantaux") or 0) for p in _positions(donnees)))


def lignes_correspondantes(donnees: dict, condition: dict) -> list[dict]:
    """Lignes matière visées par une règle : par code article OU par mot-clé dans la désignation."""
    refs = {ref_de_base(r) for r in condition.get("references", [])}
    mots = [m.lower() for m in condition.get("mots_cles", [])]
    resultat = []
    for ligne in _lignes_matiere(donnees):
        designation = (ligne.get("designation") or "").lower()
        if ref_de_base(ligne.get("reference")) in refs or any(m in designation for m in mots):
            resultat.append(ligne)
    return resultat


def _quantite_besoin(ligne: dict) -> float | None:
    """Besoin réel (valeur entre parenthèses) si connu, sinon quantité commandée."""
    return ligne.get("besoin") if ligne.get("besoin") is not None else ligne.get("quantite_commandee")


def _materiau(texte: str | None) -> str | None:
    t = (texte or "").lower()
    if "alu" in t:
        return "aluminium"
    if "acier" in t or "steel" in t:
        return "acier"
    if "inox" in t:
        return "inox"
    return None


def _anomalie(regle: dict, message: str, **champs) -> AnomalieDetectee:
    return AnomalieDetectee(
        nature="ecart",
        type=regle["type"],
        famille=champs.pop("famille", regle["famille"]),
        gravite=regle.get("gravite", "erreur"),
        message=message,
        code_regle=regle.get("code"),
        regle_id=regle.get("id"),
        **champs,
    )


def _pct(trouve: float, attendu: float) -> float | None:
    return round((trouve - attendu) / attendu * 100, 2) if attendu else None


# --- Contrôles (un par valeur de condition["controle"]) -------------------------

def ctrl_somme_positions(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    total_offre = (donnees.get("offre") or {}).get("total_ht")
    total = _valeur(total_offre)
    totaux = [p.get("total") for p in _positions(donnees)]
    if total is None or not totaux or any(t is None for t in totaux):
        return []
    somme = round(sum(totaux), 2)
    tolerance = regle.get("seuil") or 0.05
    if abs(somme - total) > tolerance:
        return [_anomalie(
            regle,
            f"Somme des positions ({somme:.2f} €) différente du total HT de l'offre ({total:.2f} €).",
            valeur_trouvee=somme, valeur_attendue=total, ecart_pct=_pct(somme, total),
            texte_source=total_offre.get("texte_source"),
        )]
    return []


def ctrl_prix_nul(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    anomalies = []
    for p in _positions(donnees):
        if p.get("prix_unitaire") == 0 or p.get("total") == 0:
            anomalies.append(_anomalie(
                regle, f"Position {p.get('numero')} : prix à zéro.",
                famille="offre", valeur_trouvee=0, texte_source=p.get("texte_source"),
            ))
    for ligne in _lignes_matiere(donnees):
        if ligne.get("prix_unitaire") == 0 or ligne.get("total") == 0:
            anomalies.append(_anomalie(
                regle, f"Article {ligne.get('reference')} ({ligne.get('designation')}) : prix à zéro.",
                famille=ligne.get("famille", regle["famille"]), reference_produit=ligne.get("reference"),
                valeur_trouvee=0, texte_source=ligne.get("texte_source"),
            ))
    return anomalies


def ctrl_ecart_prix_reference(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        prix = ligne.get("prix_unitaire")
        if not prix:  # None (illisible) ou 0 (traité par PRIX_NUL)
            continue
        ref = trouver_prix_reference(ligne.get("reference"), ctx)
        if ref is None:
            # Apprentissage des prix : référence inconnue -> proposition à valider
            anomalies.append(AnomalieDetectee(
                nature="proposition_prix", type="prix", famille=ligne.get("famille", "global"), gravite="info",
                message=(f"Article {ligne.get('reference')} ({ligne.get('designation')}) inconnu de la base : "
                         f"ajouter {prix:.2f} € / {ligne.get('unite_prix') or '?'} comme prix de référence ?"),
                code_regle=regle.get("code"), regle_id=regle.get("id"),
                reference_produit=ligne.get("reference"), valeur_trouvee=prix,
                texte_source=ligne.get("texte_source"),
            ))
            continue
        tolerance = ref.get("tolerance_pct") or regle.get("seuil") or ctx.tolerance_globale_pct
        ecart = _pct(prix, ref["prix_reference"])
        if ecart is not None and abs(ecart) > tolerance:
            sens = "au-dessus" if ecart > 0 else "en dessous"
            anomalies.append(_anomalie(
                regle,
                (f"Article {ligne.get('reference')} : {prix:.2f} € soit {abs(ecart):.1f} % {sens} "
                 f"du prix de référence ({ref['prix_reference']:.2f} €, tolérance {tolerance:g} %)."),
                famille=ligne.get("famille", regle["famille"]), reference_produit=ligne.get("reference"),
                valeur_trouvee=prix, valeur_attendue=ref["prix_reference"], ecart_pct=ecart,
                texte_source=ligne.get("texte_source"),
            ))
    return anomalies


def ctrl_quantite_par_vantail(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    vantaux = nombre_vantaux(donnees)
    if vantaux == 0:
        return []
    par_vantail = regle.get("seuil") or 1
    attendu = par_vantail * vantaux
    lignes = lignes_correspondantes(donnees, regle["condition"])
    if not lignes:
        return [_anomalie(
            regle, f"{regle['description']} Article absent de l'analyse matière ({vantaux} vantail(s), {attendu:g} attendu(s)).",
            valeur_trouvee=0, valeur_attendue=attendu,
        )]
    quantites = [_quantite_besoin(l) for l in lignes]
    if any(q is None for q in quantites):
        return []
    trouve = sum(quantites)
    if trouve != attendu:
        return [_anomalie(
            regle,
            f"{regle['description']} Trouvé {trouve:g}, attendu {attendu:g} ({vantaux} vantail(s) × {par_vantail:g}).",
            reference_produit=lignes[0].get("reference"),
            valeur_trouvee=trouve, valeur_attendue=attendu, ecart_pct=_pct(trouve, attendu),
            texte_source=" | ".join(l.get("texte_source") or "" for l in lignes),
        )]
    return []


def perimetre_total_vitrages(donnees: dict) -> float | None:
    vitrages = donnees.get("vitrages") or {}
    if vitrages.get("somme_perimetre_m") is not None:
        return vitrages["somme_perimetre_m"]
    lignes = vitrages.get("lignes") or []
    if not lignes or any(l.get("perimetre_m") is None for l in lignes):
        return None
    return round(sum(l["perimetre_m"] * (l.get("quantite") or 1) for l in lignes), 2)


def ctrl_longueur_vs_perimetre_vitrage(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    perimetre = perimetre_total_vitrages(donnees)
    if not perimetre:
        return []
    tolerance = regle.get("seuil") or 5
    anomalies = []
    for ligne in lignes_correspondantes(donnees, regle["condition"]):
        besoin = _quantite_besoin(ligne)
        if besoin is None:
            continue
        ecart = _pct(besoin, perimetre)
        if ecart is not None and abs(ecart) > tolerance:
            anomalies.append(_anomalie(
                regle,
                (f"Article {ligne.get('reference')} : besoin {besoin:g} m pour un périmètre de vitrage de "
                 f"{perimetre:g} m (écart {ecart:+.1f} %, tolérance ± {tolerance:g} %)."),
                famille=ligne.get("famille", regle["famille"]), reference_produit=ligne.get("reference"),
                valeur_trouvee=besoin, valeur_attendue=perimetre, ecart_pct=ecart,
                texte_source=ligne.get("texte_source"),
            ))
    return anomalies


def ctrl_coherence_materiau_parclose(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    sources: list[tuple[str, str, str | None]] = []  # (où, matériau, texte_source)
    for p in _positions(donnees):
        for champ, libelle in (("materiau_parclose_configuration", "configuration"),
                               ("materiau_parclose_description", "description châssis")):
            m = _materiau(p.get(champ))
            if m:
                sources.append((f"pos. {p.get('numero')} ({libelle})", m, p.get("texte_source")))
    for ligne in lignes_correspondantes(donnees, {"mots_cles": ["parclose"]}):
        m = _materiau(ligne.get("materiau")) or _materiau(ligne.get("designation"))
        if m:
            sources.append((f"analyse matière {ligne.get('reference')}", m, ligne.get("texte_source")))

    materiaux = {m for _, m, _ in sources}
    if len(materiaux) <= 1:
        return []
    detail = " ; ".join(f"{ou} = {m}" for ou, m, _ in sources)
    return [_anomalie(
        regle,
        f"Matériau de parclose incohérent ({' / '.join(sorted(materiaux))}) : {detail}.",
        texte_source=" | ".join(sorted({t for _, _, t in sources if t})),
    )]


def ctrl_total_offre_vs_matiere(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    bloc_offre = (donnees.get("offre") or {}).get("total_ht")
    bloc_matiere = (donnees.get("analyse_matiere") or {}).get("total")
    total_offre, total_matiere = _valeur(bloc_offre), _valeur(bloc_matiere)
    if total_offre is None or total_matiere is None:
        return []
    tolerance = regle.get("seuil") or 1.0
    ecart = round(total_offre - total_matiere, 2)
    if abs(ecart) > tolerance:
        return [_anomalie(
            regle,
            (f"Total offre de prix ({total_offre:.2f} €) ≠ total analyse matière ({total_matiere:.2f} €) : "
             f"écart {ecart:+.2f} € ({_pct(total_offre, total_matiere):+.1f} %)."),
            valeur_trouvee=total_offre, valeur_attendue=total_matiere, ecart_pct=_pct(total_offre, total_matiere),
            texte_source=" | ".join(filter(None, [bloc_offre.get("texte_source"), bloc_matiere.get("texte_source")])),
        )]
    return []


def ctrl_bloc_descriptif_porte(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    return [
        _anomalie(
            regle,
            f"Position {p.get('numero')} ({p.get('designation')}) : porte sans bloc descriptif (type, configurations, inclus, hors gamme).",
            texte_source=p.get("texte_source"),
        )
        for p in _positions(donnees)
        if (p.get("nb_vantaux") or 0) > 0 and not p.get("a_bloc_descriptif")
    ]


def ctrl_chute_conditionnement(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    seuil = regle.get("seuil") or 35
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        commande, besoin = ligne.get("quantite_commandee"), ligne.get("besoin")
        if not commande or besoin is None:
            continue
        chute = round((commande - besoin) / commande * 100, 1)
        if chute > seuil:
            anomalies.append(_anomalie(
                regle,
                (f"Article {ligne.get('reference')} : {commande:g} commandé(s) pour un besoin de {besoin:g} "
                 f"({chute:g} % de chute, conditionnement {ligne.get('conditionnement') or '?'})."),
                famille=ligne.get("famille", regle["famille"]), reference_produit=ligne.get("reference"),
                valeur_trouvee=commande, valeur_attendue=besoin, ecart_pct=chute,
                texte_source=ligne.get("texte_source"),
            ))
    return anomalies


CONTROLES: dict[str, Callable[[dict, dict, Contexte], list[AnomalieDetectee]]] = {
    "somme_positions": ctrl_somme_positions,
    "prix_nul": ctrl_prix_nul,
    "ecart_prix_reference": ctrl_ecart_prix_reference,
    "quantite_par_vantail": ctrl_quantite_par_vantail,
    "longueur_vs_perimetre_vitrage": ctrl_longueur_vs_perimetre_vitrage,
    "coherence_materiau_parclose": ctrl_coherence_materiau_parclose,
    "total_offre_vs_matiere": ctrl_total_offre_vs_matiere,
    "bloc_descriptif_porte": ctrl_bloc_descriptif_porte,
    "chute_conditionnement": ctrl_chute_conditionnement,
}


# --- Indicateurs (conservés pour plus tard, sans règle associée) -----------------

def calculer_indicateurs(donnees: dict) -> dict:
    """€/m² par position et par typologie, ratio offre / matière."""
    positions = []
    for p in _positions(donnees):
        l, h, prix = p.get("largeur_mm"), p.get("hauteur_mm"), p.get("prix_unitaire")
        if not (l and h and prix):
            continue
        vantaux, fixes = p.get("nb_vantaux") or 0, p.get("nb_fixes") or 0
        typologie = "porte" if vantaux and not fixes else "fixe" if fixes and not vantaux else "ensemble fixe + porte"
        surface = l * h / 1e6
        positions.append({
            "numero": p.get("numero"), "typologie": typologie,
            "surface_m2": round(surface, 3), "prix_m2": round(prix / surface, 2),
        })
    total_offre = _valeur((donnees.get("offre") or {}).get("total_ht"))
    total_matiere = _valeur((donnees.get("analyse_matiere") or {}).get("total"))
    return {
        "prix_m2_par_position": positions,
        "ratio_offre_sur_matiere": round(total_offre / total_matiere, 4) if total_offre and total_matiere else None,
        "nb_vantaux": nombre_vantaux(donnees),
        "perimetre_vitrage_m": perimetre_total_vitrages(donnees),
    }


# --- Point d'entrée ---------------------------------------------------------------

def comparer(
    donnees: dict,
    regles: list[dict],
    prix_references: list[dict],
    tolerance_globale_pct: float = 15.0,
    familles: list[str] | None = None,
) -> tuple[list[AnomalieDetectee], dict]:
    """
    donnees          : JSON produit par l'extraction
    regles           : règles ACTIVES (dict avec code, type, famille, gravite, condition, seuil)
    prix_references  : prix ACTIFS (dict avec reference_produit, prix_reference, tolerance_pct...)
    familles         : filtre métier optionnel (ex. ["profile", "joint"]) ; None = tout
    """
    ctx = Contexte(
        prix_references={normaliser_ref(p["reference_produit"]): p for p in prix_references},
        tolerance_globale_pct=tolerance_globale_pct,
    )
    anomalies: list[AnomalieDetectee] = []
    for regle in regles:
        nom = (regle.get("condition") or {}).get("controle")
        if nom == "consigne_ia":
            continue  # transmise à Claude dans le prompt, pas évaluée ici
        controle = CONTROLES.get(nom)
        if controle is None:
            logger.warning("Règle %s : contrôle '%s' inconnu, ignorée.", regle.get("code"), nom)
            continue
        try:
            anomalies.extend(controle(donnees, regle, ctx))
        except Exception:  # une règle défaillante ne doit pas bloquer les autres
            logger.exception("Erreur dans le contrôle de la règle %s", regle.get("code"))

    # Observations repérées par Claude lors de l'extraction (sans règle en base)
    for obs in donnees.get("observations") or []:
        anomalies.append(AnomalieDetectee(
            nature="observation_ia", type=obs.get("type", "etude"), famille=obs.get("famille", "global"),
            gravite=obs.get("gravite", "alerte"), message=obs.get("message", ""),
            texte_source=obs.get("texte_source"),
        ))

    if familles:
        autorisees = set(familles) | {"global", "offre"}
        anomalies = [a for a in anomalies if a.famille in autorisees]

    ordre = {"erreur": 0, "alerte": 1, "info": 2}
    anomalies.sort(key=lambda a: ordre.get(a.gravite, 3))
    return anomalies, calculer_indicateurs(donnees)
