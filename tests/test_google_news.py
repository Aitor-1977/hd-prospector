from hd_scraper.connectors.google_news import GoogleNewsConnector
from hd_scraper.db.models import ESTADO_NO_FECHADO, ESTADO_OK, QuerySpec

FIXTURE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Nubank - Google News</title>
  <item>
    <title>Nubank anuncia nueva ronda de inversión - Bloomberg Línea</title>
    <link>https://news.google.com/rss/articles/ABC123?oc=5</link>
    <pubDate>Wed, 01 Jul 2026 10:00:00 GMT</pubDate>
    <source url="https://www.bloomberglinea.com">Bloomberg Línea</source>
  </item>
  <item>
    <title>Nota sin fecha sobre Nubank</title>
    <link>https://news.google.com/rss/articles/NODATE?oc=5</link>
    <source url="https://medio-x.com">Medio X</source>
  </item>
</channel>
</rss>
"""


def _connector(monkeypatch) -> GoogleNewsConnector:
    c = GoogleNewsConnector()
    monkeypatch.setattr(c, "_get", lambda url: FIXTURE_RSS)
    return c


def test_search_extrae_items(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="ronda")))
    assert len(items) == 2
    assert items[0].meta["fuente"] == "Bloomberg Línea"
    assert items[0].meta["fecha_publicacion"] is not None
    assert items[1].meta["fecha_publicacion"] is None


def test_normalize_no_interpreta_usa_estructura(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="ronda")))
    rec = c.normalize(items[0])
    # tipo_evento viene de la consulta (estructura), no del texto:
    assert rec.tipo_evento == "ronda"
    # origen_declaracion es estructural para un feed de prensa:
    assert rec.origen_declaracion == "prensa"
    assert rec.cita_textual.startswith("Nubank anuncia")
    assert rec.nombre_medio == "Bloomberg Línea"
    assert rec.empresa_mencionada == "Nubank"


def test_valida_ok_y_no_fechado(monkeypatch):
    c = _connector(monkeypatch)
    items = list(c.search(QuerySpec(empresa="Nubank", tipo_evento="ronda")))
    v0 = c.validate(c.normalize(items[0]))
    v1 = c.validate(c.normalize(items[1]))
    assert v0.ok and v0.estado == ESTADO_OK
    assert v1.ok and v1.estado == ESTADO_NO_FECHADO
