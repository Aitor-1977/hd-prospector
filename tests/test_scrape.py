"""Pruebas del scraping bajo demanda (POST /scrape)."""
import importlib

import pytest
from fastapi.testclient import TestClient

from hd_scraper.config import settings
from tests.test_google_news import FIXTURE_RSS


@pytest.fixture()
def cli(db, monkeypatch):
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    object.__setattr__(settings, "ingest_token", "secreto-123")
    # Evita red: el conector Google News devuelve un fixture.
    from hd_scraper.connectors.google_news import GoogleNewsConnector
    monkeypatch.setattr(GoogleNewsConnector, "_get", lambda self, url: FIXTURE_RSS)
    yield TestClient(api.app)
    object.__setattr__(settings, "ingest_token", "")


H = {"X-Ingest-Token": "secreto-123"}


def test_scrape_requiere_token(cli):
    r = cli.post("/scrape", json={"empresa": "Nubank"})
    assert r.status_code == 401


def test_scrape_tipo_invalido_400(cli):
    r = cli.post("/scrape", json={"empresa": "Nubank", "tipo_evento": "opinion"}, headers=H)
    assert r.status_code == 400


def test_scrape_escribe_evidencias(cli, db):
    r = cli.post("/scrape", json={"empresa": "Nubank", "tipo_evento": "ronda",
                                  "connectors": ["google_news"]}, headers=H)
    assert r.status_code == 200
    d = r.json()
    assert d["total_escritos"] == 2
    assert db.fetch_one("SELECT COUNT(*) n FROM evidencias")["n"] == 2
    # Segunda corrida: dedup, no duplica.
    r2 = cli.post("/scrape", json={"empresa": "Nubank", "tipo_evento": "ronda",
                                   "connectors": ["google_news"]}, headers=H)
    assert r2.json()["total_escritos"] == 0
    assert db.fetch_one("SELECT COUNT(*) n FROM evidencias")["n"] == 2


def test_scrape_rechaza_conector_no_apto(cli):
    # job_boards no está en la lista de scraping bajo demanda.
    r = cli.post("/scrape", json={"empresa": "Nubank", "connectors": ["job_boards"]}, headers=H)
    assert r.status_code == 200
    assert r.json()["resultados"][0]["error"]


def test_scrape_luego_evidencias_visibles(cli):
    cli.post("/scrape", json={"empresa": "Nubank", "tipo_evento": "ronda",
                              "connectors": ["google_news"]}, headers=H)
    r = cli.get("/evidencias", params={"empresa": "Nubank"})
    # Al menos una evidencia fechada (consumible) queda visible para curar.
    assert r.json()["total"] >= 1


def test_scrape_por_categoria_etiqueta_ecosistema(cli, db):
    # Modo descubrimiento: corre las consultas temáticas del ecosistema VC.
    r = cli.post("/scrape", json={"categoria": "VC"}, headers=H)
    assert r.status_code == 200
    assert r.json()["modo"] == "categoria"
    # La evidencia queda etiquetada con la categoría y es filtrable por ecosistema.
    fila = db.fetch_one("SELECT categoria FROM evidencias LIMIT 1")
    assert fila["categoria"] == "VC"
    assert cli.get("/evidencias", params={"categoria": "VC"}).json()["total"] >= 1


def test_scrape_categoria_invalida_400(cli):
    assert cli.post("/scrape", json={"categoria": "Fondo"}, headers=H).status_code == 400


def test_scrape_categoria_respeta_tipo(cli, db):
    r = cli.post("/scrape", json={"categoria": "Startup", "tipo_evento": "despido"}, headers=H)
    assert r.status_code == 200 and r.json()["tipo_evento"] == "despido"
    fila = db.fetch_one("SELECT tipo_evento, categoria FROM evidencias LIMIT 1")
    assert fila["tipo_evento"] == "despido" and fila["categoria"] == "Startup"


def test_scrape_categoria_tipo_invalido_400(cli):
    r = cli.post("/scrape", json={"categoria": "VC", "tipo_evento": "opinion"}, headers=H)
    assert r.status_code == 400


def test_scrape_region_por_defecto_latam(cli):
    r = cli.post("/scrape", json={"categoria": "VC"}, headers=H)
    assert r.status_code == 200 and r.json()["region"] == "LATAM"


def test_scrape_region_pais(cli):
    r = cli.post("/scrape", json={"categoria": "VC", "region": "Colombia"}, headers=H)
    assert r.status_code == 200 and r.json()["region"] == "Colombia"


def test_scrape_region_invalida_400(cli):
    r = cli.post("/scrape", json={"categoria": "VC", "region": "Europa"}, headers=H)
    assert r.status_code == 400


def test_scrape_sin_empresa_ni_categoria_400(cli):
    assert cli.post("/scrape", json={}, headers=H).status_code == 400
