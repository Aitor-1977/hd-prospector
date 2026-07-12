from hd_scraper.connectors.google_news import GoogleNewsConnector
from hd_scraper.db.models import QuerySpec
from hd_scraper.pipeline import run_connector
from tests.test_google_news import FIXTURE_RSS

RSS_TIPO_LIBRE = FIXTURE_RSS


def _run(db, monkeypatch, tipo="ronda"):
    c = GoogleNewsConnector()
    monkeypatch.setattr(c, "_get", lambda url: FIXTURE_RSS)
    return run_connector(db, c, QuerySpec(empresa="Nubank", tipo_evento=tipo))


def test_end_to_end_escribe_evidencias_y_retiene_crudo(db, monkeypatch):
    res = _run(db, monkeypatch)
    assert res.vistos == 2
    assert res.escritos == 2
    assert res.no_fechados == 1

    total = db.fetch_one("SELECT COUNT(*) AS n FROM evidencias")["n"]
    assert total == 2
    # Solo 1 es consumible (estado ok); el otro es no_fechado.
    ok = db.fetch_one("SELECT COUNT(*) AS n FROM evidencias WHERE estado='ok'")["n"]
    assert ok == 1
    # Crudo retenido y vinculado por hash.
    raws = db.fetch_one("SELECT COUNT(*) AS n FROM raw_store")["n"]
    assert raws == 2
    # Salud registrada sin alerta.
    salud = db.fetch_one("SELECT * FROM salud_fuentes WHERE fuente='google_news'")
    assert salud["ultimo_estado"] == "ok" and salud["alerta"] == 0


def test_dedup_en_segunda_corrida(db, monkeypatch):
    _run(db, monkeypatch)
    res2 = _run(db, monkeypatch)
    assert res2.escritos == 0
    assert res2.duplicados == 2
    total = db.fetch_one("SELECT COUNT(*) AS n FROM evidencias")["n"]
    assert total == 2  # no se duplicó


def test_tipo_evento_invalido_va_a_rechazos(db, monkeypatch):
    # Un tipo fuera del vocabulario literal: el validador lo manda a rechazos.
    res = _run(db, monkeypatch, tipo="opinion")
    assert res.rechazados == 2
    assert res.escritos == 0
    ev = db.fetch_one("SELECT COUNT(*) AS n FROM evidencias")["n"]
    rz = db.fetch_one("SELECT COUNT(*) AS n FROM rechazos")["n"]
    assert ev == 0 and rz == 2
