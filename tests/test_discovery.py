"""Descubrimiento: composición de consultas por categoría + tipo + vertical."""
from hd_scraper.discovery import TIPO_KEYWORDS, queries_para, region_clause


def test_queries_emite_una_por_variante():
    # El bucket de fricción "queja" tiene varias variantes -> varias consultas.
    qs = queries_para("Startup", "queja", vertical="fintech")
    textos = [t for t, _ in qs]
    assert len(textos) == len(set(textos))            # sin duplicados
    assert len(textos) == len(TIPO_KEYWORDS["queja"])  # una por variante (1 base)
    # Todas llevan la base del ecosistema y la vertical (ahora en grupo OR).
    assert all("startup" in t and "fintech" in t for t in textos)
    # Y el tipo_evento se conserva (literal del contrato).
    assert all(tipo == "queja" for _, tipo in qs)


def test_queries_amplian_con_grupos_or():
    # RECALL: cada consulta usa grupos OR (no palabras sueltas AND), para que
    # Google News no exija que TODAS las palabras aparezcan juntas.
    qs = queries_para("Startup", "queja", vertical="todas")
    textos = [t for t, _ in qs]
    assert textos and all(" OR " in t for t in textos)
    # La base misma es un grupo OR de sinónimos del ecosistema.
    assert all("(startup or" in t.lower() for t in textos)


def test_queja_cubre_fricciones_ampliadas():
    variantes = " ".join(TIPO_KEYWORDS["queja"]).lower()
    for termino in ("pérdida de clientes", "cancelación", "demanda", "regulatorio",
                    "crecimiento", "reestructuración", "crisis", "cierre de operaciones"):
        assert termino in variantes


def test_region_clause_latam():
    c = region_clause("LATAM")
    assert c.startswith("(") and "México" in c and '"Costa Rica"' in c
