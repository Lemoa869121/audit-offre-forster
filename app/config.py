"""
Configuration lue depuis les variables d'environnement (fichier .env en local).
La clé API n'est JAMAIS écrite dans le code : uniquement dans .env (ignoré par git).
"""
import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./audit_offres.db")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

# Seuil de tolérance global sur les écarts de prix (%), surchargeable par article
TOLERANCE_PRIX_PCT: float = float(os.getenv("TOLERANCE_PRIX_PCT", "15"))
# Au-delà, le PDF est découpé en plusieurs appels API
MAX_PAGES_PAR_APPEL: int = int(os.getenv("MAX_PAGES_PAR_APPEL", "100"))


def verifier_cle_api() -> str:
    """Renvoie la clé ou lève une erreur explicite si elle manque."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY manquante : copiez .env.example en .env et renseignez votre clé."
        )
    return ANTHROPIC_API_KEY
