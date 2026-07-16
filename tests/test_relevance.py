"""Captura Inteligente: filtro de relevancia y calidad (objetivos, sin IA)."""
from hd_scraper.relevance import (
    CALIDAD_ALTA,
    CALIDAD_BAJA,
    CALIDAD_MEDIA,
    MOTIVO_OPINION,
    MOTIVO_SIN_EMPRESA,
    MOTIVO_SIN_EVENTO,
    calcular_calidad,
    detectar_empresa,
    es_opinion,
    evaluar_relevancia,
)


# ── detección de empresa (nombre propio) ─────────────────────────────────────

def test_detectar_empresa_nombre_al_inicio():
    assert detectar_empresa("Nubank anuncia nueva ronda de inversión") == "Nubank"


def test_detectar_empresa_ignora_articulo_inicial_y_sector():
    # "La" es artículo, "fintech" es genérico -> la empresa es "Clara".
    assert detectar_empresa("La fintech Clara levanta capital serie B") == "Clara"


def test_detectar_empresa_acepta_siglas():
    assert detectar_empresa("BBVA lanza un nuevo producto") == "BBVA"


def test_detectar_empresa_sin_nombre_propio():
    # Tendencia genérica sin empresa nombrada.
    assert detectar_empresa("las startups enfrentan un año difícil") is None


# ── marcadores de opinión / tendencia / listículo ────────────────────────────

def test_es_opinion_detecta_marcadores():
    assert es_opinion("Opinión: por qué las fintech fracasan")
    assert es_opinion("El futuro de la banca digital en 2027")
    assert es_opinion("5 claves para entender el churn")
    assert es_opinion("Los mejores bancos digitales de la región")


def test_es_opinion_no_marca_noticia_de_evento():
    assert not es_opinion("Nubank adquiere una startup de pagos")


# ── filtro de relevancia ─────────────────────────────────────────────────────

def test_relevancia_conserva_evento_con_empresa():
    ok, motivo = evaluar_relevancia(
        "Nubank adquiere una fintech de pagos", ["adquisicion"], empresa_identificada=True)
    assert ok and motivo == ""


def test_relevancia_descarta_opinion():
    ok, motivo = evaluar_relevancia(
        "Opinión: el futuro de Nubank", ["adquisicion"], empresa_identificada=True)
    assert not ok and motivo == MOTIVO_OPINION


def test_relevancia_descarta_sin_empresa():
    ok, motivo = evaluar_relevancia(
        "Las startups enfrentan más despidos", ["reduccion_personal"],
        empresa_identificada=False)
    assert not ok and motivo == MOTIVO_SIN_EMPRESA


def test_relevancia_descarta_sin_evento():
    ok, motivo = evaluar_relevancia(
        "Nubank celebra su aniversario", [], empresa_identificada=True)
    assert not ok and motivo == MOTIVO_SIN_EVENTO


def test_relevancia_descarta_espana_y_no_empresa():
    from hd_scraper.relevance import MOTIVO_NO_EMPRESA, MOTIVO_NO_LATAM
    # Geografía fuera de LATAM (España / Girona / Castilla).
    ok, m = evaluar_relevancia(
        "El Gobierno de Castilla-La Mancha impulsa la innovación", ["expansion"], True)
    assert not ok and m in (MOTIVO_NO_LATAM, MOTIVO_NO_EMPRESA)
    # Premios (no es empresa).
    ok2, m2 = evaluar_relevancia(
        "Los Premios Princesa de Girona reconocen seis proyectos", ["lanzamiento"], True)
    assert not ok2
    # Reporte de mercado "…AÑO:".
    ok3, m3 = evaluar_relevancia(
        "Venture Capital LATAM 2025: la inversión cae 30%", ["ronda_inversion"], True)
    assert not ok3 and m3 == MOTIVO_NO_EMPRESA
    # Análisis "de cada 10" sin empresa concreta.
    ok4, _ = evaluar_relevancia(
        "Siete de cada 10 startups no están listas para escalar", ["expansion"], True)
    assert not ok4


def test_relevancia_conserva_empresa_mexicana_real():
    ok, motivo = evaluar_relevancia(
        "Konfío levanta una ronda serie C en México", ["ronda_inversion"], True)
    assert ok and motivo == ""


# ── calidad de captura (informativa) ─────────────────────────────────────────

def test_calidad_alta_media_baja():
    assert calcular_calidad(True, True, True) == CALIDAD_ALTA
    assert calcular_calidad(True, True, False) == CALIDAD_MEDIA
    assert calcular_calidad(True, False, False) == CALIDAD_BAJA
    assert calcular_calidad(False, False, False) == CALIDAD_BAJA


def test_calidad_duplicado_fuerza_baja():
    assert calcular_calidad(True, True, True, sin_duplicado=False) == CALIDAD_BAJA
