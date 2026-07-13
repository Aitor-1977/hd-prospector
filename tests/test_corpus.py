"""Endpoint /corpus: contrato estable Motor A → Motor B (RadarHD)."""
import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hd_scraper.config import settings
from hd_scraper.connectors.google_news import GoogleNewsConnector
from hd_scraper.db.models import QuerySpec
from hd_scraper.pipeline import run_connector
from tests.test_google_news import FIXTURE_RSS


@pytest.fixture()
def cli(db, monkeypatch):
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    object.__setattr__(settings, "min_interval_s", 0.0)
    c = GoogleNewsConnector()
    with patch.object(c, "_get", lambda url: FIXTURE_RSS):
        run_connector(db, c, QuerySpec(empresa="Nubank", tipo_evento="ronda"))
    return TestClient(api.app)


def test_corpus_contrato_solo_hechos(cli):
    r = cli.get("/corpus")
    assert r.status_code == 200
    d = r.json()
    assert d["contrato"] == "motor_a.corpus.v1"
    assert d["total"] >= 1
    item = d["items"][0]
    # Campos del contrato: solo hechos observables.
    assert set(item) == {"empresa", "fuente", "fecha", "texto", "url",
                         "keywords", "confianza", "categoria", "tipo_evento", "hash"}
    # NADA de Deuda Cultural / ICP / hipótesis (eso es Motor B).
    assert "deuda_cultural" not in item and "icp" not in item
    assert isinstance(item["keywords"], list)
    assert isinstance(item["confianza"], (int, float))


def test_corpus_filtra_min_confianza(cli):
    total = cli.get("/corpus").json()["total"]
    imposible = cli.get("/corpus", params={"min_confianza": 1.0}).json()["total"]
    assert imposible <= total


def test_corpus_valida_categoria(cli):
    assert cli.get("/corpus", params={"categoria": "Fondo"}).status_code == 400
