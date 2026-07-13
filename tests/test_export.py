"""Exportación de prospectos a CSV / JSON."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from hd_scraper.prospectos import nuevo_prospecto, upsert_prospecto


@pytest.fixture()
def cli(db, monkeypatch):
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    upsert_prospecto(db, nuevo_prospecto("Kaszek", "VC", discurso_corporativo="Tesis, LatAm; con coma"))
    upsert_prospecto(db, nuevo_prospecto("Nubank", "Startup"))
    return TestClient(api.app)


def test_export_csv(cli):
    r = cli.get("/prospectos/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "prospectos_todos.csv" in r.headers["content-disposition"]
    texto = r.text
    assert "nombre,categoria" in texto  # cabecera
    assert "Kaszek" in texto and "Nubank" in texto
    # La coma del discurso no rompe columnas (csv la entrecomilla).
    assert '"Tesis, LatAm; con coma"' in texto


def test_export_json(cli):
    r = cli.get("/prospectos/export.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    datos = json.loads(r.text)
    nombres = {p["nombre"] for p in datos}
    assert {"Kaszek", "Nubank"} <= nombres


def test_export_filtra_categoria(cli):
    r = cli.get("/prospectos/export.csv", params={"categoria": "VC"})
    assert "Kaszek" in r.text and "Nubank" not in r.text
    assert "prospectos_VC.csv" in r.headers["content-disposition"]


def test_export_categoria_invalida_400(cli):
    assert cli.get("/prospectos/export.csv", params={"categoria": "Fondo"}).status_code == 400


def test_export_no_lo_captura_ruta_id(cli):
    # /prospectos/export.csv NO debe ser interpretada como /prospectos/{id}.
    assert cli.get("/prospectos/export.csv").status_code == 200
    # y un id numérico inexistente sí es 404 (ruta {id} intacta).
    assert cli.get("/prospectos/999999").status_code == 404
