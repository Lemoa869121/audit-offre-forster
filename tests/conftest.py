"""
Configuration des tests : base SQLite temporaire, et AUCUN appel réel à l'API Claude
(la clé éventuellement présente dans .env est neutralisée dans les tests d'API).
"""
import os
import sys
import tempfile
import types

# Base de test isolée : doit être définie AVANT tout import de app.config / app.db
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="audit_tests_"), "test.db")

# Le SDK anthropic n'est pas nécessaire pour les tests (l'extraction est simulée) ;
# s'il n'est pas installé, on fournit un module minimal pour pouvoir importer l'application.
try:
    import anthropic  # noqa: F401
except ImportError:
    module = types.ModuleType("anthropic")
    for nom in ("Anthropic", "AuthenticationError", "RateLimitError", "BadRequestError", "APIError"):
        setattr(module, nom, type(nom, (Exception,), {}))
    sys.modules["anthropic"] = module
