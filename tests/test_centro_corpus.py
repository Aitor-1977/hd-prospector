"""Tests: Centro de Inteligencia Comercial + Corpus + nuevas verticales."""
import importlib

import pytest
from fastapi.testclient import TestClient

from hd_scraper.config import settings
from hd_scraper.discovery import VERTICALES_HD


@pytest.fixture()
def client(db, monkeypatch):
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    object.__setattr__(settings, "ingest_token", "test-tok")
    yield TestClient(api.app)
    object.__setattr__(settings, "ingest_token", "")


def _insertar_evidencia(db, empresa="Acme Corp", tipo="contratacion", dia=""):
    from hd_scraper.db.models import ahora_iso
    import hashlib
    ahora = dia or ahora_iso()
    h = hashlib.sha256(f"{empresa}|{tipo}|{ahora}".encode()).hexdigest()[:32]
    db.execute(
        "INSERT INTO evidencias (cita_textual, fecha_extraccion, url_fuente, "
        "nombre_medio, empresa_mencionada, tipo_evento, origen_declaracion, "
        "hash_dedup, connector, estado, keywords, confianza, creado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"{empresa} busca head of growth para retention", ahora,
         "https://example.com/article", "TechMedio", empresa, tipo,
         "prensa", h, "google_news", "ok",
         '["friccion_retencion","crecimiento"]', 0.7, ahora),
    )


# ── Nuevas verticales ───────────────────────────────────────────────────────

def test_verticales_incluyen_hrtech():
    assert "hrtech" in VERTICALES_HD
    assert "recursos humanos" in VERTICALES_HD["hrtech"]


def test_verticales_incluyen_saas_b2b():
    assert "saas_b2b" in VERTICALES_HD
    assert "SaaS B2B" in VERTICALES_HD["saas_b2b"]


def test_verticales_incluyen_climatetech():
    assert "climatetech" in VERTICALES_HD
    assert "climatetech" in VERTICALES_HD["climatetech"]


def test_verticales_hd_set_sync():
    from hd_scraper.analisis import VERTICALES_HD_SET
    for v in ("hrtech", "saas_b2b", "climatetech"):
        assert v in VERTICALES_HD_SET


def test_matiz_vertical_nuevas():
    from hd_scraper.analisis import MATIZ_VERTICAL
    for v in ("hrtech", "saas_b2b", "climatetech"):
        assert v in MATIZ_VERTICAL


def test_queries_para_nuevas_verticales():
    from hd_scraper.discovery import queries_para
    for v in ("hrtech", "saas_b2b", "climatetech"):
        qs = queries_para("Startup", "ronda", v)
        assert len(qs) >= 1
        assert v != "hrtech" or "recursos humanos" in qs[0][0]


def test_analizar_vertical_hrtech():
    from hd_scraper.analisis import analizar
    r = analizar(["friccion_retencion"], vertical="hrtech", confianza=0.8)
    assert r["scoring"] == "A"
    assert "hrtech" in r["razon"]


def test_analizar_vertical_climatetech():
    from hd_scraper.analisis import analizar
    r = analizar(["ronda_inversion"], vertical="climatetech", confianza=0.6)
    assert r["scoring"] == "B"
    assert r["score_icp"] > 25


# ── GET /centro ─────────────────────────────────────────────────────────────

def test_centro_sin_datos(client):
    r = client.get("/centro")
    assert r.status_code == 200
    d = r.json()
    assert d["fecha"]
    assert d["nuevas_hoy"]["total"] == 0
    assert d["cambio_narrativa"]["total"] == 0
    assert d["mayor_dolor"]["total"] == 0
    assert d["califican_peritaje"]["total"] == 0
    assert d["califican_dolormap"]["total"] == 0
    assert d["seguimiento_semanal"]["total"] == 0
    assert "resumen" in d


def test_centro_con_evidencias_hoy(client, db):
    _insertar_evidencia(db)
    r = client.get("/centro")
    d = r.json()
    assert d["nuevas_hoy"]["total"] >= 1
    assert "Acme Corp" in d["nuevas_hoy"]["organizaciones"]


def test_centro_mayor_dolor_scoring_a(client, db):
    _insertar_evidencia(db)
    r = client.get("/centro")
    d = r.json()
    if d["mayor_dolor"]["total"] > 0:
        for org in d["mayor_dolor"]["organizaciones"]:
            assert org["scoring"] == "A"


def test_centro_estructura_resumen(client, db):
    _insertar_evidencia(db)
    r = client.get("/centro")
    d = r.json()
    res = d["resumen"]
    assert "total_expedientes" in res
    assert "scoring_a" in res
    assert "scoring_b" in res
    assert "scoring_c" in res
    assert "en_pipeline" in res


def test_centro_seguimiento_semanal(client, db):
    from hd_scraper.db.models import ahora_iso
    import datetime as _dt
    hace_10 = (_dt.date.today() - _dt.timedelta(days=10)).isoformat() + "T00:00:00"
    import hashlib
    h = hashlib.sha256(b"vieja-org").hexdigest()[:32]
    db.execute(
        "INSERT INTO pipeline_comercial (org_nombre, etapa, notas, resultado, hash_dedup, creado_en, actualizado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("OrgVieja", "vigilancia", "pendiente", "", h, hace_10, hace_10),
    )
    r = client.get("/centro")
    d = r.json()
    assert d["seguimiento_semanal"]["total"] >= 1
    org = d["seguimiento_semanal"]["organizaciones"][0]
    assert org["nombre"] == "OrgVieja"
    assert org["dias_sin_mover"] >= 10


def test_centro_califican_peritaje(client, db):
    _insertar_evidencia(db)
    r = client.get("/centro")
    d = r.json()
    for org in d["califican_peritaje"].get("organizaciones", []):
        assert org["scoring"] in ("A", "B")
        assert org["score_icp"] >= 40


def test_centro_califican_dolormap(client, db):
    _insertar_evidencia(db)
    r = client.get("/centro")
    d = r.json()
    for org in d["califican_dolormap"].get("organizaciones", []):
        assert org["scoring"] == "A"
        assert org["score_icp"] >= 50


# ── POST /corpus/poblar ─────────────────────────────────────────────────────

def test_corpus_requiere_token(client):
    r = client.post("/corpus/poblar", json={})
    assert r.status_code == 401


def test_corpus_vertical_invalida(client):
    r = client.post("/corpus/poblar",
                    json={"verticales": ["inexistente"]},
                    headers={"X-Ingest-Token": "test-tok"})
    assert r.status_code == 400


def test_corpus_region_invalida(client):
    r = client.post("/corpus/poblar",
                    json={"region": "Europa"},
                    headers={"X-Ingest-Token": "test-tok"})
    assert r.status_code == 400


def test_corpus_constantes():
    from hd_scraper.api.app import CORPUS_VERTICALES, CORPUS_TIPOS
    assert set(CORPUS_VERTICALES) == {"fintech", "healthtech", "hrtech", "saas_b2b", "climatetech"}
    assert len(CORPUS_TIPOS) == 5
