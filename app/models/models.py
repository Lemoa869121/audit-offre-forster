"""
Modèles de données (SQLAlchemy 2.0).

Principe : la "mémoire" de l'application, c'est cette base. Le modèle Claude
reste figé ; ce sont les règles, prix de référence et décisions humaines
stockés ici qui s'enrichissent à chaque analyse.

Choix importants :
- Aucune suppression physique : règles et prix ont un statut actif/archive.
  Modifier une règle = archiver la version courante + créer une nouvelle
  version qui pointe vers l'ancienne (champ `remplace_id`).
- `utilisateur_id` partout : inutilisé en Phase 1 (valeur "local"), mais
  permettra d'isoler les données par utilisateur/équipe si hébergement.
- Les colonnes JSON utilisent le type générique `JSON` de SQLAlchemy :
  stocké en TEXT sous SQLite, natif sous PostgreSQL. Migration sans changement.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Enum, Float, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UTILISATEUR_PAR_DEFAUT = "local"


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --- Énumérations métier -----------------------------------------------------

class TypeRegle(str, enum.Enum):
    etude = "etude"   # erreur technique (quantités, cotes, cohérence config)
    prix = "prix"     # erreur de prix (écart vs référence, calcul de total)


class Famille(str, enum.Enum):
    """Sections d'une offre Forster (remplace la notion de lot CLV/MIN/MET)."""
    profile = "profile"
    ferrure = "ferrure"
    accessoire = "accessoire"
    joint = "joint"
    offre = "offre"        # positions de l'offre de prix (châssis, portes...)
    vitrage = "vitrage"    # consultation vitrages/panneaux
    global_ = "global"     # contrôles transverses (totaux, cohérence entre sections)


class Statut(str, enum.Enum):
    actif = "actif"
    archive = "archive"


class Gravite(str, enum.Enum):
    info = "info"        # pour information (ex. chute due au conditionnement)
    alerte = "alerte"    # à regarder (ex. écart vs prix de référence)
    erreur = "erreur"    # à corriger avant envoi (ex. totaux incohérents, oubli)


class StatutAnomalie(str, enum.Enum):
    en_attente = "en_attente"
    valide = "valide"      # vraie erreur confirmée
    rejete = "rejete"      # faux positif
    corrige = "corrige"    # l'utilisateur fournit la bonne valeur


class NatureAnomalie(str, enum.Enum):
    ecart = "ecart"                          # violation d'une règle existante
    observation_ia = "observation_ia"        # repérée par Claude sans règle en base
    proposition_prix = "proposition_prix"    # référence inconnue -> proposer un prix de référence


# --- Tables -------------------------------------------------------------------

class Rule(Base):
    """Règle métier. Une ligne = une VERSION d'une règle."""
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utilisateur_id: Mapped[str] = mapped_column(String(64), default=UTILISATEUR_PAR_DEFAUT, index=True)

    # Identifiant stable partagé par toutes les versions d'une même règle
    code: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    description: Mapped[str] = mapped_column(Text)
    type: Mapped[TypeRegle] = mapped_column(Enum(TypeRegle))
    famille: Mapped[Famille] = mapped_column(Enum(Famille), default=Famille.global_)
    gravite: Mapped[Gravite] = mapped_column(Enum(Gravite), default=Gravite.erreur)

    # Condition évaluée par le moteur de comparaison (app/services/comparaison.py).
    # La clé "controle" désigne la fonction de contrôle, ex. {"controle": "quantite_par_vantail", "references": ["907662"]}
    # Le contrôle spécial "consigne_ia" n'est pas évalué en code : son texte est transmis à Claude.
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    # Seuil ou valeur attendue, ex. 15 (%) ou 76 (mm de déduction vitrage)
    seuil: Mapped[float | None] = mapped_column(Float, nullable=True)

    statut: Mapped[Statut] = mapped_column(Enum(Statut), default=Statut.actif, index=True)
    source_pdf: Mapped[str | None] = mapped_column(String(255), nullable=True)
    motif_changement: Mapped[str | None] = mapped_column(Text, nullable=True)
    remplace_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)
    date_creation: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_maintenant)


class PriceReference(Base):
    """
    Prix de référence d'un article. Même logique de versions que les règles.

    Deux types de prix (champ type_prix) :
    - "net"   : prix net observé dans un devis validé (ex. "531.731 ZM" à 31,14 €/m).
                Prioritaire : c'est le prix réellement pratiqué.
    - "tarif" : prix public du tarif fournisseur (ex. "531.731F.51.A" à 47,90 €), auquel
                le moteur applique la remise de la famille (règles REMISE_*).
                Le prix tarif peut être exprimé par unité (m, pce) ou par unité de vente (UV).
    """
    __tablename__ = "price_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utilisateur_id: Mapped[str] = mapped_column(String(64), default=UTILISATEUR_PAR_DEFAUT, index=True)

    type_prix: Mapped[str] = mapped_column(String(8), default="net", index=True)   # "net" | "tarif"
    reference_produit: Mapped[str] = mapped_column(String(64), index=True)  # ex. "531.731 ZM" (net), "531.731F.51.A" (tarif)
    designation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    famille: Mapped[Famille] = mapped_column(Enum(Famille))
    prix_reference: Mapped[float | None] = mapped_column(Float, nullable=True)  # € HT ; None = "sur demande"
    unite: Mapped[str] = mapped_column(String(16))                # "m", "pce", "UV"
    conditionnement: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ex. "UV 25 m", "barre 6 m"
    tarif_par: Mapped[str | None] = mapped_column(String(8), nullable=True)   # tarif : "unite" | "uv" | None (déduit)
    taille_uv: Mapped[float | None] = mapped_column(Float, nullable=True)     # tarif : nb d'unités par UV si connu
    tolerance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)       # None -> seuil global

    statut: Mapped[Statut] = mapped_column(Enum(Statut), default=Statut.actif, index=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)  # PDF ou fichier Excel d'origine
    date_maj: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_maintenant)


class AnalysisHistory(Base):
    """Une analyse = un PDF traité."""
    __tablename__ = "analysis_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utilisateur_id: Mapped[str] = mapped_column(String(64), default=UTILISATEUR_PAR_DEFAUT, index=True)

    nom_pdf: Mapped[str] = mapped_column(String(255))
    hash_pdf: Mapped[str] = mapped_column(String(64), index=True)  # SHA-256, détecte un PDF déjà analysé
    reference_affaire: Mapped[str | None] = mapped_column(String(128), nullable=True)
    date_analyse: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_maintenant)

    donnees_extraites: Mapped[dict] = mapped_column(JSON, default=dict)
    anomalies_detectees: Mapped[list] = mapped_column(JSON, default=list)  # instantané au moment de l'analyse

    # Décision globale sur le document (le détail est par anomalie, table ci-dessous)
    decision_utilisateur: Mapped[str | None] = mapped_column(String(16), nullable=True)
    commentaire: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Suivi des coûts API
    modele: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_entree: Mapped[int] = mapped_column(Integer, default=0)
    tokens_sortie: Mapped[int] = mapped_column(Integer, default=0)
    cout_estime_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pdf_scanne: Mapped[bool] = mapped_column(default=False)

    anomalies: Mapped[list[Anomaly]] = relationship(back_populates="analyse", cascade="all, delete-orphan")


class Anomaly(Base):
    """
    Une anomalie détectée, validable individuellement.
    (Ajout par rapport au cahier des charges : sans cette table, on ne pourrait
    valider/rejeter qu'un document entier, pas chaque ligne.)
    """
    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utilisateur_id: Mapped[str] = mapped_column(String(64), default=UTILISATEUR_PAR_DEFAUT, index=True)
    analyse_id: Mapped[int] = mapped_column(ForeignKey("analysis_history.id"), index=True)
    regle_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)

    nature: Mapped[NatureAnomalie] = mapped_column(Enum(NatureAnomalie))
    type: Mapped[TypeRegle] = mapped_column(Enum(TypeRegle))
    famille: Mapped[Famille] = mapped_column(Enum(Famille))
    gravite: Mapped[Gravite] = mapped_column(Enum(Gravite))
    code_regle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text)

    reference_produit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valeur_trouvee: Mapped[float | None] = mapped_column(Float, nullable=True)
    valeur_attendue: Mapped[float | None] = mapped_column(Float, nullable=True)
    ecart_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    texte_source: Mapped[str | None] = mapped_column(Text, nullable=True)  # traçabilité : extrait brut du PDF

    statut: Mapped[StatutAnomalie] = mapped_column(Enum(StatutAnomalie), default=StatutAnomalie.en_attente, index=True)
    valeur_corrigee: Mapped[float | None] = mapped_column(Float, nullable=True)
    commentaire: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_decision: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    analyse: Mapped[AnalysisHistory] = relationship(back_populates="anomalies")
