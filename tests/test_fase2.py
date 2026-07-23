"""Tests: Dictamen Antropológico, Ranking, Dossier, Alertas (Fase 2)."""
import importlib

import pytest
from fastapi.testclient import TestClient

from hd_scraper.config import settings
from hd_scraper.dictamen import (
    generar_dictamen,
    generar_ranking,
    _score_compuesto,
    _generar_hallazgos,
    _generar_hipotesis,
    _generar_alertas,
)


@pytest.fixture()
def client(db, monkeypatch):
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    object.__setattr__(settings, "ingest_token", "test-tok")
    yield TestClient(api.app)
    object.__setattr__(settings, "ingest_token", "")


def _exp(nombre="Acme", scoring="A", score_icp=70, tipo_deuda="Deuda Relacional",
         deuda_secundaria="", intensidad="Alta", total_evidencias=5,
         keywords=None, patrones=None, vertical="fintech", categoria="Startup",
         decisor_sugerido="Head of CX", angulo_conversacion="abrir por la experiencia",
         deuda_razon="fricción", senal_dominante="friccion_retencion"):
    return {
        "nombre": nombre,
        "scoring": scoring,
        "score_icp": score_icp,
        "tipo_deuda": tipo_deuda,
        "deuda_secundaria": deuda_secundaria,
        "deuda_razon": deuda_razon,
        "intensidad": intensidad,
        "total_evidencias": total_evidencias,
        "keywords": keywords or ["friccion_retencion", "crecimiento"],
        "patrones": patrones or [{"patron": "dolor+crecimiento", "razonamiento": "test"}],
        "vertical": vertical,
        "categoria": categoria,
        "decisor_sugerido": decisor_sugerido,
        "angulo_conversacion": angulo_conversacion,
        "senal_dominante": senal_dominante,
    }


# ── generar_dictamen ──────────────────────────────────────────────────────

def test_dictamen_vacio():
    d = generar_dictamen([])
    assert d["resumen_ejecutivo"]["organizaciones_analizadas"] == 0
    assert d["ranking"] == []
    assert isinstance(d["hallazgos"], list)
    assert isinstance(d["alertas"], list)


def test_dictamen_completo():
    exps = [_exp("A"), _exp("B", scoring="B", score_icp=40, tipo_deuda="Deuda Moral",
                            keywords=["reduccion_personal"], intensidad="Media")]
    d = generar_dictamen(exps, query="fintech", region="LATAM", vertical="fintech",
                         tiempo_s=12.5, total_escritos=20, total_vistos=100)
    re = d["resumen_ejecutivo"]
    assert re["organizaciones_analizadas"] == 2
    assert re["scoring"]["A"] == 1
    assert re["scoring"]["B"] == 1
    assert re["query"] == "fintech"
    assert re["region"] == "LATAM"
    assert len(d["ranking"]) == 2
    assert d["ranking"][0]["posicion"] == 1
    assert len(d["hallazgos"]) >= 1
    assert d["hipotesis"]["texto"]


def test_dictamen_resumen_incluye_noticias_senales():
    d = generar_dictamen([_exp()], noticias_senales=42)
    assert d["resumen_ejecutivo"]["senales_rss"] == 42


# ── generar_ranking ───────────────────────────────────────────────────────

def test_ranking_orden():
    exps = [
        _exp("Lo", scoring="C", score_icp=10, total_evidencias=1, patrones=[], intensidad="Baja"),
        _exp("Hi", scoring="A", score_icp=90, total_evidencias=10, intensidad="Alta"),
    ]
    r = generar_ranking(exps)
    assert r[0]["nombre"] == "Hi"
    assert r[1]["nombre"] == "Lo"
    assert r[0]["score_compuesto"] > r[1]["score_compuesto"]


def test_ranking_limite():
    exps = [_exp(f"Org{i}") for i in range(20)]
    r = generar_ranking(exps, limite=5)
    assert len(r) == 5
    assert r[0]["posicion"] == 1
    assert r[4]["posicion"] == 5


def test_ranking_incluye_motivos():
    r = generar_ranking([_exp(scoring="A", score_icp=80)])
    assert len(r[0]["motivos"]) >= 1
    assert any("Scoring A" in m for m in r[0]["motivos"])


def test_ranking_incluye_deuda_y_angulo():
    r = generar_ranking([_exp()])
    item = r[0]
    assert item["tipo_deuda"] == "Deuda Relacional"
    assert item["angulo_conversacion"]
    assert item["decisor_sugerido"]


# ── _score_compuesto ──────────────────────────────────────────────────────

def test_score_compuesto_a_mayor_que_c():
    sa = _score_compuesto(_exp(scoring="A"))
    sc = _score_compuesto(_exp(scoring="C", tipo_deuda="", intensidad="Baja", score_icp=10, patrones=[]))
    assert sa > sc


def test_score_compuesto_con_deuda_secundaria():
    sin = _score_compuesto(_exp(deuda_secundaria=""))
    con = _score_compuesto(_exp(deuda_secundaria="Deuda Moral"))
    assert con > sin


# ── _generar_hallazgos ────────────────────────────────────────────────────

def test_hallazgos_sin_datos():
    h = _generar_hallazgos([])
    assert h[0]["tipo"] == "sin_datos"


def test_hallazgos_detecta_dolor():
    h = _generar_hallazgos([_exp(keywords=["friccion_retencion"])])
    tipos = [x["tipo"] for x in h]
    assert "dolor_ecosistema" in tipos


def test_hallazgos_detecta_cambio():
    h = _generar_hallazgos([_exp(keywords=["ronda_inversion"])])
    tipos = [x["tipo"] for x in h]
    assert "cambio_ecosistema" in tipos


def test_hallazgos_detecta_deuda_dominante():
    exps = [_exp(tipo_deuda="Deuda Relacional"), _exp(tipo_deuda="Deuda Relacional")]
    h = _generar_hallazgos(exps)
    tipos = [x["tipo"] for x in h]
    assert "deuda_dominante" in tipos


def test_hallazgos_concentracion_vertical():
    exps = [_exp(vertical="fintech"), _exp(vertical="fintech")]
    h = _generar_hallazgos(exps)
    tipos = [x["tipo"] for x in h]
    assert "concentracion_vertical" in tipos


# ── _generar_hipotesis ────────────────────────────────────────────────────

def test_hipotesis_sin_datos():
    hip = _generar_hipotesis([])
    assert hip["confianza"] == "baja"
    assert hip["deuda_dominante"] == ""


def test_hipotesis_sin_deuda():
    hip = _generar_hipotesis([_exp(tipo_deuda="")])
    assert hip["confianza"] == "baja"


def test_hipotesis_confianza_alta():
    exps = [_exp(tipo_deuda="Deuda Relacional")] * 5
    hip = _generar_hipotesis(exps)
    assert hip["confianza"] == "alta"
    assert hip["deuda_dominante"] == "Deuda Relacional"
    assert hip["angulo"]


def test_hipotesis_confianza_media():
    exps = [_exp(tipo_deuda="Deuda Relacional")] * 2 + [_exp(tipo_deuda="Deuda Moral")] * 3 + [_exp(tipo_deuda="")] * 5
    hip = _generar_hipotesis(exps)
    assert hip["confianza"] in ("media", "baja")


def test_hipotesis_distribucion():
    exps = [_exp(tipo_deuda="Deuda Relacional"), _exp(tipo_deuda="Deuda Moral")]
    hip = _generar_hipotesis(exps)
    assert "distribucion" in hip
    assert len(hip["distribucion"]) >= 1


# ── _generar_alertas ──────────────────────────────────────────────────────

def test_alertas_vacio():
    assert _generar_alertas([]) == []


def test_alertas_detecta_dolor_intenso():
    alertas = _generar_alertas([_exp(scoring="A", intensidad="Alta")])
    assert len(alertas) == 1
    assert "dolor intenso detectado" in alertas[0]["razones"]


def test_alertas_detecta_doble_deuda():
    alertas = _generar_alertas([_exp(deuda_secundaria="Deuda Moral")])
    razones = alertas[0]["razones"]
    assert any("dos tipos de deuda" in r for r in razones)


def test_alertas_detecta_evidencias_altas():
    alertas = _generar_alertas([_exp(total_evidencias=8)])
    razones = alertas[0]["razones"]
    assert any("evidencias" in r for r in razones)


def test_alertas_incluye_accion():
    alertas = _generar_alertas([_exp(scoring="A", score_icp=60)])
    assert alertas[0]["accion_sugerida"]


def test_alertas_max_20():
    exps = [_exp(f"Org{i}", scoring="A", intensidad="Alta") for i in range(30)]
    assert len(_generar_alertas(exps)) <= 20


# ── GET /dossier/{org} ────────────────────────────────────────────────────

def _insertar_evidencia_dossier(db, empresa="TestOrg"):
    from hd_scraper.db.models import ahora_iso
    import hashlib
    ahora = ahora_iso()
    h = hashlib.sha256(f"{empresa}|dossier|{ahora}".encode()).hexdigest()[:32]
    db.execute(
        "INSERT INTO evidencias (cita_textual, fecha_extraccion, url_fuente, "
        "nombre_medio, empresa_mencionada, tipo_evento, origen_declaracion, "
        "hash_dedup, connector, estado, keywords, confianza, creado_en) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"{empresa} tiene fricción de retención", ahora,
         "https://example.com/article", "TechMedio", empresa, "contratacion",
         "prensa", h, "google_news", "ok",
         '["friccion_retencion"]', 0.8, ahora),
    )


def test_dossier_endpoint_devuelve_html(client, db):
    _insertar_evidencia_dossier(db)
    r = client.get("/dossier/TestOrg")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Dossier de Inteligencia" in r.text
    assert "TestOrg" in r.text


def test_dossier_incluye_preguntas(client, db):
    _insertar_evidencia_dossier(db)
    r = client.get("/dossier/TestOrg")
    assert "Preguntas antropológicas" in r.text


def test_dossier_incluye_secciones_clave(client, db):
    _insertar_evidencia_dossier(db)
    r = client.get("/dossier/TestOrg")
    assert "Señales clave" in r.text or "Dolor Cultural" in r.text
    assert "Evidencias" in r.text


def test_dossier_org_sin_datos(client, db):
    r = client.get("/dossier/Inexistente")
    assert r.status_code == 200
    assert "Inexistente" in r.text


# ── GET /alertas ──────────────────────────────────────────────────────────

def test_alertas_endpoint_vacio(client):
    r = client.get("/alertas")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 0
    assert d["alertas"] == []


def test_alertas_endpoint_con_datos(client, db):
    _insertar_evidencia_dossier(db)
    r = client.get("/alertas")
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["total"], int)
    assert isinstance(d["alertas"], list)


# ── POST /investigacion incluye dictamen ──────────────────────────────────

def test_investigacion_incluye_dictamen(client, monkeypatch):
    api_mod = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api_mod, "_correr_query", lambda *a, **kw: [])
    monkeypatch.setattr(api_mod, "_construir_expedientes", lambda *a, **kw: {"expedientes": []})
    r = client.post("/investigacion",
                    json={"query": "fintech México", "presupuesto_s": 10},
                    headers={"X-Ingest-Token": "test-tok"})
    assert r.status_code == 200
    d = r.json()
    assert "dictamen" in d
    dict_data = d["dictamen"]
    assert "resumen_ejecutivo" in dict_data
    assert "hallazgos" in dict_data
    assert "hipotesis" in dict_data
    assert "ranking" in dict_data
    assert "alertas" in dict_data


# ── UI muestra Dossier button ─────────────────────────────────────────────

def test_admin_ui_incluye_dossier(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Dossier" in r.text
    assert "renderDictamen" in r.text
