import json

import httpx

from hd_scraper.connectors.job_boards import (
    JobBoardsConnector,
    _iso_or_none,
    _ms_a_iso,
    _parse_ashby,
    _parse_greenhouse,
    _parse_lever,
)
from hd_scraper.connectors import REGISTRY
from hd_scraper.db.models import ESTADO_NO_FECHADO, ESTADO_OK, QuerySpec
from hd_scraper.pipeline import run_connector

GREENHOUSE_JSON = json.dumps({"jobs": [
    {"title": "Ingeniero Backend", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
     "updated_at": "2026-07-01T10:00:00-04:00", "location": {"name": "CDMX"}},
    {"title": "Vacante sin fecha", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
     "location": {"name": "Remote"}},
]})


def _raise_404(url: str):
    req = httpx.Request("GET", url)
    raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))


def _fake_get(url: str) -> str:
    if "greenhouse" in url:
        return GREENHOUSE_JSON
    if "lever" in url:
        _raise_404(url)          # ese slug no está en Lever -> se salta, sin fallo
    raise RuntimeError("ashby caído")  # Ashby down -> fallo de salud


def _connector(monkeypatch) -> JobBoardsConnector:
    c = JobBoardsConnector()
    monkeypatch.setattr(c, "_get", _fake_get)
    return c


# --- Adaptadores / helpers ------------------------------------------------

def test_parsers_por_plataforma():
    gh = _parse_greenhouse({"jobs": [{"title": "T", "absolute_url": "u", "updated_at": "2026-07-01T00:00:00Z"}]})
    assert gh[0]["titulo"] == "T" and gh[0]["url"] == "u"
    lv = _parse_lever([{"text": "T", "hostedUrl": "u", "createdAt": 1690000000000}])
    assert lv[0]["titulo"] == "T" and lv[0]["fecha_publicacion"] is not None
    ash = _parse_ashby({"jobs": [{"title": "T", "jobUrl": "u", "publishedAt": "2026-06-01T00:00:00Z"}]})
    assert ash[0]["url"] == "u"


def test_conversion_fechas():
    assert _iso_or_none(None) is None
    assert _iso_or_none("basura") is None
    assert _ms_a_iso(1690000000000).startswith("2023-")
    assert _ms_a_iso(None) is None


# --- Comportamiento del conector -----------------------------------------

def test_requires_slug_solo_en_job_boards():
    assert REGISTRY["job_boards"].requires_slug is True
    assert REGISTRY["google_news"].requires_slug is False
    assert REGISTRY["rss_fijos"].requires_slug is False


def test_sin_slug_no_consulta(monkeypatch):
    c = _connector(monkeypatch)
    assert list(c.search(QuerySpec(empresa="Acme", tipo_evento="contratacion"))) == []


def test_search_salta_404_y_marca_error(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Acme", tipo_evento="contratacion", slug="acme")))
    assert len(items) == 2  # solo Greenhouse
    eventos = {f: ok for f, ok, _ in c.drain_health_events()}
    assert eventos["job_boards:Greenhouse"] is True
    assert eventos["job_boards:Ashby"] is False
    assert "job_boards:Lever" not in eventos  # 404 no cuenta como fallo


def test_normalize_tipo_y_origen_estructurales(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Acme", tipo_evento="ronda", slug="acme")))
    rec = c.normalize(items[0])
    # tipo_evento es estructural (contratacion), aunque la query pidiera otra cosa:
    assert rec.tipo_evento == "contratacion"
    assert rec.origen_declaracion == "operador"
    assert rec.nombre_medio == "Greenhouse"
    assert rec.empresa_mencionada == "Acme"


def test_end_to_end_escribe_y_salud_por_plataforma(db, monkeypatch):
    c = _connector(monkeypatch)
    res = run_connector(db, c, QuerySpec(empresa="Acme", tipo_evento="contratacion", slug="acme"))
    assert res.escritos == 2 and res.no_fechados == 1

    estados = {r["url_fuente"]: r["estado"]
               for r in db.fetch_all("SELECT url_fuente, estado FROM evidencias")}
    assert estados["https://boards.greenhouse.io/acme/jobs/1"] == ESTADO_OK
    assert estados["https://boards.greenhouse.io/acme/jobs/2"] == ESTADO_NO_FECHADO

    ashby = db.fetch_one("SELECT * FROM salud_fuentes WHERE fuente='job_boards:Ashby'")
    gh = db.fetch_one("SELECT * FROM salud_fuentes WHERE fuente='job_boards:Greenhouse'")
    assert ashby["ultimo_estado"] == "error" and gh["ultimo_estado"] == "ok"

    # Segunda corrida: dedup por hash_dedup.
    c2 = _connector(monkeypatch)
    res2 = run_connector(db, c2, QuerySpec(empresa="Acme", tipo_evento="contratacion", slug="acme"))
    assert res2.escritos == 0 and res2.duplicados == 2
