import json

from hd_scraper.connectors.gdelt import GdeltConnector, _seendate_a_iso
from hd_scraper.db.models import ESTADO_NO_FECHADO, ESTADO_OK, QuerySpec
from hd_scraper.pipeline import run_connector

FIXTURE_JSON = json.dumps({
    "articles": [
        {
            "url": "https://www.eleconomista.com.mx/nubank-cierra-ronda",
            "title": "Nubank cierra ronda Serie G",
            "seendate": "20260701T100000Z",
            "domain": "eleconomista.com.mx",
            "language": "Spanish",
        },
        {
            "url": "https://medio-y.com/nubank-nota",
            "title": "Nubank nota sin fecha",
            "seendate": "",
            "domain": "medio-y.com",
            "language": "Spanish",
        },
    ]
})


def _connector(monkeypatch) -> GdeltConnector:
    c = GdeltConnector()
    monkeypatch.setattr(c, "_get", lambda url: FIXTURE_JSON)
    return c


def test_seendate_a_iso():
    assert _seendate_a_iso("20260701T100000Z") == "2026-07-01T10:00:00+00:00"
    assert _seendate_a_iso("") is None
    assert _seendate_a_iso("basura") is None


def test_search_extrae_articulos(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="ronda")))
    assert len(items) == 2
    assert items[0].meta["fuente"] == "eleconomista.com.mx"
    assert items[0].meta["fecha_publicacion"] == "2026-07-01T10:00:00+00:00"
    assert items[1].meta["fecha_publicacion"] is None


def test_normalize_no_interpreta_usa_estructura(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="ronda")))
    rec = c.normalize(items[0])
    assert rec.tipo_evento == "ronda"          # de la consulta, no del texto
    assert rec.origen_declaracion == "prensa"  # estructural
    assert rec.nombre_medio == "eleconomista.com.mx"
    assert rec.empresa_mencionada == "Nubank"


def test_valida_ok_y_no_fechado(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="ronda")))
    assert c.validate(c.normalize(items[0])).estado == ESTADO_OK
    assert c.validate(c.normalize(items[1])).estado == ESTADO_NO_FECHADO


def test_respuesta_vacia_no_rompe(monkeypatch):
    c = GdeltConnector()
    monkeypatch.setattr(c, "_get", lambda url: "")
    assert list(c.search(QuerySpec(empresa="X", tipo_evento="ronda"))) == []


def test_end_to_end_y_dedup(db, monkeypatch):
    c = _connector(monkeypatch)
    res = run_connector(db, c, QuerySpec(empresa="Nubank", tipo_evento="ronda"))
    assert res.escritos == 2 and res.no_fechados == 1
    assert db.fetch_one("SELECT COUNT(*) n FROM evidencias")["n"] == 2

    # Segunda corrida: dedup por hash_dedup, no reinserta.
    c2 = _connector(monkeypatch)
    res2 = run_connector(db, c2, QuerySpec(empresa="Nubank", tipo_evento="ronda"))
    assert res2.escritos == 0 and res2.duplicados == 2
    assert db.fetch_one("SELECT COUNT(*) n FROM evidencias")["n"] == 2

    salud = db.fetch_one("SELECT * FROM salud_fuentes WHERE fuente='gdelt'")
    assert salud["ultimo_estado"] == "ok" and salud["alerta"] == 0
