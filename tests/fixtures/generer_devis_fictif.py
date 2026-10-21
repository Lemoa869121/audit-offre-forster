"""
Génère tests/fixtures/devis_fictif.pdf : une offre FICTIVE (client, affaire et montants inventés)
au format des offres Forster, avec des erreurs volontaires, pour un premier essai réel de l'extraction.

Usage : python tests/fixtures/generer_devis_fictif.py   (nécessite reportlab : pip install reportlab)

Erreurs volontaires (ce que l'analyse doit trouver) :
  1. TOTAL_OFFRE_VS_MATIERE : total de l'offre 3 600,00 € différent du total de l'analyse matière
  2. COHERENCE_DIN          : gâche FFR.307 (DIN D) sur une porte DIN G
  3. ARTICLE_A_REMPLACER    : joint 975301 au lieu de 985301
  4. KIT_CR4                : béquilles 907350 absentes
  5. PRIX_NUL               : 987454 à 0,00 € alors qu'il est au tarif
  6. PAUMELLES_PAR_VANTAIL  : 6 paumelles pour 2 vantaux CR4 (4 par vantail attendues)
Points normaux à ne PAS signaler comme erreurs : RR15/20/2 à 0 € (tube fourni par le métallier),
renfort 983302 doublé (FB6), vitrage 38 mm.
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SORTIE = Path(__file__).parent / "devis_fictif.pdf"
AFFAIRE = "2026-TEST-0001-CLIENT FICTIF CR4 FB6"


def euros(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


# (quantité texte, numéro, description, prix unitaire, quantité facturée)
PROFILES = [
    ("2 x 6 m (6,4)", "533.751 ZM", "Forster UNICO, Acier Zinc-Magnésium", 36.08, 12),
    ("2 x 6 m (9,0)", "533.752 ZM", "Forster UNICO, Acier Zinc-Magnésium", 37.75, 12),
    ("1 x 6 m (2,3)", "533.753 ZM", "Forster UNICO, Acier Zinc-Magnésium", 37.75, 6),
    ("7 x 6 m (36,3)", "983302", "Renfort FB4, Aluminium", 19.38, 42),
    ("1 x 4 m (2,4)", "FFR102FND", "Plat acier usiné pour CR4", 72.10, 4),
    ("2 x 6 m (10,4)", "RR15/20/2", "RR15/20/2 Rechteckstahlrohr (Article uniq. sur demande)", 0.0, 12),
]
FERRURES = [
    ("1 pce (1)", "", "FFR.298F.ND.A Serrure ISEO Multiblindo EASY DIN G 1860x24x6", 264.69, 1),
    ("1 pce (1)", "", "FFR.307F.ND.A Gâche médiane serrure ISEO DIN D", 15.07, 1),
    ("2 pce (2)", "", "FFR.294F.ND.A Gâche serrure ISEO point haut et bas", 14.42, 2),
    ("3 UV á 2 pce (6)", "907662", "paumelle à souder", 21.99, 6),
    ("1 pce (1)", "917031", "Set de rosace de protection pour CP, en INOX", 159.91, 1),
    ("1 pce (1)", "987454", "serrure à verrouillage", 0.0, 1),
]
ACCESSOIRES = [
    ("1 UV á 10 pce (4)", "987081", "plaque de fixation RC/E30/EW30", 2.66, 10),
    ("1 pce (1)", "987039", "Garniture fixation 1 vantail", 81.07, 1),
]
JOINTS = [
    ("1 UV á 100 m (31,52)", "975301", "door buffer seal, EPDM, black, omnia (Non tenu stock)", 1.61, 100),
    ("1 UV á 50 m (11,21)", "988006", "bande en pe 8mm (Non tenu stock)", 0.57, 50),
    ("1 UV á 25 m (25)", "FFR103FND", "Bande TESA rouleau 25m pour CR4", 1.24, 25),
]
TOTAL_OFFRE = 3600.00


def tableau(titre, lignes, styles):
    entete = ["Quantité (nécess.)", "Numéro", "Description", "Prix [EUR]", "Total [EUR]"]
    corps = [[q, n, Paragraph(d, styles["BodyText"]), euros(p), euros(p * f)] for q, n, d, p, f in lignes]
    somme = sum(p * f for *_, p, f in lignes)
    t = Table([entete] + corps + [["Somme:", "", "", "", euros(somme)]], colWidths=[36 * mm, 24 * mm, 70 * mm, 22 * mm, 24 * mm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black), ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return [Paragraph(f"<b>{titre}</b>", styles["Heading4"]), t, Spacer(1, 4 * mm)], somme


def generer() -> Path:
    styles = getSampleStyleSheet()
    bleu = styles["BodyText"].clone("bleu", textColor=colors.HexColor("#1f3fbf"))
    p = lambda t, s=styles["BodyText"]: Paragraph(t, s)  # noqa: E731
    contenu = [
        p("Date : 01/10/2026"), p("<b>Fournisseur fictif — Exemple Systèmes SAS</b>"), p("CLIENT FICTIF SARL, 1 rue de l'Exemple, 00000 VILLE"),
        Spacer(1, 6 * mm), p("<b>Offre de prix</b>", styles["Heading2"]), p(f"Affaire: {AFFAIRE}"),
        p("Cette offre prévoit uniquement la fourniture des profilés et accessoires. Offre rédigée en Euros hors taxes."),
        Spacer(1, 6 * mm),
        Table([["Position", "Quantité", "Description", "Prix", "Total"], ["001", "1 Pce", "Porte 2 vtx", euros(TOTAL_OFFRE), euros(TOTAL_OFFRE)]],
              colWidths=[20 * mm, 20 * mm, 90 * mm, 22 * mm, 24 * mm],
              style=[("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black)]),
        p("<b>châssis 1670 mm x 2320 mm</b>, comprenant une porte à 2 battants."),
        p("Système: forster unico/unico XS Standard, Siliconé Ecart 12mm, Par : FB4"),
        p("Description de la porte: Porte 2 Vantaux ouvrants vers EXT DIN G"),
        p("Vitrage: 2 x préparé pour épaisseur 38mm"),
        p("Ferrures de fenêtre:<br/>1 x FFR.307F.ND.A Gâche médiane serrure ISEO DIN D<br/>1 x FFR.298F.ND.A Serrure ISEO Multiblindo EASY DIN G 1860x24x6<br/>"
          "2 x FFR.294F.ND.A Gâche serrure ISEO point haut et bas<br/>1 x 987039<br/>1 x 917031<br/>3 x 907662"),
        p("Article complémentaire:<br/>6 x 983302, 2320 mm<br/>3 x 983302, 1670 mm<br/>1 x FFR102FND, 2320 mm<br/>1 x FFR103FND, 25000 mm"),
        p("<u>Porte vitrée Unico FB6 RC4</u>", bleu),
        p("Type : Ensemble Porte vitrée 2 vantaux<br/>RC4 Unico — Porte : Hauteur max 3240mm<br/>CR4 selon rapport d'essai n° MD 00 00 00 A (fictif)<br/>"
          "FB6 selon rapport d'essai n° S 00 0000 00 / Z (fictif)", bleu),
        p("Configurations :<br/>- Ouverture : Extérieur<br/>- Sens : Din gauche<br/>- Parclose : Tube acier<br/>- Carré de 8 non chiffré", bleu),
        p("Remplissages : Vitrage 38 mm dans la porte, à confirmer suivant le PV", bleu),
        p("Inclus dans l'offre :<br/>- 4 paumelles soudées par vantail<br/>- Plat acier usiné pour le montant serrure<br/>- Adhésif TESA ACX+", bleu),
        p("Fourniture hors lot + fabrication par le métallier :<br/>- Maintien des vitrages : tube acier faisant office de parclose<br/>- Cylindre", bleu),
        Spacer(1, 4 * mm), p(f"<b>Total HT net {euros(TOTAL_OFFRE)} EUR</b>"), PageBreak(),
        p("<b>Analyse matière</b>", styles["Heading2"]), p(f"Nom affaire: {AFFAIRE}"),
    ]
    total = 0.0
    for titre, lignes in (("Profilés", PROFILES), ("Ferrure", FERRURES), ("Accessoires", ACCESSOIRES), ("Joints", JOINTS)):
        bloc, somme = tableau(titre, lignes, styles)
        contenu += bloc
        total += somme
    contenu += [p(f"<b>Total: {euros(total)}</b>"), PageBreak(),
                p("<b>Consultation vitrages/panneaux</b>", styles["Heading2"]), p(f"Affaire: {AFFAIRE}"), p("Vitrage — Epaisseur: 38 mm"),
                Table([["Position / Numéro", "Quantité", "Largeur [mm]", "Hauteur [mm]", "Surface [m²/Pce]", "Périmètre [m/Pce]"],
                       ["001 / 1", "1", "963", "2 120", "2,04", "6,17"], ["001 / 2", "1", "357", "2 120", "0,76", "4,95"],
                       ["Somme:", "2", "", "", "2,80", "11,12"]],
                      style=[("FONTSIZE", (0, 0), (-1, -1), 8), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")])]
    SimpleDocTemplate(str(SORTIE), pagesize=A4, title="Offre fictive", leftMargin=15 * mm, rightMargin=15 * mm).build(contenu)
    print(f"{SORTIE} généré (total matière {euros(total)} € pour une offre à {euros(TOTAL_OFFRE)} €)")
    return SORTIE


if __name__ == "__main__":
    generer()
