"""
Extraction : PDF -> JSON structuré, via l'API Claude.

Choix techniques :
- Le PDF est envoyé tel quel (bloc "document") : Claude lit le texte ET l'image de
  chaque page, ce qui couvre les tableaux et les PDF scannés.
- Sortie forcée par "structured outputs" (output_config.format = json_schema) :
  l'API garantit une réponse conforme au schéma SCHEMA_EXTRACTION, pas de texte libre.
- Chaque donnée clé porte "texte_source" (extrait brut) et "a_verifier" (doute).
- Les consignes apprises (règles "consigne_ia") sont ajoutées au prompt.
- Les logs ne contiennent que des métadonnées (nom, pages, tokens, coût), jamais le contenu.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from dataclasses import dataclass

import anthropic
from pypdf import PdfReader, PdfWriter

from app.config import CLAUDE_MODEL, MAX_PAGES_PAR_APPEL, verifier_cle_api

logger = logging.getLogger(__name__)

# Tarifs USD par million de tokens (entrée, sortie) — https://platform.claude.com/docs/en/about-claude/pricing
TARIFS_USD_PAR_MTOK = {
    "claude-sonnet-5-5": (2.0, 10.0),
}
SEUIL_CARACTERES_PAR_PAGE = 50  # en dessous : PDF considéré comme scanné
MAX_TOKENS_SORTIE = 16000


class ErreurPDF(Exception):
    """PDF inutilisable (corrompu, protégé, vide)."""


class ErreurExtraction(Exception):
    """Échec de l'appel API ou réponse inexploitable."""


# --- Schéma JSON de sortie -----------------------------------------------------------
# Contraintes des structured outputs : objets en additionalProperties=false, et au plus
# 16 champs à type "union" (ex. nombre OU null) par requête. Notre schéma en compterait ~30,
# donc : les nombres sont des CHAÎNES contrôlées par regex ("1384.73", ou "" si absent),
# converties en float/None après réception (_convertir_nombres). Zéro union, schéma strict.

CHAMPS_NUMERIQUES = {
    "valeur", "quantite", "largeur_mm", "hauteur_mm", "prix_unitaire", "total",
    "quantite_commandee", "besoin", "surface_m2", "perimetre_m", "somme_surface_m2", "somme_perimetre_m",
}


def _nullable(type_json: str) -> dict:
    if type_json == "number":
        return {"type": "string", "pattern": r"^(-?[0-9]+(\.[0-9]+)?)?$",
                "description": "Nombre avec point décimal (ex. 1384.73), chaîne vide si absent."}
    return {"type": "string", "description": "Chaîne vide si absent."}


def _objet(proprietes: dict) -> dict:
    return {"type": "object", "properties": proprietes, "required": list(proprietes), "additionalProperties": False}


def _convertir_nombres(objet) -> None:
    """Après réception : "1384.73" -> 1384.73, "" -> None (champs de CHAMPS_NUMERIQUES), "" -> None (textes)."""
    if isinstance(objet, dict):
        for cle, valeur in objet.items():
            if isinstance(valeur, str) and cle in CHAMPS_NUMERIQUES:
                objet[cle] = float(valeur) if valeur else None
            elif valeur == "" and cle not in ("texte_source", "message"):
                objet[cle] = None
            else:
                _convertir_nombres(valeur)
    elif isinstance(objet, list):
        for element in objet:
            _convertir_nombres(element)


TRACE = {
    "texte_source": {"type": "string", "description": "Extrait brut et littéral du PDF ayant servi (200 caractères max)."},
    "a_verifier": {"type": "boolean", "description": "true si la donnée est illisible, ambiguë ou déduite."},
}

VALEUR_TRACEE = _objet({"valeur": _nullable("number"), **TRACE})

FAMILLES_MATIERE = ["profile", "ferrure", "accessoire", "joint"]

SCHEMA_EXTRACTION = _objet({
    "document": _objet({
        "fournisseur": _nullable("string"),
        "reference_affaire": _nullable("string"),
        "client": _nullable("string"),
        "date_document": _nullable("string"),
        "sections_presentes": {
            "type": "array",
            "items": {"type": "string", "enum": ["offre_prix", "analyse_matiere", "consultation_vitrages", "autre"]},
        },
    }),
    "offre": _objet({
        "positions": {"type": "array", "items": _objet({
            "numero": {"type": "string"},
            "quantite": _nullable("number"),
            "designation": {"type": "string"},
            "largeur_mm": _nullable("number"),
            "hauteur_mm": _nullable("number"),
            "systeme": _nullable("string"),
            "nb_vantaux": {"type": "integer"},
            "nb_fixes": {"type": "integer"},
            "prix_unitaire": _nullable("number"),
            "total": _nullable("number"),
            "materiau_parclose_description": _nullable("string"),
            "materiau_parclose_configuration": _nullable("string"),
            "a_bloc_descriptif": {"type": "boolean"},
            "elements_hors_gamme": {"type": "array", "items": {"type": "string"}},
            **TRACE,
        })},
        "total_ht": VALEUR_TRACEE,
    }),
    "analyse_matiere": _objet({
        "lignes": {"type": "array", "items": _objet({
            "famille": {"type": "string", "enum": FAMILLES_MATIERE},
            "reference": {"type": "string"},
            "designation": {"type": "string"},
            "materiau": _nullable("string"),
            "quantite_texte": {"type": "string"},
            "quantite_commandee": _nullable("number"),
            "besoin": _nullable("number"),
            "unite_quantite": {"type": "string", "enum": ["m", "pce", "autre"]},
            "conditionnement": _nullable("string"),
            "prix_unitaire": _nullable("number"),
            "unite_prix": {"type": "string", "enum": ["m", "pce", "autre"]},
            "total": _nullable("number"),
            **TRACE,
        })},
        "sous_totaux": {"type": "array", "items": _objet({
            "famille": {"type": "string", "enum": FAMILLES_MATIERE},
            "valeur": _nullable("number"),
            "texte_source": {"type": "string"},
        })},
        "total": VALEUR_TRACEE,
    }),
    "vitrages": _objet({
        "lignes": {"type": "array", "items": _objet({
            "position": {"type": "string"},
            "numero": {"type": "string"},
            "quantite": _nullable("number"),
            "largeur_mm": _nullable("number"),
            "hauteur_mm": _nullable("number"),
            "surface_m2": _nullable("number"),
            "perimetre_m": _nullable("number"),
            "texte_source": {"type": "string"},
        })},
        "somme_surface_m2": _nullable("number"),
        "somme_perimetre_m": _nullable("number"),
    }),
    "observations": {"type": "array", "items": _objet({
        "type": {"type": "string", "enum": ["etude", "prix"]},
        "famille": {"type": "string", "enum": FAMILLES_MATIERE + ["offre", "vitrage", "global"]},
        "gravite": {"type": "string", "enum": ["info", "alerte", "erreur"]},
        "message": {"type": "string"},
        "texte_source": {"type": "string"},
    })},
})


PROMPT_SYSTEME = """Tu es un assistant d'extraction de données pour des offres de menuiserie métallique \
(systèmes de profilés, ex. Forster). Tu extrais fidèlement les données du PDF dans le schéma JSON imposé.

Structure habituelle d'un document :
1. "Offre de prix" : positions numérotées (001, 002...), quantité, désignation, dimensions du châssis \
(largeur x hauteur en mm), système, prix unitaire et total, puis "Total HT net".
   Certaines positions ont un bloc descriptif en couleur (Type, Configurations, Remplissages, Inclus dans l'offre, \
Hors gamme) : a_bloc_descriptif = true seulement si ce bloc est présent pour CETTE position.
2. "Analyse matière" : tableaux par famille (Profilés, Ferrure, Accessoires, Joints) avec numéro d'article, \
quantité / unité (besoin entre parenthèses), description, prix unitaire, total ; puis sous-totaux et "Total".
3. "Consultation vitrages/panneaux" : dimensions, surface et périmètre des vitrages par position.

Règles d'extraction :
- N'invente JAMAIS une valeur. Si elle est absente : chaîne vide "". Si elle est illisible, ambiguë ou \
déduite : remplis-la au mieux ET mets a_verifier = true.
- texte_source : copie littérale courte (200 caractères max) de l'extrait du PDF d'où vient la donnée.
- Nombres : écris-les avec un point décimal et sans espace ("1 384,73" -> "1384.73").
- quantite_texte : la quantité telle qu'écrite (ex. "3 x 6 m (17,0)", "2 UV á 25 m (39,76)").
- quantite_commandee : quantité totale commandée dans l'unité du prix : "3 x 6 m" -> 18 ; \
"6 UV á 2 pce" -> 12 ; "2 UV á 25 m" -> 50 ; "4 pce" -> 4.
- besoin : le nombre entre parenthèses (quantité nécessaire), sinon null.
- conditionnement : ex. "barre 6 m", "UV 25 m", "UV 10 pce", sinon null.
- nb_vantaux / nb_fixes : nombre d'ouvrants et d'éléments fixes de la position \
(ex. "un élément fixe et une porte à 1 battant" -> 1 fixe, 1 vantail).
- materiau_parclose_description : matériau après "Par :" dans la description du châssis (ex. "Aluminium").
- materiau_parclose_configuration : matériau après "Parclose :" dans le bloc Configurations, sinon null.
- materiau (analyse matière) : matériau indiqué dans la description de l'article, sinon null.
- Si une section est absente du document, renvoie des listes vides et des chaînes vides.

observations : liste uniquement les incohérences ou oublis techniques ou de prix significatifs que tu remarques \
en lisant (ex. information contradictoire entre deux pages, mention manquante). Pas de remarques générales, \
pas de doublon avec les données extraites. Liste vide si rien de notable."""


@dataclass
class ResultatExtraction:
    donnees: dict
    nb_pages: int
    pdf_scanne: bool
    modele: str
    tokens_entree: int
    tokens_sortie: int
    cout_usd: float


# --- Vérifications du PDF ------------------------------------------------------------------

def inspecter_pdf(contenu: bytes) -> tuple[int, bool]:
    """Renvoie (nombre de pages, PDF scanné ?) ou lève ErreurPDF avec un message clair."""
    try:
        lecteur = PdfReader(io.BytesIO(contenu))
        if lecteur.is_encrypted and not lecteur.decrypt(""):
            raise ErreurPDF("Le PDF est protégé par mot de passe : déverrouillez-le avant l'analyse.")
        nb_pages = len(lecteur.pages)
        nb_caracteres = sum(len((page.extract_text() or "").strip()) for page in lecteur.pages)
    except ErreurPDF:
        raise
    except Exception as exc:
        raise ErreurPDF("Fichier illisible : ce n'est pas un PDF valide ou il est corrompu.") from exc
    if nb_pages == 0:
        raise ErreurPDF("Le PDF ne contient aucune page.")
    return nb_pages, nb_caracteres < SEUIL_CARACTERES_PAR_PAGE * nb_pages


def decouper_pdf(contenu: bytes, max_pages: int) -> list[bytes]:
    """Découpe en morceaux de max_pages pages (limite API : 100 pages par requête)."""
    lecteur = PdfReader(io.BytesIO(contenu))
    morceaux = []
    for debut in range(0, len(lecteur.pages), max_pages):
        ecrivain = PdfWriter()
        for page in lecteur.pages[debut:debut + max_pages]:
            ecrivain.add_page(page)
        tampon = io.BytesIO()
        ecrivain.write(tampon)
        morceaux.append(tampon.getvalue())
    return morceaux


# --- Appel API ---------------------------------------------------------------------------

def estimer_cout(modele: str, tokens_entree: int, tokens_sortie: int) -> float:
    prix_entree, prix_sortie = TARIFS_USD_PAR_MTOK.get(modele, (0.0, 0.0))
    return round(tokens_entree / 1e6 * prix_entree + tokens_sortie / 1e6 * prix_sortie, 4)


def _construire_consigne(consignes: list[str], pdf_scanne: bool, morceau: tuple[int, int]) -> str:
    texte = "Extrais les données de ce document selon le schéma."
    if morceau[1] > 1:
        texte += f" (Partie {morceau[0]}/{morceau[1]} d'un document plus long.)"
    if pdf_scanne:
        texte += " Ce PDF est scanné (image) : sois particulièrement prudent et mets a_verifier = true en cas de doute."
    if consignes:
        texte += "\n\nConsignes apprises des validations précédentes :\n" + "\n".join(f"- {c}" for c in consignes)
    return texte


def _appeler_claude(client: anthropic.Anthropic, pdf: bytes, consigne: str) -> tuple[dict, int, int]:
    try:
        reponse = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS_SORTIE,
            system=PROMPT_SYSTEME,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {
                        "type": "base64", "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf).decode("ascii"),
                    }},
                    {"type": "text", "text": consigne},
                ],
            }],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA_EXTRACTION}},
        )
    except anthropic.AuthenticationError as exc:
        raise ErreurExtraction("Clé API Anthropic invalide : vérifiez ANTHROPIC_API_KEY dans .env.") from exc
    except anthropic.RateLimitError as exc:
        raise ErreurExtraction("Limite de requêtes API atteinte : réessayez dans quelques minutes.") from exc
    except anthropic.BadRequestError as exc:
        raise ErreurExtraction(f"Requête refusée par l'API (PDF ou schéma) : {exc.message}") from exc
    except anthropic.APIError as exc:
        raise ErreurExtraction(f"Erreur de l'API Claude : {exc.__class__.__name__}.") from exc

    if reponse.stop_reason == "max_tokens":
        raise ErreurExtraction(
            "Réponse tronquée (document trop dense) : réduisez MAX_PAGES_PAR_APPEL dans .env et relancez."
        )
    texte = next((bloc.text for bloc in reponse.content if bloc.type == "text"), None)
    if texte is None:
        raise ErreurExtraction("Réponse de l'API sans contenu exploitable.")
    try:
        donnees = json.loads(texte)
    except json.JSONDecodeError as exc:
        raise ErreurExtraction("Réponse de l'API non conforme au format JSON attendu.") from exc
    _convertir_nombres(donnees)
    return donnees, reponse.usage.input_tokens, reponse.usage.output_tokens


def _fusionner(parties: list[dict]) -> dict:
    """Fusion des extractions d'un PDF découpé : listes concaténées, première valeur non nulle sinon."""
    if len(parties) == 1:
        return parties[0]

    def fusion(a, b):
        if isinstance(a, list) and isinstance(b, list):
            return a + b
        if isinstance(a, dict) and isinstance(b, dict):
            if "valeur" in a:  # valeur tracée : garder celle qui est renseignée
                return a if a.get("valeur") is not None else b
            return {cle: fusion(a.get(cle), b.get(cle)) for cle in a.keys() | b.keys()}
        return a if a not in (None, "") else b

    resultat = parties[0]
    for partie in parties[1:]:
        resultat = fusion(resultat, partie)
    return resultat


def _marquer_a_verifier(objet) -> None:
    """PDF scanné : toutes les données tracées passent à a_verifier = true."""
    if isinstance(objet, dict):
        if "a_verifier" in objet:
            objet["a_verifier"] = True
        for valeur in objet.values():
            _marquer_a_verifier(valeur)
    elif isinstance(objet, list):
        for element in objet:
            _marquer_a_verifier(element)


def extraire_pdf(contenu: bytes, nom_fichier: str, consignes: list[str] | None = None) -> ResultatExtraction:
    """Point d'entrée : vérifie, découpe si besoin, appelle Claude et renvoie les données + coûts."""
    client = anthropic.Anthropic(api_key=verifier_cle_api())
    nb_pages, pdf_scanne = inspecter_pdf(contenu)
    morceaux = decouper_pdf(contenu, MAX_PAGES_PAR_APPEL) if nb_pages > MAX_PAGES_PAR_APPEL else [contenu]

    parties, tokens_entree, tokens_sortie = [], 0, 0
    for i, morceau in enumerate(morceaux, start=1):
        consigne = _construire_consigne(consignes or [], pdf_scanne, (i, len(morceaux)))
        donnees, t_in, t_out = _appeler_claude(client, morceau, consigne)
        parties.append(donnees)
        tokens_entree += t_in
        tokens_sortie += t_out

    donnees = _fusionner(parties)
    if pdf_scanne:
        _marquer_a_verifier(donnees)

    cout = estimer_cout(CLAUDE_MODEL, tokens_entree, tokens_sortie)
    logger.info(
        "Extraction '%s' : %d page(s), %d appel(s), scanné=%s, tokens entrée=%d sortie=%d, coût≈%.4f $",
        nom_fichier, nb_pages, len(morceaux), pdf_scanne, tokens_entree, tokens_sortie, cout,
    )
    return ResultatExtraction(donnees, nb_pages, pdf_scanne, CLAUDE_MODEL, tokens_entree, tokens_sortie, cout)
