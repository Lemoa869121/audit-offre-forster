"""
Connexion à la base. Passer à PostgreSQL = changer DATABASE_URL dans .env
(ex. postgresql+psycopg://user:mdp@localhost/audit) — aucun changement de code.

Choix : pas d'Alembic en Phase 1. `create_all` suffit tant que le schéma bouge
souvent ; on ajoutera Alembic au moment de figer le schéma ou de migrer vers PostgreSQL.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL
from app.models import Base

# check_same_thread=False : nécessaire pour SQLite avec FastAPI (plusieurs threads)
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Crée les tables si elles n'existent pas."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : une session par requête, fermée à la fin."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
