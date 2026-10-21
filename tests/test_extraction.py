"""
Tests de l'extraction sans appel API : conversion des nombres et repli en JSON libre
quand l'API refuse le schéma strict (« compiled grammar is too large »).
"""
import json
from types import SimpleNamespace

import pytest

from app.services import extraction
from app.services.extraction import lire_nombre


@pytest.mark.parametrize("valeur, attendu", [
    ("1384.73", 1384.73), ("1 384,73", 1384.73), ("1.384,73", 1384.73), ("12 €", 12.0),
    ("0,00", 0.0), (7, 7.0), ("", None), ("illisible", None), (None, None),
])
def test_lire_nombre(valeur, attendu):
    assert lire_nombre(valeur) == attendu


def test_nombre_illisible_marque_a_verifier():
    donnees = {"total_ht": {"valeur": "3 5?2,68", "texte_source": "Total", "a_verifier": False}}
    extraction._convertir_nombres(donnees)
    assert donnees["total_ht"] == {"valeur": None, "texte_source": "Total", "a_verifier": True}


def test_schema_sans_regex():
    assert '"pattern"' not in json.dumps(extraction.SCHEMA_EXTRACTION)


class RefusSchema(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def test_repli_json_libre_si_schema_refuse(monkeypatch):
    monkeypatch.setattr(extraction.anthropic, "BadRequestError", RefusSchema, raising=False)
    monkeypatch.setattr(extraction, "_SCHEMA_REFUSE", False)
    appels = []

    def create(**kwargs):
        appels.append("output_config" in kwargs)
        if "output_config" in kwargs:
            raise RefusSchema("The compiled grammar is too large, which would cause performance issues.")
        texte = '```json\n{"offre": {"positions": [{"numero": "001", "total": "3 522,68"}]}}\n```'
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=texte)],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=50))

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    donnees, t_in, t_out = extraction._appeler_claude(client, b"%PDF-1.4", "Extrais")
    assert appels == [True, False]  # schéma strict tenté, puis JSON libre
    assert donnees["offre"]["positions"][0]["total"] == 3522.68
    assert donnees["analyse_matiere"]["lignes"] == [] and donnees["observations"] == []  # sections complétées
    # appel suivant : directement en JSON libre (pas de nouvel essai refusé)
    extraction._appeler_claude(client, b"%PDF-1.4", "Extrais")
    assert appels == [True, False, False]
