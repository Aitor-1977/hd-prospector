import pytest

from hd_scraper.connectors.rss_fijos import RssFijosConnector, _normalizar_texto
from hd_scraper.db.models import ESTADO_NO_FECHADO, ESTADO_OK, QuerySpec
from hd_scraper.pipeline import run_connector

FEED_STARTUPEABLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Startupeable</title>
  <item>
    <title>Nubank lanza nuevo producto en México</title>
    <link>https://startupeable.com/nubank-producto</link>
    <description>La fintech anuncia expansión.</description>
    <pubDate>Wed, 01 Jul 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Otra startup levanta capital</title>
    <link>https://startupeable.com/otra-startup</link>
    <description>Sin relación.</description>
    <pubDate>Tue, 30 Jun 2026 09:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

# Menciona "Núbank" con acento: la coincidencia debe ser insensible a acentos.
FEED_CONTXTO = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Contxto</title>
  <item>
    <title>Análisis: Núbank sigue creciendo</title>
    <link>https://contxto.com/nubank-analisis</link>
    <description>Cobertura regional.</description>
  </item>
</channel></rss>
"""

FEEDS = {
    "Startupeable": "https://startupeable.com/feed/",
    "Contxto": "https://contxto.com/feed/",
    "FeedRoto": "https://roto.example/feed/",
}


def _fake_get(url: str) -> str:
    if "startupeable" in url:
        return FEED_STARTUPEABLE
    if "contxto" in url:
        return FEED_CONTXTO
    raise RuntimeError("feed caído")  # no-httpx: sin reintentos/backoff


def _connector(monkeypatch) -> RssFijosConnector:
    c = RssFijosConnector(feeds=FEEDS)
    monkeypatch.setattr(c, "_get", _fake_get)
    return c


def test_normalizar_texto_sin_acentos():
    assert _normalizar_texto("Núbank MÉXICO") == "nubank mexico"


def test_filtra_por_mencion_literal(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="lanzamiento")))
    # Solo 2 entradas mencionan Nubank (una por feed vivo); la no relacionada se descarta.
    assert len(items) == 2
    medios = {it.meta["medio"] for it in items}
    assert medios == {"Startupeable", "Contxto"}


def test_normalize_usa_nombre_medio_fijo_y_estructura(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="lanzamiento")))
    rec = c.normalize(items[0])
    assert rec.nombre_medio in {"Startupeable", "Contxto"}  # nombre fijo, no del feed
    assert rec.origen_declaracion == "prensa"
    assert rec.tipo_evento == "lanzamiento"


def test_salud_por_feed_incluye_el_caido(monkeypatch):
    c = _connector(monkeypatch)
    list(c.search(QuerySpec(empresa="Nubank", tipo_evento="lanzamiento")))
    eventos = {f: ok for f, ok, _ in c.drain_health_events()}
    assert eventos["rss_fijos:Startupeable"] is True
    assert eventos["rss_fijos:Contxto"] is True
    assert eventos["rss_fijos:FeedRoto"] is False


def test_end_to_end_escribe_filtra_y_registra_salud(db, monkeypatch):
    c = _connector(monkeypatch)
    res = run_connector(db, c, QuerySpec(empresa="Nubank", tipo_evento="lanzamiento"))
    assert res.vistos == 2 and res.escritos == 2
    # La de Contxto no trae fecha -> no_fechado; la de Startupeable sí.
    assert res.no_fechados == 1

    estados = {r["nombre_medio"]: r["estado"]
               for r in db.fetch_all("SELECT nombre_medio, estado FROM evidencias")}
    assert estados["Startupeable"] == ESTADO_OK
    assert estados["Contxto"] == ESTADO_NO_FECHADO

    # Salud por sub-fuente persistida por el pipeline.
    roto = db.fetch_one("SELECT * FROM salud_fuentes WHERE fuente='rss_fijos:FeedRoto'")
    ok_feed = db.fetch_one("SELECT * FROM salud_fuentes WHERE fuente='rss_fijos:Startupeable'")
    assert roto["ultimo_estado"] == "error"
    assert ok_feed["ultimo_estado"] == "ok"


def test_feed_caido_dos_corridas_dispara_alerta(db, monkeypatch):
    c = _connector(monkeypatch)
    run_connector(db, c, QuerySpec(empresa="Nubank", tipo_evento="lanzamiento"))
    c2 = _connector(monkeypatch)
    run_connector(db, c2, QuerySpec(empresa="Nubank", tipo_evento="lanzamiento"))
    roto = db.fetch_one("SELECT * FROM salud_fuentes WHERE fuente='rss_fijos:FeedRoto'")
    assert roto["fallos_consecutivos"] == 2 and roto["alerta"] == 1
