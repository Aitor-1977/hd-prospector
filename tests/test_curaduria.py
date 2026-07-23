"""Tests: Motor de Curaduría Antropológica (Capa 10)."""
import importlib

import pytest
from fastapi.testclient import TestClient

from hd_scraper.config import settings
from hd_scraper.curaduria import (
    curar,
    _identificar_tension,
    _construir_narrativa,
    _curar_convergencias,
    _lectura_convergencia,
    _lectura_antropologica,
    _preguntas_abiertas,
    _siguiente_paso,
    _organizaciones_curadas,
    _lectura_organizacion,
)


@pytest.fixture()
def client(db, monkeypatch):
    api = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api, "get_db", lambda: db)
    object.__setattr__(settings, "ingest_token", "test-tok")
    yield TestClient(api.app)
    object.__setattr__(settings, "ingest_token", "")


def _exp(nombre="Acme", scoring="A", score_icp=70, tipo_deuda="Deuda Relacional",
         deuda_secundaria="", intensidad="Alta", total_evidencias=5,
         keywords=None, patrones=None, vertical="fintech", categoria="Startup",
         decisor_sugerido="Head of CX", angulo_conversacion="abrir por la experiencia",
         deuda_razon="fricción", senal_dominante="friccion_retencion"):
    return {
        "nombre": nombre,
        "scoring": scoring,
        "score_icp": score_icp,
        "tipo_deuda": tipo_deuda,
        "deuda_secundaria": deuda_secundaria,
        "deuda_razon": deuda_razon,
        "intensidad": intensidad,
        "total_evidencias": total_evidencias,
        "keywords": keywords or ["friccion_retencion", "crecimiento"],
        "patrones": patrones or [{"patron": "dolor+crecimiento", "razonamiento": "test"}],
        "vertical": vertical,
        "categoria": categoria,
        "decisor_sugerido": decisor_sugerido,
        "angulo_conversacion": angulo_conversacion,
        "senal_dominante": senal_dominante,
    }


# ── curar() ──────────────────────────────────────────────────────────────

def test_curar_sin_datos():
    c = curar([])
    assert c["tension_central"]["tipo"] == "sin_datos"
    assert c["convergencias"] == []
    assert c["organizaciones_curadas"] == []
    assert len(c["preguntas_abiertas"]) >= 1
    assert c["meta"]["total_organizaciones"] == 0


def test_curar_completo():
    exps = [
        _exp("A", keywords=["friccion_retencion", "crecimiento"]),
        _exp("B", scoring="B", keywords=["reduccion_personal"], tipo_deuda="Deuda Moral"),
    ]
    c = curar(exps, query="fintech", region="LATAM", vertical="fintech")
    assert c["tension_central"]["tipo"] in (
        "dolor_y_crecimiento", "dolor_dominante", "crecimiento_dominante", "estancamiento",
    )
    assert c["tension_central"]["titulo"]
    assert c["tension_central"]["descripcion"]
    assert len(c["narrativa"]) > 50
    assert isinstance(c["convergencias"], list)
    assert isinstance(c["organizaciones_curadas"], list)
    assert len(c["organizaciones_curadas"]) <= 5
    assert isinstance(c["preguntas_abiertas"], list)
    assert c["siguiente_paso"]
    assert c["meta"]["total_organizaciones"] == 2
    assert c["meta"]["query"] == "fintech"
    assert c["meta"]["region"] == "LATAM"


def test_curar_incluye_query_en_narrativa():
    c = curar([_exp()], query="edtech Colombia")
    assert "edtech Colombia" in c["narrativa"]


def test_curar_meta_contadores():
    exps = [
        _exp("D", keywords=["friccion_retencion"]),
        _exp("C", keywords=["crecimiento"]),
        _exp("Conv", keywords=["friccion_retencion", "ronda_inversion"]),
    ]
    c = curar(exps)
    assert c["meta"]["con_dolor"] >= 1
    assert c["meta"]["con_cambio"] >= 1
    assert c["meta"]["con_convergencia"] >= 1


# ── _identificar_tension ─────────────────────────────────────────────────

def test_tension_dolor_y_crecimiento():
    t = _identificar_tension(10, 3, 3)
    assert t[0] == "dolor_y_crecimiento"


def test_tension_dolor_dominante():
    t = _identificar_tension(10, 4, 0)
    assert t[0] == "dolor_dominante"


def test_tension_crecimiento_dominante():
    t = _identificar_tension(10, 0, 4)
    assert t[0] == "crecimiento_dominante"


def test_tension_estancamiento():
    t = _identificar_tension(10, 1, 1)
    assert t[0] == "estancamiento"


def test_tension_n_cero():
    t = _identificar_tension(0, 0, 0)
    assert t[0] == "estancamiento"


# ── _curar_convergencias ─────────────────────────────────────────────────

def test_convergencias_vacio():
    assert _curar_convergencias([]) == []


def test_convergencias_top5():
    exps = [_exp(f"Org{i}", keywords=["friccion_retencion", "crecimiento"]) for i in range(10)]
    conv = _curar_convergencias(exps)
    assert len(conv) <= 5


def test_convergencias_incluye_lectura():
    conv = _curar_convergencias([_exp(keywords=["friccion_retencion", "crecimiento"])])
    assert len(conv) == 1
    assert conv[0]["lectura"]
    assert conv[0]["nombre"] == "Acme"
    assert conv[0]["hipotesis_deuda"] == "Deuda Relacional"
    assert "friccion_retencion" in conv[0]["senales_dolor"]
    assert "crecimiento" in conv[0]["senales_cambio"]


def test_convergencias_ordenadas_por_icp():
    exps = [
        _exp("Baja", score_icp=20, keywords=["friccion_retencion", "crecimiento"]),
        _exp("Alta", score_icp=90, keywords=["friccion_retencion", "crecimiento"]),
    ]
    conv = _curar_convergencias(exps)
    assert conv[0]["nombre"] == "Alta"
    assert conv[1]["nombre"] == "Baja"


# ── _lectura_convergencia ────────────────────────────────────────────────

def test_lectura_convergencia_friccion_crecimiento():
    r = _lectura_convergencia(
        {"friccion_retencion"}, {"crecimiento"}, _exp())
    assert "crece" in r and "pierde" in r


def test_lectura_convergencia_reduccion_ronda():
    r = _lectura_convergencia(
        {"reduccion_personal"}, {"ronda_inversion"}, _exp())
    assert "capital" in r and "recorta" in r


def test_lectura_convergencia_cierre_cambio():
    r = _lectura_convergencia(
        {"cierre_operaciones"}, {"expansion"}, _exp())
    assert "cierra" in r


def test_lectura_convergencia_regulacion():
    r = _lectura_convergencia(
        {"regulacion"}, {"crecimiento"}, _exp())
    assert "regulatoria" in r


def test_lectura_convergencia_con_deuda():
    r = _lectura_convergencia(
        {"otra_senal"}, {"otro_cambio"}, _exp(tipo_deuda="Deuda Moral"))
    assert "Deuda Moral" in r


def test_lectura_convergencia_generica():
    r = _lectura_convergencia(
        {"otra_senal"}, {"otro_cambio"}, _exp(tipo_deuda=""))
    assert "dolor y cambio" in r


# ── _lectura_antropologica ───────────────────────────────────────────────

def test_lectura_antropologica_vacio():
    from collections import Counter
    r = _lectura_antropologica([], Counter(), Counter(), ("estancamiento", "", ""), "")
    assert r == ""


def test_lectura_antropologica_dolor_y_crecimiento():
    from collections import Counter
    exps = [_exp()]
    r = _lectura_antropologica(
        exps, Counter(["Deuda Relacional"]), Counter(["fintech"]),
        ("dolor_y_crecimiento", "t", "d"), "fintech",
    )
    assert "bifurcación" in r


def test_lectura_antropologica_dolor_dominante():
    from collections import Counter
    r = _lectura_antropologica(
        [_exp()], Counter(["Deuda Relacional"]), Counter(),
        ("dolor_dominante", "t", "d"), "",
    )
    assert "estrés" in r


def test_lectura_antropologica_crecimiento_dominante():
    from collections import Counter
    r = _lectura_antropologica(
        [_exp()], Counter(), Counter(),
        ("crecimiento_dominante", "t", "d"), "",
    )
    assert "expansiva" in r


def test_lectura_antropologica_estancamiento():
    from collections import Counter
    r = _lectura_antropologica(
        [_exp()], Counter(), Counter(),
        ("estancamiento", "t", "d"), "",
    )
    assert "no muestra señales fuertes" in r


def test_lectura_antropologica_patrones():
    from collections import Counter
    exps = [_exp(patrones=[{"patron": "a"}, {"patron": "b"}])]
    r = _lectura_antropologica(
        exps, Counter(), Counter(),
        ("estancamiento", "t", "d"), "",
    )
    assert "patrones cruzados" in r


def test_lectura_antropologica_deuda_diversa():
    from collections import Counter
    dc = Counter(["Deuda Relacional", "Deuda Moral", "Deuda Situacional"])
    r = _lectura_antropologica(
        [_exp()], dc, Counter(),
        ("estancamiento", "t", "d"), "",
    )
    assert "diversidad" in r


def test_lectura_antropologica_deuda_unica():
    from collections import Counter
    dc = Counter(["Deuda Relacional"])
    r = _lectura_antropologica(
        [_exp()], dc, Counter(),
        ("estancamiento", "t", "d"), "",
    )
    assert "concentración" in r


# ── _preguntas_abiertas ──────────────────────────────────────────────────

def test_preguntas_max_5():
    from collections import Counter
    p = _preguntas_abiertas(Counter(["Deuda Relacional"]), 5, 5, 10, "fintech")
    assert len(p) <= 5


def test_preguntas_incluye_siempre_la_ultima():
    from collections import Counter
    p = _preguntas_abiertas(Counter(), 0, 0, 1, "")
    assert any("deuda cultural" in q for q in p)


def test_preguntas_con_dolor_y_cambio():
    from collections import Counter
    p = _preguntas_abiertas(Counter(["Deuda Relacional"]), 3, 3, 10, "fintech")
    assert any("crecen" in q for q in p)


def test_preguntas_con_deuda():
    from collections import Counter
    dc = Counter(["Deuda Moral"])
    p = _preguntas_abiertas(dc, 0, 0, 3, "")
    assert any("Deuda Moral" in q for q in p)


# ── _siguiente_paso ──────────────────────────────────────────────────────

def test_siguiente_paso_convergentes():
    exps = [
        _exp("A", keywords=["friccion_retencion", "crecimiento"]),
        _exp("B", keywords=["reduccion_personal", "ronda_inversion"]),
    ]
    from collections import Counter
    r = _siguiente_paso(exps, Counter(["Deuda Relacional"]), 2,
                        ("dolor_y_crecimiento", "", ""))
    assert "convergencia" in r


def test_siguiente_paso_scoring_a():
    from collections import Counter
    exps = [_exp(f"O{i}", scoring="A", keywords=["friccion_retencion"]) for i in range(4)]
    r = _siguiente_paso(exps, Counter(["Deuda Relacional"]), 4,
                        ("dolor_dominante", "", ""))
    assert "peritaje" in r or "interés analítico" in r


def test_siguiente_paso_dolor():
    from collections import Counter
    exps = [_exp(keywords=["friccion_retencion"])]
    r = _siguiente_paso(exps, Counter(["Deuda Relacional"]), 1,
                        ("dolor_dominante", "", ""))
    assert "drift" in r or "observación" in r


def test_siguiente_paso_sin_dolor():
    from collections import Counter
    exps = [_exp(keywords=["crecimiento"])]
    r = _siguiente_paso(exps, Counter(), 0,
                        ("crecimiento_dominante", "", ""))
    assert "vigilancia" in r


def test_siguiente_paso_vacio():
    from collections import Counter
    r = _siguiente_paso([], Counter(), 0, ("estancamiento", "", ""))
    assert "captura" in r


# ── _organizaciones_curadas ──────────────────────────────────────────────

def test_orgs_curadas_limite():
    exps = [_exp(f"Org{i}") for i in range(10)]
    r = _organizaciones_curadas(exps, limite=3)
    assert len(r) == 3


def test_orgs_curadas_priorizan_a():
    exps = [
        _exp("C1", scoring="C"),
        _exp("A1", scoring="A", score_icp=90),
        _exp("B1", scoring="B", score_icp=80),
    ]
    r = _organizaciones_curadas(exps)
    assert r[0]["nombre"] == "A1"
    assert r[1]["nombre"] == "B1"


def test_orgs_curadas_incluyen_lectura():
    r = _organizaciones_curadas([_exp()])
    assert r[0]["lectura"]
    assert r[0]["hipotesis_deuda"] == "Deuda Relacional"
    assert r[0]["angulo"]
    assert r[0]["decisor"]


def test_orgs_curadas_sin_a_b():
    exps = [_exp("C1", scoring="C"), _exp("C2", scoring="C")]
    r = _organizaciones_curadas(exps)
    assert len(r) == 2


# ── _lectura_organizacion ────────────────────────────────────────────────

def test_lectura_org_a_con_deuda():
    r = _lectura_organizacion(_exp(scoring="A", tipo_deuda="Deuda Relacional"))
    assert "dolor explícito" in r
    assert "Deuda Relacional" in r


def test_lectura_org_b_con_deuda():
    r = _lectura_organizacion(_exp(scoring="B", tipo_deuda="Deuda Moral"))
    assert "cambio activo" in r
    assert "Deuda Moral" in r


def test_lectura_org_a_sin_deuda():
    r = _lectura_organizacion(_exp(scoring="A", tipo_deuda=""))
    assert "sin hipótesis" in r


def test_lectura_org_c():
    r = _lectura_organizacion(_exp(scoring="C", tipo_deuda=""))
    assert "observación" in r


def test_lectura_org_deuda_secundaria():
    r = _lectura_organizacion(_exp(deuda_secundaria="Deuda Situacional"))
    assert "secundaria" in r


def test_lectura_org_patrones():
    r = _lectura_organizacion(_exp(patrones=[{"p": "a"}, {"p": "b"}]))
    assert "patrones" in r


def test_lectura_org_evidencia_alta():
    r = _lectura_organizacion(_exp(total_evidencias=8))
    assert "volumen" in r or "solidez" in r


def test_lectura_org_evidencia_baja():
    r = _lectura_organizacion(_exp(total_evidencias=1))
    assert "preliminar" in r


# ── POST /investigacion incluye curaduria ────────────────────────────────

def test_investigacion_incluye_curaduria(client, monkeypatch):
    api_mod = importlib.import_module("hd_scraper.api.app")
    monkeypatch.setattr(api_mod, "_correr_query", lambda *a, **kw: [])
    monkeypatch.setattr(api_mod, "_construir_expedientes", lambda *a, **kw: {"expedientes": []})
    r = client.post("/investigacion",
                    json={"query": "fintech México", "presupuesto_s": 10},
                    headers={"X-Ingest-Token": "test-tok"})
    assert r.status_code == 200
    d = r.json()
    assert "curaduria" in d
    cur = d["curaduria"]
    assert "tension_central" in cur
    assert "narrativa" in cur
    assert "lectura_antropologica" in cur
    assert "convergencias" in cur
    assert "organizaciones_curadas" in cur
    assert "preguntas_abiertas" in cur
    assert "siguiente_paso" in cur
    assert "meta" in cur


# ── UI muestra renderCuraduria ───────────────────────────────────────────

def test_admin_ui_incluye_curaduria(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert "renderCuraduria" in r.text
    assert "curaduria-tension" in r.text


# ── viabilidad_hd y evidencia_curada (JSON de laboratorio) ───────────────

def test_orgs_curadas_incluyen_viabilidad():
    r = _organizaciones_curadas([_exp(scoring="A", tipo_deuda="Deuda Relacional")])
    org = r[0]
    assert "viabilidad_hd" in org
    viab = org["viabilidad_hd"]
    assert viab["nivel"] in ("alta", "media", "baja", "descartable")
    assert viab["razon"]
    assert "profundidad" in viab


def test_orgs_curadas_incluyen_evidencia_curada():
    r = _organizaciones_curadas([_exp(
        scoring="A", tipo_deuda="Deuda Relacional",
        keywords=["friccion_retencion", "crecimiento"],
    )])
    org = r[0]
    assert "evidencia_curada" in org
    ev = org["evidencia_curada"]
    assert len(ev["hechos_estructurales"]) >= 1
    assert "Hipótesis:" in ev["hipotesis_deuda"]
    assert "convergencia" in ev["hipotesis_deuda"]


def test_viabilidad_alta_con_dolor_y_deuda():
    from hd_scraper.curaduria import _viabilidad_candidato
    e = _exp(scoring="A", tipo_deuda="Deuda Relacional")
    e["viabilidad"] = "alta"
    e["profundidad_dolor"] = 90
    v = _viabilidad_candidato(e)
    assert v["nivel"] == "alta"
    assert "viable" in v["razon"]


def test_viabilidad_baja_sin_deuda():
    from hd_scraper.curaduria import _viabilidad_candidato
    e = _exp(scoring="C", tipo_deuda="")
    v = _viabilidad_candidato(e)
    assert v["nivel"] == "baja"


def test_evidencia_curada_separa_dolor_cambio():
    from hd_scraper.curaduria import _evidencia_curada
    e = _exp(keywords=["friccion_retencion", "crecimiento", "regulacion"])
    ev = _evidencia_curada(e)
    assert any("dolor" in h for h in ev["hechos_estructurales"])
    assert any("cambio" in h for h in ev["hechos_estructurales"])


def test_evidencia_curada_sin_deuda():
    from hd_scraper.curaduria import _evidencia_curada
    e = _exp(tipo_deuda="", deuda_razon="")
    ev = _evidencia_curada(e)
    assert ev["hipotesis_deuda"] == ""
