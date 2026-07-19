"""Directorio de empresas (Wikidata): parseo, cascada, caché y resiliencia.

La red se inyecta por fixture; ninguna prueba llama a Wikidata de verdad. La
espera del reintento se inyecta como no-op para que las pruebas sean instantáneas.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

from hd_scraper import directorio
from hd_scraper.config import settings

_NOOP = lambda s: None  # noqa: E731 (sleep inyectado, sin espera real)

# Respuesta SPARQL simulada de Wikidata (formato results/bindings).
FIXTURE_WD = {
    "results": {"bindings": [
        {"empresaLabel": {"value": "Klar"},
         "sitio": {"value": "https://www.klar.mx/"},
         "descripcion": {"value": "empresa fintech de servicios financieros en México"}},
        {"empresaLabel": {"value": "Kavak"},
         "sitio": {"value": "https://www.kavak.com/"},
         "descripcion": {"value": "plataforma de compraventa de autos usados"}},
        {"empresaLabel": {"value": "Google México"},   # gigante -> se filtra
         "sitio": {"value": "https://google.com.mx/"},
         "descripcion": {"value": "filial mexicana de Google"}},
        {"empresaLabel": {"value": "Q123456"},         # sin etiqueta -> se ignora
         "sitio": {"value": "https://x.example/"}},
    ]},
}
VACIO = {"results": {"bindings": []}}


# ── unidad: consulta y parseo ────────────────────────────────────────────────

def test_url_consulta_conoce_paises():
    assert "Q96" in directorio.url_consulta("México")
    assert directorio.url_consulta("Europa") == ""


def test_parse_filtra_gigantes_y_sin_label():
    emp = directorio.parse_empresas(FIXTURE_WD, vertical="todas")
    nombres = [e["nombre"] for e in emp]
    assert "Klar" in nombres and "Kavak" in nombres
    assert "Google México" not in nombres       # gigante fuera
    assert not any("Q123456" == n for n in nombres)


def test_parse_filtra_por_vertical():
    emp = directorio.parse_empresas(FIXTURE_WD, vertical="fintech")
    nombres = [e["nombre"] for e in emp]
    assert "Klar" in nombres            # descripción fintech
    assert "Kavak" not in nombres       # no es fintech


def test_buscar_nunca_lanza():
    def falla(url):
        raise RuntimeError("wikidata caída")
    assert directorio.buscar_empresas("México", "todas", falla, sleep=_NOOP) == []


# ── (1) cascada de relajación ────────────────────────────────────────────────

def test_cascada_relaja_vertical(db):
    # Ninguna empresa es "salud mental"; el filtro se amplía a "todas" (misma
    # consulta de red, solo cambia el parseo) y devuelve Klar + Kavak.
    llamadas = []
    def get(url):
        llamadas.append(url)
        return FIXTURE_WD
    r = directorio.buscar_empresas_cascada("México", "salud mental", get, db=db, sleep=_NOOP)
    assert r["ampliado"] is True and r["error"] == ""
    assert {e["nombre"] for e in r["empresas"]} == {"Klar", "Kavak"}
    assert len(llamadas) == 1


def test_cascada_escala_a_latam(db):
    # País (solo Q96) da 0; escala a toda LATAM (incluye Q739 Colombia) y trae datos.
    def get(url):
        return FIXTURE_WD if "Q739" in url else VACIO
    r = directorio.buscar_empresas_cascada("México", "todas", get, db=db, sleep=_NOOP)
    assert r["ampliado"] is True and "LATAM" in r["nivel"]
    assert r["empresas"]


def test_cascada_cero_real_sin_error(db):
    # Todo vacío de verdad (sin error de red): empresas=[] y error="".
    r = directorio.buscar_empresas_cascada("México", "todas", lambda u: VACIO, db=db, sleep=_NOOP)
    assert r["empresas"] == [] and r["error"] == ""


# ── (2) caché en SQLite (7 días) ─────────────────────────────────────────────

def test_cache_evita_segunda_llamada(db):
    llamadas = []
    def get(url):
        llamadas.append(url)
        return FIXTURE_WD
    r1 = directorio.buscar_empresas_cascada("México", "todas", get, db=db, sleep=_NOOP)
    r2 = directorio.buscar_empresas_cascada("México", "todas", get, db=db, sleep=_NOOP)
    assert r1["empresas"] and r2["empresas"]
    assert r1["cache"] is False and r2["cache"] is True
    assert len(llamadas) == 1               # la segunda se sirvió de caché


def test_cache_caducada_no_se_sirve(db):
    from datetime import datetime, timedelta, timezone
    # Sembramos una entrada de caché de hace 8 días (> 7): NO debe servirse.
    clave = directorio._clave_cache([directorio.PAIS_QID["México"]], 40)
    viejo = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    import json as _json
    db.execute(
        "INSERT INTO directorio_cache (clave, data_json, creado_en) VALUES (?, ?, ?)",
        (clave, _json.dumps(VACIO), viejo))
    assert directorio.cache_get(db, clave) is None


# ── (3) resiliencia: un reintento tras esperar ───────────────────────────────

def test_resiliencia_reintenta_una_vez(db):
    estado = {"n": 0}
    esperas = []
    def get(url):
        estado["n"] += 1
        if estado["n"] == 1:
            raise RuntimeError("bloqueo temporal")
        return FIXTURE_WD
    r = directorio.buscar_empresas_cascada(
        "México", "todas", get, db=db, sleep=lambda s: esperas.append(s))
    assert r["empresas"] and r["error"] == ""
    assert estado["n"] == 2                              # falló 1, reintentó 1
    assert esperas == [directorio.ESPERA_REINTENTO_S]    # esperó 5 s antes del reintento


def test_resiliencia_falla_dos_veces_avisa(db):
    def get(url):
        raise RuntimeError("caída total")
    r = directorio.buscar_empresas_cascada("México", "todas", get, db=db, sleep=_NOOP)
    assert r["empresas"] == [] and r["error"]


# ── endpoint ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def cli(db, monkeypatch):
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    object.__setattr__(settings, "ingest_token", "secreto-123")
    yield api, TestClient(api.app)
    object.__setattr__(settings, "ingest_token", "")


H = {"X-Ingest-Token": "secreto-123"}


def test_directorio_requiere_token(cli):
    _, c = cli
    assert c.post("/directorio", json={"region": "México"}).status_code == 401


def test_directorio_region_invalida_400(cli):
    _, c = cli
    assert c.post("/directorio", json={"region": "Europa"}, headers=H).status_code == 400


def test_directorio_acepta_latam(cli, monkeypatch):
    api, c = cli
    monkeypatch.setattr(api.directorio, "buscar_empresas_cascada",
                        lambda region, vert, getter, **kw: {
                            "empresas": directorio.parse_empresas(FIXTURE_WD, vert),
                            "ampliado": False, "nivel": "", "cache": False, "error": ""})
    r = c.post("/directorio", json={"region": "LATAM", "categoria": "Startup"}, headers=H)
    assert r.status_code == 200 and r.json()["encontradas"] == 2


def test_directorio_guarda_prospectos_y_salen_en_informe(cli, monkeypatch):
    api, c = cli
    monkeypatch.setattr(api.directorio, "buscar_empresas_cascada",
                        lambda region, vert, getter, **kw: {
                            "empresas": directorio.parse_empresas(FIXTURE_WD, vert),
                            "ampliado": True, "nivel": "toda LATAM · todas las verticales",
                            "cache": False, "error": ""})
    r = c.post("/directorio", json={"region": "México", "categoria": "Startup",
                                    "vertical": "todas", "limite": 40}, headers=H)
    assert r.status_code == 200
    d = r.json()
    assert d["nuevos"] == 2 and d["encontradas"] == 2
    assert d["ampliado"] is True and "filtro ampliado" in d["nota"]
    inf = c.get("/informe").json()
    empresas = {t["empresa"] for t in inf["prospectos"]}
    assert "Klar" in empresas and "Kavak" in empresas


def test_directorio_error_red_avisa(cli, monkeypatch):
    api, c = cli
    monkeypatch.setattr(api.directorio, "buscar_empresas_cascada",
                        lambda region, vert, getter, **kw: {
                            "empresas": [], "ampliado": False, "nivel": "",
                            "cache": False, "error": "timeout"})
    r = c.post("/directorio", json={"region": "México", "categoria": "Startup"}, headers=H)
    assert r.status_code == 200
    assert r.json()["encontradas"] == 0 and "no respondió" in r.json()["nota"]
