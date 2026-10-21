"""
Point d'entrée : uvicorn main:app --reload
- Interface : http://127.0.0.1:8000/
- API documentée (Swagger) : http://127.0.0.1:8000/docs
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes import router
from app.config import CLAUDE_MODEL
from app.db.database import init_db
from app.services.apprentissage import ErreurMetier
from app.services.extraction import ErreurExtraction, ErreurPDF

# Logs : jamais de contenu de PDF en clair, uniquement des métadonnées (nom, pages, tokens, coût)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
logger = logging.getLogger("audit")

PAGE = Path(__file__).parent / "app" / "static" / "index.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Audit offres Forster", version="0.4.0", lifespan=lifespan)
app.include_router(router)


# --- Erreurs : messages clairs plutôt que des plantages ---------------------------------------

@app.exception_handler(ErreurMetier)
async def _erreur_metier(_: Request, exc: ErreurMetier):
    return JSONResponse(status_code=exc.code_http, content={"detail": str(exc)})


@app.exception_handler(ErreurPDF)
async def _erreur_pdf(_: Request, exc: ErreurPDF):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ErreurExtraction)
async def _erreur_extraction(_: Request, exc: ErreurExtraction):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(RuntimeError)
async def _erreur_configuration(_: Request, exc: RuntimeError):
    # ex. clé API manquante (message explicite levé par verifier_cle_api)
    logger.error("Erreur de configuration : %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html; charset=utf-8")


@app.get("/health", tags=["Suivi"])
def health() -> dict:
    return {"statut": "ok", "modele": CLAUDE_MODEL}
