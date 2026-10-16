"""
Point d'entrée : uvicorn main:app --reload
Étape 1 : création de la base + endpoint de santé. Les routes d'analyse,
de règles et de validation arrivent aux étapes suivantes.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import CLAUDE_MODEL
from app.db.database import init_db

# Logs : jamais de contenu de PDF en clair, uniquement des métadonnées (nom, taille, tokens)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Audit offres Forster", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"statut": "ok", "modele": CLAUDE_MODEL}
