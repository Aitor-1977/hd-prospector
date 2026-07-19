"""Captura Inteligente end-to-end: dedup robusto + filtro de relevancia + calidad.

Simula el descubrimiento amplio (QuerySpec.exact=False), que es donde entra el
ruido, y comprueba que:
  - el MISMO artículo capturado por dos consultas distintas se guarda una vez,
  - las notas de opinión / sin empresa / sin evento se descartan con motivo,
  - las evidencias almacenadas llevan calidad_captura (Alta|Media|Baja),
  - el contrato /corpus NO cambia (calidad_captura no aparece ahí).
"""
import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hd_scraper.config import settings
from hd_scraper.connectors.google_news import GoogleNewsConnector
from hd_scraper.db.models import QuerySpec
from hd_scraper.pipeline import run_connector

# Feed de descubrimiento: 1 evento bueno con empresa, el MISMO evento con otra
# URL (republicado), 1 opinión, 1 tendencia sin empresa, 1 nota sin evento.
FIXTURE_DISCOVERY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>descubrimiento - Google News</title>
  <item>
    <title>Nubank adquiere una fintech de pagos - Bloomberg Línea</title>
    <link>https://news.google.com/rss/articles/EVENTO1?oc=5</link>
    <pubDate>Wed, 01 Jul 2026 10:00:00 GMT</pubDate>
    <source url="https://www.bloomberglinea.com">Bloomberg Línea</source>
  </item>
  <item>
    <title>Nubank adquiere una fintech de pagos - El Financiero</title>
    <link>https://news.google.com/rss/articles/EVENTO1_REPUB?oc=5</link>
    <pubDate>Wed, 01 Jul 2026 12:00:00 GMT</pubDate>
    <source url="https://elfinanciero.com">El Financiero</source>
  </item>
  <item>
    <title>Opinión: por qué las fintech van a fracasar - Columna</title>
    <link>https://news.google.com/rss/articles/OPINION?oc=5</link>
    <pubDate>Wed, 01 Jul 2026 09:00:00 GMT</pubDate>
    <source url="https://medio-x.com">Medio X</source>
  </item>
  <item>
    <title>Las startups enfrentan un año de despidos - Reporte</title>
    <link>https://news.google.com/rss/articles/TENDENCIA?oc=5</link>
    <pubDate>Wed, 01 Jul 2026 08:00:00 GMT</pubDate>
    <source url="https://medio-y.com">Medio Y</source>
  </item>
  <item>
    <title>Nubank celebra su aniversario en la ciudad - Prensa</title>
    <link>https://news.google.com/rss/articles/SINEVENTO?oc=5</link>
    <pubDate>Wed, 01 Jul 2026 07:00:00 GMT</pubDate>
    <source url="https://medio-z.com">Medio Z</source>
  </item>
</channel></rss>
"""


def _discovery_query(termino: str) -> QuerySpec:
    # Descubrimiento amplio: exact=False (así se aplica el filtro de relevancia).
    return QuerySpec(empresa=termino, tipo_evento="ronda", exact=False, categoria="Startup")


def _run(db, termino="startup fintech"):
    c = GoogleNewsConnector()
    with patch.object(c, "_get", lambda url: FIXTURE_DISCOVERY):
        return run_connector(db, c, _discovery_query(termino))


def test_filtro_relevancia_descarta_ruido(db):
    res = _run(db)
    assert res.vistos == 5
    # Se conserva 1 (evento con empresa) + su republicación se deduplica.
    assert res.escritos == 1
    assert res.duplicados == 1          # la republicación (mismo título) colapsa
    assert res.filtrados == 3           # opinión + tendencia sin empresa + sin evento
    total = db.fetch_one("SELECT COUNT(*) AS n FROM evidencias")["n"]
    assert total == 1
    # Los descartes quedan auditados con motivo de relevancia.
    motivos = {r["motivo"] for r in db.fetch_all("SELECT motivo FROM rechazos")}
    assert any(m.startswith("relevancia:") for m in motivos)


def test_dedup_contenido_entre_consultas_distintas(db):
    # Dos consultas de descubrimiento DISTINTAS traen el mismo feed: el artículo
    # bueno NO debe guardarse dos veces (aunque la "empresa" del query difiera).
    _run(db, termino="startup fintech")
    res2 = _run(db, termino="fintech adquisición")
    assert res2.escritos == 0
    assert res2.duplicados >= 1
    total = db.fetch_one("SELECT COUNT(*) AS n FROM evidencias")["n"]
    assert total == 1


def test_evidencia_lleva_calidad_captura(db):
    _run(db)
    fila = db.fetch_one("SELECT calidad_captura FROM evidencias LIMIT 1")
    assert fila["calidad_captura"] in ("Alta", "Media", "Baja")


def test_descubrimiento_guarda_la_empresa_detectada_no_el_termino(db):
    # El término de consulta era "startup fintech"; la evidencia guardada debe
    # tener la ORGANIZACIÓN detectada del titular ("Nubank"), no el término.
    _run(db, termino="startup fintech")
    fila = db.fetch_one("SELECT empresa_mencionada FROM evidencias LIMIT 1")
    assert fila["empresa_mencionada"] == "Nubank"


def test_calidad_en_evidencias_y_en_corpus(db, monkeypatch):
    _run(db)
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    cli = TestClient(api.app)

    ev = cli.get("/evidencias").json()["items"][0]
    assert "calidad_captura" in ev   # informativo, visible en /evidencias

    # Extensión aditiva del contrato: calidad_captura (objetiva) ahora viaja en
    # /corpus para que Motor B la use como contexto. Sigue sin filtrar interpretación.
    corpus = cli.get("/corpus").json()["items"][0]
    assert corpus["calidad_captura"] in ("Alta", "Media", "Baja")
    assert set(corpus) == {"empresa", "fuente", "fecha", "texto", "url",
                           "keywords", "confianza", "calidad_captura",
                           "categoria", "tipo_evento", "hash"}
    assert "deuda_cultural" not in corpus and "icp" not in corpus
