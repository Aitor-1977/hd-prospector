"""Análisis profundo determinista: señales capturadas -> scoring/Deuda/ICP."""
from hd_scraper.analisis import analizar


def test_dolor_friccion_da_scoring_a_y_deuda_relacional():
    r = analizar(["friccion_retencion"], vertical="fintech", confianza=0.85, calidad="Alta")
    assert r["scoring"] == "A"
    assert r["tipo_deuda"] == "Deuda Relacional"
    assert r["senal_dominante"] == "friccion_retencion"
    assert "Customer Success" in r["decisor_sugerido"]
    # fintech (vertical HD) + dolor + confianza alta + calidad Alta -> ICP alto.
    assert r["score_icp"] >= 80


def test_recorte_da_deuda_moral_y_rrhh():
    r = analizar(["reduccion_personal"], vertical="edtech", confianza=0.6, calidad="Media")
    assert r["scoring"] == "A"
    assert r["tipo_deuda"] == "Deuda Moral"
    assert "RRHH" in r["decisor_sugerido"]


def test_ronda_da_scoring_b_y_escalamiento():
    r = analizar(["ronda_inversion"], vertical="fintech", confianza=0.7, calidad="Media")
    assert r["scoring"] == "B"
    assert r["tipo_deuda"] == "Deuda de Escalamiento"
    assert "Fundador" in r["decisor_sugerido"]


def test_sin_senal_da_c_y_sin_deuda():
    r = analizar([], vertical="", confianza=0.4, calidad="Baja")
    assert r["scoring"] == "C"
    assert r["tipo_deuda"] == ""
    assert r["score_icp"] < 50


def test_senal_dominante_prioriza_dolor_sobre_cambio():
    # Con ronda (cambio) Y despidos (dolor), el scoring sigue siendo A (dolor).
    # La deuda ahora la afina la COMBINACIÓN (ver test siguiente).
    r = analizar(["ronda_inversion", "reduccion_personal"], vertical="healthtech",
                 confianza=0.9, calidad="Alta")
    assert r["scoring"] == "A"


def test_combinacion_recorte_tras_ronda():
    # Recorte + ronda => hipótesis de combinación más precisa que cada señal sola.
    r = analizar(["ronda_inversion", "reduccion_personal"], vertical="", confianza=0.9)
    assert r["tipo_deuda"] == "Deuda de Escalamiento mal gestionado"
    assert r["angulo_conversacion"]  # trae un ángulo de conversación


def test_combinacion_friccion_con_crecimiento():
    r = analizar(["crecimiento", "friccion_retencion"], vertical="fintech", confianza=0.8)
    assert r["tipo_deuda"] == "Deuda de Experiencia en escala"


def test_matiz_vertical_enriquece_razon():
    r = analizar(["friccion_retencion"], vertical="salud mental", confianza=0.7)
    assert "salud mental" in r["deuda_razon"]


def test_vertical_no_hd_no_suma_bonus():
    con_hd = analizar(["ronda_inversion"], vertical="fintech", confianza=0.5, calidad="Baja")
    sin_hd = analizar(["ronda_inversion"], vertical="retail", confianza=0.5, calidad="Baja")
    assert con_hd["score_icp"] > sin_hd["score_icp"]


def test_icp_acotado_0_100():
    r = analizar(["friccion_retencion", "reduccion_personal"], vertical="fintech",
                 confianza=1.0, calidad="Alta")
    assert 0 <= r["score_icp"] <= 100


def test_intensidad_alta_con_dos_dolores():
    r = analizar(["friccion_retencion", "reduccion_personal"], confianza=0.5)
    assert r["intensidad"] == "Alta"


def test_intensidad_baja_sin_dolor_ni_cambio():
    r = analizar([], confianza=0.3)
    assert r["intensidad"] == "Baja"


def test_deuda_secundaria_cuando_hay_dos_senales_distintas():
    # Recorte (Deuda Moral) + regulación (Deuda de Gobernanza): sin combinación
    # declarada para ese par, domina la señal única (recorte) y la otra queda
    # como secundaria.
    r = analizar(["reduccion_personal", "regulacion"])
    assert r["tipo_deuda"] == "Deuda Moral"
    assert r["deuda_secundaria"] == "Deuda de Gobernanza"


# ── profundidad_dolor y viabilidad ───────────────────────────────────────

def test_profundidad_dolor_presente():
    r = analizar(["friccion_retencion"], vertical="fintech", confianza=0.8)
    assert r["profundidad_dolor"] > 0
    assert r["profundidad_dolor"] >= 90


def test_profundidad_dolor_cero_sin_senal():
    r = analizar([], confianza=0.3)
    assert r["profundidad_dolor"] == 0


def test_profundidad_amplificada_por_vertical():
    con_vert = analizar(["friccion_retencion"], vertical="fintech")
    sin_vert = analizar(["friccion_retencion"], vertical="retail")
    assert con_vert["profundidad_dolor"] > sin_vert["profundidad_dolor"]


def test_viabilidad_alta_dolor_vertical_hd():
    r = analizar(["friccion_retencion"], vertical="fintech", confianza=0.8)
    assert r["viabilidad"] == "alta"


def test_viabilidad_media_dolor_sin_vertical():
    r = analizar(["friccion_retencion"], vertical="retail", confianza=0.5)
    assert r["viabilidad"] in ("alta", "media")


def test_viabilidad_baja_solo_cambio():
    r = analizar(["ronda_inversion"], vertical="retail", confianza=0.5)
    assert r["viabilidad"] == "baja"


def test_viabilidad_descartable_sin_senal():
    r = analizar([], confianza=0.3)
    assert r["viabilidad"] == "descartable"


def test_profundidad_en_razon():
    r = analizar(["friccion_retencion"], vertical="fintech")
    assert "profundidad" in r["razon"]
