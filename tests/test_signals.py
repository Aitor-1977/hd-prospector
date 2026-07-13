"""Extracción objetiva Nivel 1: keywords de señal + confianza (Motor A)."""
from hd_scraper.signals import calcular_confianza, detectar_keywords


def test_detectar_keywords_genericas():
    tags = detectar_keywords("La fintech anuncia una nueva ronda serie B")
    assert "ronda_inversion" in tags
    tags2 = detectar_keywords("Reportan alta deserción y churn de usuarios")
    assert "friccion_retencion" in tags2
    assert detectar_keywords("Un día soleado en la ciudad") == []


def test_confianza_es_objetiva():
    # fechada + medio nombrado + con señales => alta
    alta = calcular_confianza("2026-07-01T00:00:00Z", "Bloomberg Línea", ["ronda_inversion"])
    # sin fecha + fuente genérica + sin señales => baja
    baja = calcular_confianza(None, "Google News", [])
    assert alta > baja
    assert 0.0 <= baja <= 1.0 and 0.0 <= alta <= 1.0
    assert alta == round(0.4 + 0.25 + 0.20 + 0.15, 2)
    assert baja == 0.4
