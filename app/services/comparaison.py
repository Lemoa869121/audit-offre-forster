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
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

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
    prix_references: dict[str, dict]   # prix NETS observés, clé = référence normalisée
    tolerance_globale_pct: float
    tarifs: dict[str, list[dict]] = field(default_factory=dict)  # prix TARIF, clé = code de base ("531731")
    remises: dict[str, float] = field(default_factory=dict)      # famille -> remise % (règles REMISE_*)


# --- Utilitaires ---------------------------------------------------------------

def normaliser_ref(ref: str | None) -> str:
    """'531.731  zm' -> '531.731 ZM'"""
    return " ".join((ref or "").upper().split())


def ref_de_base(ref: str | None) -> str:
    """Code article sans suffixe : '531.731 ZM' -> '531.731', '905386_25' -> '905386'."""
    return normaliser_ref(ref).replace("_", " ").split(" ")[0] if ref else ""


def trouver_prix_reference(ref: str | None, ctx: Contexte) -> dict | None:
    """Prix net : correspondance exacte d'abord, puis sur le code de base."""
    exact = ctx.prix_references.get(normaliser_ref(ref))
    if exact:
        return exact
    base = ref_de_base(ref)
    for cle, prix in ctx.prix_references.items():
        if ref_de_base(cle) == base:
            return prix
    return None


# --- Tarif FFR ---------------------------------------------------------------------
# Format des articles du tarif : "531.731F.51.A" = code 531.731, finition 51 (ZM), indice A.
# Dans les devis, le même article apparaît sous la forme "531.731 ZM" : on rapproche par le
# code de base (6 chiffres) puis par la finition.

_RE_CODE_6 = re.compile(r"^(\d{3})\.?(\d{3})")
_RE_CODE_F = re.compile(r"^(F\d{2})\.?(\d{4})")
_RE_CODE_FFR = re.compile(r"^(FFR)\.?(\d{3})")  # 'FFR102FND' (devis) = 'FFR.102F.ND.A' (tarif)
# Mot présent dans la référence ou la désignation du devis -> code finition du tarif
FINITIONS = {"ZM": "51", "GF": "62", "INOX": "62", "CRNI": "62"}


def code_base(ref: str | None) -> str:
    """'531.731 ZM' / '531.731F.51.A' / '531731' -> '531731' ; 'F15.1031.50.A' -> 'F151031'."""
    texte = normaliser_ref(ref)
    for motif in (_RE_CODE_6, _RE_CODE_FFR, _RE_CODE_F):
        m = motif.match(texte)
        if m:
            return m.group(1) + m.group(2)
    return re.sub(r"[^0-9A-Z]", "", ref_de_base(texte))


def finition_tarif(article: str) -> str | None:
    """'531.731F.51.A' -> '51' ; '907.662F.ND.A' -> 'ND'."""
    m = re.search(r"F\.?([0-9A-Z]{2})\.?[A-Z]$", normaliser_ref(article))
    return m.group(1) if m else None


def trouver_tarif(ligne: dict, ctx: Contexte) -> tuple[dict | None, bool]:
    """
    Renvoie (article du tarif, variante_supposee).
    Plusieurs variantes pour un même code (brut 50, ZM 51, inox 62...) : on choisit celle
    indiquée par la référence/désignation du devis ; à défaut la finition 50, signalée comme supposée.
    """
    candidats = ctx.tarifs.get(code_base(ligne.get("reference")), [])
    if len(candidats) <= 1:
        return (candidats[0] if candidats else None), False
    texte = f"{ligne.get('reference') or ''} {ligne.get('designation') or ''}".upper()
    for mot, code in FINITIONS.items():
        if re.search(rf"\b{mot}\b", texte):
            for c in candidats:
                if finition_tarif(c["reference_produit"]) == code:
                    return c, False
    defaut = next((c for c in candidats if finition_tarif(c["reference_produit"]) == "50"), candidats[0])
    return defaut, True


def taille_uv(ligne: dict) -> float | None:
    """Nombre d'unités par unité de vente lu dans le devis : 'UV 25 m', '2 UV á 25 m' -> 25."""
    for texte in (ligne.get("conditionnement"), ligne.get("quantite_texte")):
        m = re.search(r"UV\s*(?:á|à|a|de)?\s*([0-9]+(?:[.,][0-9]+)?)", texte or "", re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def prix_unitaire_tarif(tarif: dict, ligne: dict, famille: str) -> tuple[float, str]:
    """
    Ramène le prix tarif à l'unité du devis (m ou pce). Renvoie (prix, explication).
    - Profilés : tarif au mètre.
    - Sinon, "tarif_par" connu (appris d'un devis validé) -> appliqué.
    - Sinon, déduction : un tarif par UV divisé par la taille de l'UV reste supérieur au prix net ;
      un tarif à l'unité divisé par l'UV tombe nettement en dessous.
    """
    prix = tarif["prix_reference"]
    uv = tarif.get("taille_uv") or taille_uv(ligne)
    if famille == "profile" or tarif.get("tarif_par") == "unite" or not uv or uv <= 1:
        return prix, "tarif à l'unité"
    if tarif.get("tarif_par") == "uv":
        return prix / uv, f"tarif par UV de {uv:g}"
    if prix / uv >= (ligne.get("prix_unitaire") or 0):
        return prix / uv, f"tarif par UV de {uv:g} (déduit)"
    return prix, "tarif à l'unité (déduit)"


def _lignes_matiere(donnees: dict) -> list[dict]:
    return (donnees.get("analyse_matiere") or {}).get("lignes") or []


def _positions(donnees: dict) -> list[dict]:
    return (donnees.get("offre") or {}).get("positions") or []


def _valeur(bloc: dict | None) -> float | None:
    return bloc.get("valeur") if isinstance(bloc, dict) else None


def nombre_vantaux(donnees: dict) -> int:
    return int(sum((p.get("quantite") or 1) * (p.get("nb_vantaux") or 0) for p in _positions(donnees)))


def lignes_correspondantes(donnees: dict, condition: dict) -> list[dict]:
    """
    Lignes matière visées par une règle : par code article ("references", ex. "987009", "FFR.102"),
    par motif sur la référence ("motifs", regex, ex. "^RR\\d+/20/2$") ou par mot-clé dans la désignation.
    """
    refs = {code_base(r) for r in condition.get("references", [])}
    motifs = [re.compile(m, re.IGNORECASE) for m in condition.get("motifs", [])]
    mots = [m.lower() for m in condition.get("mots_cles", [])]
    resultat = []
    for ligne in _lignes_matiere(donnees):
        reference = normaliser_ref(ligne.get("reference"))
        designation = (ligne.get("designation") or "").lower()
        if (code_base(reference) in refs or any(m.search(reference) for m in motifs)
                or any(m in designation for m in mots)):
            resultat.append(ligne)
    return resultat


# --- Classements (CR4, FB6...) et articles hors lot -----------------------------------

def classements(position: dict) -> set[str]:
    """Classements normalisés : 'RC4' et 'CR4' désignent la même classe anti-effraction -> 'CR4'."""
    return {re.sub(r"^RC(\d)$", r"CR\1", c.upper().replace(" ", "")) for c in position.get("classements") or []}


def est_classee(position: dict, voulus: list[str] | None) -> bool:
    return bool(voulus) and bool(classements(position) & {c.upper() for c in voulus})


def _ferrures_texte(position: dict) -> str:
    return " ".join(position.get("ferrures_listees") or []).upper()


def references_hors_lot(donnees: dict) -> set[str]:
    """Codes des articles que Claude a rattachés à la liste « Fourniture hors lot » d'une position."""
    return {
        code_base(item.get("reference_facturee"))
        for p in _positions(donnees) for item in p.get("hors_lot") or []
        if item.get("reference_facturee")
    }


def est_hors_lot(ligne: dict, donnees: dict, motifs: list[str] = ()) -> bool:
    """Article fourni par le métallier : marqué par l'extraction, cité hors lot, ou motif connu (tube RR**/20/2)."""
    reference = normaliser_ref(ligne.get("reference"))
    return (bool(ligne.get("hors_lot"))
            or code_base(reference) in references_hors_lot(donnees)
            or any(re.search(m, reference, re.IGNORECASE) for m in motifs))


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
        gravite=champs.pop("gravite", regle.get("gravite", "erreur")),
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


def _probleme_prix(element: dict) -> str | None:
    """'à zéro', 'manquant' ou None si le prix unitaire et le total sont renseignés et non nuls."""
    valeurs = (element.get("prix_unitaire"), element.get("total"))
    if any(v == 0 for v in valeurs):
        return "prix à zéro"
    if any(v is None for v in valeurs):
        return "prix manquant" + (" ou illisible" if element.get("a_verifier") else "")
    return None


def ctrl_prix_nul(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    motifs_hors_lot = (regle.get("condition") or {}).get("motifs_hors_lot", [])
    anomalies = []
    for p in _positions(donnees):
        probleme = _probleme_prix(p)
        if probleme:
            anomalies.append(_anomalie(
                regle, f"Position {p.get('numero')} : {probleme}.",
                famille="offre", valeur_trouvee=p.get("prix_unitaire"), texte_source=p.get("texte_source"),
            ))
    for ligne in _lignes_matiere(donnees):
        probleme = _probleme_prix(ligne)
        if probleme == "prix à zéro" and est_hors_lot(ligne, donnees, motifs_hors_lot):
            continue  # article hors lot (fourni par le métallier) : prix à zéro normal
        gravite = regle.get("gravite", "erreur")
        if probleme == "prix à zéro" and ctx.tarifs and trouver_tarif(ligne, ctx)[0] is None:
            # Article hors tarif à 0 € (acier fourni par le métallier en général) : simple rappel (C3)
            gravite = "info"
            probleme = "prix à zéro, article hors tarif : rappel, vérifier qu'il figure dans la liste « Fourniture hors lot »"
        elif probleme == "prix à zéro":
            probleme += " (article au tarif, absent de la liste « Fourniture hors lot »)"
        if probleme:
            anomalies.append(_anomalie(
                regle, f"Article {ligne.get('reference')} ({ligne.get('designation')}) : {probleme}.", gravite=gravite,
                famille=ligne.get("famille", regle["famille"]), reference_produit=ligne.get("reference"),
                valeur_trouvee=ligne.get("prix_unitaire"), texte_source=ligne.get("texte_source"),
            ))
    return anomalies


def _comparer_au_tarif(ligne: dict, regle: dict, ctx: Contexte) -> AnomalieDetectee | None:
    """Compare un prix du devis au tarif FFR remisé. None si conforme ; lève LookupError si article absent du tarif."""
    tarif, variante_supposee = trouver_tarif(ligne, ctx)
    if tarif is None:
        raise LookupError
    prix = ligne["prix_unitaire"]
    famille = ligne.get("famille") or tarif.get("famille") or "global"
    if tarif.get("prix_reference") is None:
        return AnomalieDetectee(
            nature="ecart", type="prix", famille=famille, gravite="info",
            message=f"Article {ligne.get('reference')} : prix « sur demande » au tarif ({tarif['reference_produit']}), contrôle impossible.",
            code_regle=regle.get("code"), regle_id=regle.get("id"),
            reference_produit=ligne.get("reference"), valeur_trouvee=prix, texte_source=ligne.get("texte_source"),
        )
    if not (regle.get("condition") or {}).get("comparer_tarif", True):
        return None  # article présent au tarif avec un prix : suffisant tant que les remises ne sont pas validées
    prix_tarif, explication = prix_unitaire_tarif(tarif, ligne, famille)
    remise = ctx.remises.get(famille, 0.0)
    attendu = round(prix_tarif * (1 - remise / 100), 4)
    tolerance = tarif.get("tolerance_pct") or regle.get("seuil") or ctx.tolerance_globale_pct
    ecart = _pct(prix, attendu)
    if ecart is None or abs(ecart) <= tolerance:
        return None
    sens = "au-dessus" if ecart > 0 else "en dessous"
    detail = f"tarif {tarif['reference_produit']} {tarif['prix_reference']:.2f} €, {explication}, remise {remise:g} %"
    if variante_supposee:
        detail += ", variante supposée : finition non précisée dans le devis"
    return _anomalie(
        regle,
        (f"Article {ligne.get('reference')} : {prix:.2f} € soit {abs(ecart):.1f} % {sens} du prix attendu "
         f"{attendu:.2f} € ({detail} ; tolérance {tolerance:g} %)."),
        famille=famille, reference_produit=ligne.get("reference"),
        valeur_trouvee=prix, valeur_attendue=attendu, ecart_pct=ecart, texte_source=ligne.get("texte_source"),
    )


def ctrl_ecart_prix_reference(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        prix = ligne.get("prix_unitaire")
        if not prix:  # None (illisible) ou 0 (traité par PRIX_NUL)
            continue
        ref = trouver_prix_reference(ligne.get("reference"), ctx)
        if ref is None:
            # Pas de prix net connu : comparaison au tarif FFR remisé
            try:
                anomalie = _comparer_au_tarif(ligne, regle, ctx)
                if anomalie:
                    anomalies.append(anomalie)
                continue
            except LookupError:
                pass  # absent du tarif aussi -> proposition de prix ci-dessous
            # Apprentissage des prix : référence inconnue -> proposition à valider
            anomalies.append(AnomalieDetectee(
                nature="proposition_prix", type="prix", famille=ligne.get("famille", "global"), gravite="info",
                message=(f"Article {ligne.get('reference')} ({ligne.get('designation')}) absent du tarif et de la base : "
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
    """
    Quantité attendue = somme sur les positions de (vantaux × quantité par vantail).
    Options de condition :
      classements + seuil_classe : autre quantité par vantail pour les portes classées (ex. CR4/FB6 -> 4 paumelles)
      exclure_classements        : positions classées non concernées (ex. carré fourni par le métallier en CR4)
      exclure_si_ferrure         : positions dont les ferrures citent ce mot non concernées (ex. "ISEO")
    """
    condition = regle["condition"]
    par_defaut = regle.get("seuil") or 1
    vantaux, attendu, details = 0, 0.0, []
    for p in _positions(donnees):
        nb = (p.get("quantite") or 1) * (p.get("nb_vantaux") or 0)
        if not nb or est_classee(p, condition.get("exclure_classements")):
            continue
        if condition.get("exclure_si_ferrure") and condition["exclure_si_ferrure"].upper() in _ferrures_texte(p):
            continue
        par_vantail = condition.get("seuil_classe", par_defaut) if est_classee(p, condition.get("classements")) else par_defaut
        vantaux += nb
        attendu += nb * par_vantail
        details.append(f"pos. {p.get('numero')} : {nb:g} vantail(s) × {par_vantail:g}")
    if vantaux == 0:
        return []
    lignes = lignes_correspondantes(donnees, condition)
    if not lignes:
        return [_anomalie(
            regle, f"{regle['description']} Article absent de l'analyse matière ({', '.join(details)} = {attendu:g} attendu(s)).",
            valeur_trouvee=0, valeur_attendue=attendu,
        )]
    quantites = [_quantite_besoin(l) for l in lignes]
    if any(q is None for q in quantites):
        return []
    trouve = sum(quantites)
    if trouve != attendu:
        return [_anomalie(
            regle,
            f"{regle['description']} Trouvé {trouve:g}, attendu {attendu:g} ({', '.join(details)}).",
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
    exclues = set((regle.get("condition") or {}).get("exclure_familles", []))
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        if ligne.get("famille") in exclues:
            continue
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


# --- Contrôles appris sur le devis 2026-XAG-00525 (porte CR4 / FB6) -------------------

def ctrl_gaches_par_serrure(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Serrure ISEO : toujours 3 gâches ISEO (1 centrale + haute et basse) par serrure."""
    mot = (regle["condition"].get("mot_serrure") or "ISEO").lower()
    lignes = [l for l in _lignes_matiere(donnees) if mot in (l.get("designation") or "").lower()]
    est_gache = lambda l: re.search(r"g[âa]che", (l.get("designation") or "").lower())  # noqa: E731
    serrures = [l for l in lignes if "serrure" in (l.get("designation") or "").lower() and not est_gache(l)]
    gaches = [l for l in lignes if est_gache(l)]
    nb_serrures = sum(_quantite_besoin(l) or 0 for l in serrures)
    if not nb_serrures:
        return []
    attendu = nb_serrures * (regle.get("seuil") or 3)
    trouve = sum(_quantite_besoin(l) or 0 for l in gaches)
    if trouve == attendu:
        return []
    return [_anomalie(
        regle, f"{regle['description']} Trouvé {trouve:g} gâche(s) pour {nb_serrures:g} serrure(s), attendu {attendu:g}.",
        valeur_trouvee=trouve, valeur_attendue=attendu,
        texte_source=" | ".join(l.get("texte_source") or "" for l in serrures + gaches),
    )]


def ctrl_kit_classement(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Positions classées (ex. CR4) : chaque article du kit doit figurer dans l'analyse matière."""
    condition = regle["condition"]
    classees = [p for p in _positions(donnees) if est_classee(p, condition.get("classements"))]
    if not classees:
        return []
    numeros = ", ".join(str(p.get("numero")) for p in classees)
    anomalies = []
    for article in condition.get("articles", []):
        if not lignes_correspondantes(donnees, article):
            anomalies.append(_anomalie(
                regle,
                (f"Porte {'/'.join(condition['classements'])} (pos. {numeros}) : {article['libelle']} absent de l'analyse matière"
                 + (f" ({', '.join(article.get('references', []))})." if article.get("references") else ".")),
                reference_produit=(article.get("references") or [None])[0],
                texte_source=" | ".join(p.get("texte_source") or "" for p in classees),
            ))
    return anomalies


_MOTS_DIN = re.compile(r"\b(DROITE|GAUCHE|D|G)\b")


def variante_din(article_tarif: dict, din_cible: str, ctx: Contexte) -> dict | None:
    """Article du tarif identique mais de l'autre sens DIN (ex. FFR.297 DIN D -> FFR.298 DIN G)."""
    def gabarit(texte: str) -> str:
        # sens DIN neutralisé, codes fournisseur de 5 chiffres et plus ignorés (« - 033232 » / « - 033233 »)
        return re.sub(r"\b\d{5,}\b", "", _MOTS_DIN.sub("#", normaliser_ref(texte))).strip(" -")
    cible = gabarit(article_tarif.get("designation") or "")
    mots_cibles = {"G": {"G", "GAUCHE"}, "D": {"D", "DROITE"}}[din_cible]
    for candidats in ctx.tarifs.values():
        for c in candidats:
            designation = normaliser_ref(c.get("designation"))
            if (c is not article_tarif and gabarit(designation) == cible
                    and set(_MOTS_DIN.findall(designation)) & mots_cibles):
                return c
    return None


def ctrl_coherence_din(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Serrures et gâches dans le même sens DIN que la porte (contrôle fait si toutes les portes ont le même DIN)."""
    dins = {(p.get("din") or "").upper() for p in _positions(donnees) if p.get("nb_vantaux")} - {""}
    if len(dins) != 1:
        return []
    din_porte = dins.pop()
    familles = set(regle["condition"].get("familles", ["ferrure"]))
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        din_ligne = (ligne.get("din") or "").upper()
        if ligne.get("famille") not in familles or din_ligne not in ("G", "D") or din_ligne == din_porte:
            continue
        tarif, _ = trouver_tarif(ligne, ctx)
        variante = variante_din(tarif, din_porte, ctx) if tarif else None
        conseil = f" Version DIN {din_porte} au tarif : {variante['reference_produit']}." if variante else ""
        anomalies.append(_anomalie(
            regle,
            f"Article {ligne.get('reference')} ({ligne.get('designation')}) en DIN {din_ligne} alors que la porte est DIN {din_porte}.{conseil}",
            reference_produit=ligne.get("reference"), texte_source=ligne.get("texte_source"),
        ))
    return anomalies


def ctrl_configuration_vs_pv(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Le bloc descriptif (texte du PV) doit décrire la porte réellement chiffrée."""
    anomalies = []
    for p in _positions(donnees):
        ecarts = []
        nb_bloc = p.get("nb_vantaux_bloc")
        if nb_bloc is not None and p.get("nb_vantaux") and nb_bloc != p["nb_vantaux"]:
            ecarts.append(f"{nb_bloc:g} vantail(s) dans le texte, {p['nb_vantaux']} chiffré(s)")
        sens, sens_bloc = p.get("sens_ouverture"), p.get("sens_ouverture_bloc")
        if sens and sens_bloc and sens != sens_bloc:
            ecarts.append(f"ouverture {sens_bloc} dans le texte, {sens} chiffrée")
        if ecarts:
            anomalies.append(_anomalie(
                regle,
                f"ATTENTION position {p.get('numero')} : texte incohérent avec la porte chiffrée ({' ; '.join(ecarts)}).",
                texte_source=p.get("texte_source"),
            ))
    return anomalies


def ctrl_hors_lot_facture(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Un élément listé « Fourniture hors lot » ne doit pas être facturé dans l'analyse matière."""
    lignes = {code_base(l.get("reference")): l for l in _lignes_matiere(donnees)}
    anomalies = []
    for p in _positions(donnees):
        for item in p.get("hors_lot") or []:
            ligne = lignes.get(code_base(item.get("reference_facturee")))
            if ligne and (ligne.get("total") or ligne.get("prix_unitaire") or 0) > 0:
                anomalies.append(_anomalie(
                    regle,
                    (f"Position {p.get('numero')} : « {item.get('texte')} » est listé hors lot, mais "
                     f"{ligne.get('reference')} ({ligne.get('designation')}) est facturé "
                     f"{(ligne.get('total') or 0):.2f} €. Corriger le texte ou retirer l'article."),
                    reference_produit=ligne.get("reference"), valeur_trouvee=ligne.get("total"),
                    texte_source=" | ".join(filter(None, [item.get("texte"), ligne.get("texte_source")])),
                ))
    return anomalies


def ctrl_renfort_fb(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """
    Renfort FB (983302) inséré dans chaque profilé : besoin ≈ somme des besoins des profilés × facteur
    (FB6 : renforts doublés -> × 2 ; FB4 -> × 1).
    """
    condition = regle["condition"]
    renforts = lignes_correspondantes(donnees, {"references": condition.get("renfort", [])})
    if not renforts:
        return []
    refs_renfort = {code_base(r.get("reference")) for r in renforts}
    mot = (condition.get("mot_profile") or "UNICO").lower()
    profils = [l for l in _lignes_matiere(donnees)
               if l.get("famille") == "profile" and mot in (l.get("designation") or "").lower()
               and code_base(l.get("reference")) not in refs_renfort]
    base = sum(_quantite_besoin(l) or 0 for l in profils)
    if not base:
        return []
    facteur, classe = condition.get("facteur_defaut", 1), "FB4"
    for nom, valeur in (condition.get("facteurs") or {}).items():
        if any(est_classee(p, [nom]) for p in _positions(donnees)):
            facteur, classe = valeur, nom
    attendu = round(base * facteur, 2)
    trouve = sum(_quantite_besoin(l) or 0 for l in renforts)
    ecart = _pct(trouve, attendu)
    tolerance = regle.get("seuil") or 10
    if ecart is None or abs(ecart) <= tolerance:
        return []
    detail = " + ".join(f"{l.get('reference')} {(_quantite_besoin(l) or 0):g} m" for l in profils)
    return [_anomalie(
        regle,
        (f"Renfort {renforts[0].get('reference')} : besoin {trouve:g} m, attendu {attendu:g} m "
         f"(({detail}) × {facteur:g} en {classe} ; écart {ecart:+.1f} %, tolérance ± {tolerance:g} %)."),
        reference_produit=renforts[0].get("reference"), valeur_trouvee=trouve, valeur_attendue=attendu, ecart_pct=ecart,
        texte_source=renforts[0].get("texte_source"),
    )]


def ctrl_article_a_remplacer(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Articles à ne plus utiliser, avec leur remplaçant (ex. 975301 -> 985301)."""
    anomalies = []
    for r in regle["condition"].get("remplacements", []):
        for ligne in lignes_correspondantes(donnees, {"references": [r["reference"]]}):
            anomalies.append(_anomalie(
                regle,
                f"Article {ligne.get('reference')} ({ligne.get('designation')}) à remplacer par {r['remplacant']}"
                + (f" : {r['motif']}." if r.get("motif") else "."),
                reference_produit=ligne.get("reference"), texte_source=ligne.get("texte_source"),
            ))
    return anomalies


def ctrl_disponibilite(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Pour attirer l'attention : articles non tenus en stock ou sur demande (hors articles hors lot)."""
    motifs_hors_lot = regle["condition"].get("motifs_hors_lot", [])
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        if est_hors_lot(ligne, donnees, motifs_hors_lot):
            continue
        mentions = [m for m, champ in (("non tenu en stock", "non_tenu_stock"), ("sur demande", "sur_demande")) if ligne.get(champ)]
        if mentions:
            anomalies.append(_anomalie(
                regle, f"Article {ligne.get('reference')} ({ligne.get('designation')}) : {' et '.join(mentions)}, vérifier le délai.",
                reference_produit=ligne.get("reference"), texte_source=ligne.get("texte_source"),
            ))
    return anomalies


def ctrl_barres_profiles(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Profilés vendus en barres de 6 m dont 5,90 m utilisables : nb barres × 5,90 ≥ besoin."""
    utile = regle.get("seuil") or 5.9
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        m = re.search(r"(\d+)\s*x\s*6\s*m", ligne.get("quantite_texte") or "", re.IGNORECASE)
        besoin = ligne.get("besoin")
        if ligne.get("famille") != "profile" or not m or besoin is None:
            continue
        barres = int(m.group(1))
        if barres * utile + 1e-9 < besoin:
            minimum = -(-besoin // utile)
            anomalies.append(_anomalie(
                regle,
                (f"Profilé {ligne.get('reference')} : {barres} barre(s) de 6 m pour un besoin de {besoin:g} m "
                 f"({utile:g} m utilisables par barre), au moins {minimum:g} barre(s) nécessaire(s)."),
                reference_produit=ligne.get("reference"), valeur_trouvee=barres, valeur_attendue=minimum,
                texte_source=ligne.get("texte_source"),
            ))
    return anomalies


# --- Contrôles appris sur l'offre modifiée 2026-XAG-00525 ----------------------------

_RE_ARTICLE_LISTE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*x\s*([A-Z0-9][A-Z0-9._/\-]*)", re.IGNORECASE)


def ctrl_liste_vs_matiere(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Articles cités dans « Ferrures de fenêtre » / « Article complémentaire » : présents dans l'analyse matière, sauf hors lot."""
    codes_matiere = {code_base(l.get("reference")) for l in _lignes_matiere(donnees)}
    hors_lot = references_hors_lot(donnees)
    anomalies = []
    for p in _positions(donnees):
        for champ, rubrique in (("ferrures_listees", "Ferrures de fenêtre"), ("articles_complementaires", "Article complémentaire")):
            for texte in p.get(champ) or []:
                m = _RE_ARTICLE_LISTE.match(texte)
                if not m:
                    continue
                code = code_base(m.group(2))
                if code in codes_matiere or code in hors_lot:
                    continue
                anomalies.append(_anomalie(
                    regle,
                    (f"Position {p.get('numero')} : {m.group(2)} cité dans « {rubrique} » ({texte.strip()}) mais absent de "
                     f"l'analyse matière : article non chiffré, ou à indiquer hors lot."),
                    reference_produit=m.group(2), texte_source=texte,
                ))
    return anomalies


def ctrl_serrure_antipanique(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Porte antipanique : la serrure ISEO doit être une version « Exit » (inutile sinon, le texte est parfois général)."""
    if not any(p.get("antipanique") for p in _positions(donnees)):
        return []
    mot = (regle["condition"].get("mot_serrure") or "ISEO").lower()
    anomalies = []
    for ligne in _lignes_matiere(donnees):
        designation = (ligne.get("designation") or "").lower()
        if mot not in designation or "serrure" not in designation or re.search(r"g[âa]che", designation):
            continue
        tarif, _ = trouver_tarif(ligne, ctx)
        if "exit" not in designation and "exit" not in ((tarif or {}).get("designation") or "").lower():
            anomalies.append(_anomalie(
                regle, f"Porte antipanique : la serrure {ligne.get('reference')} ({ligne.get('designation')}) n'est pas une version « Exit ».",
                reference_produit=ligne.get("reference"), texte_source=ligne.get("texte_source"),
            ))
    return anomalies


def ctrl_hauteur_max_pv(donnees: dict, regle: dict, ctx: Contexte) -> list[AnomalieDetectee]:
    """Hauteur du châssis ≤ hauteur maximale admise par le rapport d'essai."""
    anomalies = []
    for p in _positions(donnees):
        hauteur, maximum = p.get("hauteur_mm"), p.get("hauteur_max_pv_mm")
        if hauteur and maximum and hauteur > maximum:
            anomalies.append(_anomalie(
                regle, f"Position {p.get('numero')} : hauteur {hauteur:g} mm supérieure au maximum du rapport d'essai ({maximum:g} mm).",
                valeur_trouvee=hauteur, valeur_attendue=maximum, ecart_pct=_pct(hauteur, maximum), texte_source=p.get("texte_source"),
            ))
    return anomalies


CONTROLES: dict[str, Callable[[dict, dict, Contexte], list[AnomalieDetectee]]] = {
    "liste_vs_matiere": ctrl_liste_vs_matiere,
    "serrure_antipanique": ctrl_serrure_antipanique,
    "hauteur_max_pv": ctrl_hauteur_max_pv,
    "gaches_par_serrure": ctrl_gaches_par_serrure,
    "kit_classement": ctrl_kit_classement,
    "coherence_din": ctrl_coherence_din,
    "configuration_vs_pv": ctrl_configuration_vs_pv,
    "hors_lot_facture": ctrl_hors_lot_facture,
    "renfort_fb": ctrl_renfort_fb,
    "article_a_remplacer": ctrl_article_a_remplacer,
    "disponibilite": ctrl_disponibilite,
    "barres_profiles": ctrl_barres_profiles,
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
    tarifs: dict[str, list[dict]] = defaultdict(list)
    nets: dict[str, dict] = {}
    for p in prix_references:
        if p.get("type_prix", "net") == "tarif":
            tarifs[code_base(p["reference_produit"])].append(p)
        elif p.get("prix_reference") is not None:
            nets[normaliser_ref(p["reference_produit"])] = p
    ctx = Contexte(
        prix_references=nets,
        tolerance_globale_pct=tolerance_globale_pct,
        tarifs=dict(tarifs),
        remises={r["famille"]: float(r["seuil"] or 0) for r in regles
                 if (r.get("condition") or {}).get("controle") == "remise_tarif"},
    )
    anomalies: list[AnomalieDetectee] = []
    for regle in regles:
        nom = (regle.get("condition") or {}).get("controle")
        if nom in ("consigne_ia", "remise_tarif"):
            continue  # consigne : transmise à Claude ; remise : paramètre lu par ECART_PRIX_REF
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
