"""Consultas de descubrimiento alineadas al perfil de prospecto ideal de HD.

Hamaca Digital busca (ver docs/perfil_prospecto_hd.md):
  - Verticales dependientes de contexto: fintech, edtech, healthtech / salud
    mental, logística agrícola, identidad.
  - Fase de escala o estancamiento con señales de fricción cultural: churn alto,
    baja adopción/retención, fricción "inexplicable por datos".
  - VCs que necesitan due diligence cualitativo de su portafolio.

Cada consulta se compone de partes DECLARADAS (no inferidas):

    base del ecosistema  +  vertical  +  señal (tipo)   ( +  zona geográfica )

La zona geográfica se añade aparte (OR de países). El motor trae titulares que
coinciden y los etiqueta; NO decide qué empresa menciona ni puntúa Deuda
Cultural (eso es interpretación de HD, no del scraper). Ajustable con
HD_DISCOVERY_<CATEGORIA> (bases separadas por '|').
"""
from __future__ import annotations

import os

# Bases por ecosistema, orientadas al prospecto ideal de HD.
#
# RECALL (mejora de la búsqueda): Google News combina las palabras sueltas con
# AND, así que una base de varias palabras ("corporativo innovación abierta")
# exige que TODAS aparezcan y casi no devuelve nada. Por eso las bases usan
# grupos OR entre sinónimos/variantes: piden CUALQUIERA, no todas. Sigue siendo
# descubrimiento estructural (frases declaradas), no interpretación.
CATEGORIA_BASE_DEFAULT: dict[str, list[str]] = {
    "VC": ['("venture capital" OR "corporate venture capital" OR "fondo de inversión" OR "capital de riesgo")'],
    "Startup": ["(startup OR startups OR emprendimiento)"],
    "Incubadora": ["(aceleradora OR incubadora OR \"venture builder\")"],
    "Corporativo": ['(corporativo OR corporación OR "innovación abierta" OR "transformación digital")'],
}

# Verticales dependientes de contexto que le interesan a HD. También en grupo OR:
# basta que aparezca cualquiera de los sinónimos de la vertical.
VERTICALES_HD: dict[str, str] = {
    "todas": "",
    "fintech": "(fintech OR pagos OR \"crédito digital\")",
    "edtech": "(edtech OR educación)",
    "healthtech": "(healthtech OR \"salud digital\" OR telemedicina)",
    "salud mental": '("salud mental" OR bienestar OR terapia)',
    "logística agrícola": '(agtech OR "logística agrícola" OR agro)',
    "identidad": '("identidad digital" OR verificación OR KYC)',
    "hrtech": '(hrtech OR "recursos humanos" OR "gestión de talento" OR "people analytics")',
    "saas_b2b": '("SaaS B2B" OR "software empresarial" OR "plataforma B2B" OR "enterprise software")',
    "climatetech": '(climatetech OR "tecnología climática" OR "energía renovable" OR "sostenibilidad" OR cleantech)',
}

# Señal (tipo_evento del contrato) → VARIANTES de consulta. Cada variante es una
# frase temática que se combina con base+vertical para formar UNA consulta
# independiente. Usar varias frases cortas (en vez de una sola frase larga)
# amplía el descubrimiento: Google News trata los términos como conjunción, así
# que una frase larga estrecha demasiado. El vocabulario literal del contrato
# (las claves) NO cambia; solo se enriquecen las frases de búsqueda.
#
# Cambio principal de Captura Inteligente: el tipo "queja" (bucket de fricción)
# deja de ser un único término y pasa a cubrir explícitamente pérdida de
# clientes, despidos, conflictos regulatorios, caídas de crecimiento,
# cancelación de servicios, demandas, reestructuración y crisis operativas.
# Cada VARIANTE es ahora un grupo OR (frases entre comillas unidas con OR), no
# una bolsa de palabras sueltas AND. Antes, "pérdida de clientes fuga de
# usuarios" exigía que las 7 palabras aparecieran juntas -> casi 0 resultados.
# Ahora pide CUALQUIERA de las frases -> muchos más titulares reales, que luego
# el filtro de relevancia y la zona depuran. El vocabulario del contrato (las
# claves del dict) NO cambia.
TIPO_KEYWORDS: dict[str, list[str]] = {
    "ronda": [
        '("ronda de inversión" OR "levanta capital" OR "serie A" OR "serie B" '
        'OR recauda OR financiamiento OR "capital semilla")',
    ],
    "contratacion": [
        '("contratación masiva" OR "nuevo ejecutivo" OR "head of" OR "director de" '
        'OR "nombra a" OR "plan de contratación")',
    ],
    "despido": [
        '("despidos masivos" OR "recorte de personal" OR reestructuración '
        'OR "cierre de operaciones" OR "ajuste de plantilla")',
    ],
    "lanzamiento": [
        '("lanzamiento de producto" OR "nueva plataforma" OR estrena '
        'OR "lanza" OR relanzamiento)',
    ],
    # Bucket de fricción — grupos OR amplios que cubren pérdida de clientes,
    # cancelaciones, demandas/regulación, caída de crecimiento y reestructuración.
    "queja": [
        '("pérdida de clientes" OR "fuga de usuarios" OR churn OR "baja retención" '
        'OR cancelaciones OR "cancelación de servicio")',
        '("demanda colectiva" OR denuncia OR "conflicto regulatorio" OR multa '
        'OR sanción)',
        '("caída de crecimiento" OR desaceleración OR reestructuración '
        'OR "crisis operativa" OR despidos OR "cierre de operaciones")',
    ],
    "cambio_sitio": [
        '("rediseño de marca" OR pivote OR "nuevo modelo de negocio" OR relanzamiento)',
    ],
}

PAISES_LATAM = ["México", "Colombia", "Chile", "Perú", "Argentina", "Brasil",
                "Costa Rica", "Panamá"]

REGIONES: dict[str, list[str]] = {"LATAM": PAISES_LATAM, **{p: [p] for p in PAISES_LATAM}}


def _comilla_si_espacio(pais: str) -> str:
    return f'"{pais}"' if " " in pais else pais


def region_clause(region: str) -> str:
    """Cláusula de zona, p. ej. (México OR Colombia OR …). Vacía si es desconocida."""
    paises = REGIONES.get(region)
    if not paises:
        return ""
    return "(" + " OR ".join(_comilla_si_espacio(p) for p in paises) + ")"


def _bases(categoria: str) -> list[str]:
    override = os.getenv(f"HD_DISCOVERY_{categoria.upper()}")
    if override:
        return [b.strip() for b in override.split("|") if b.strip()]
    return CATEGORIA_BASE_DEFAULT.get(categoria, [])


def queries_para(categoria: str, tipo_evento: str, vertical: str = "todas") -> list[tuple[str, str]]:
    """Consultas (texto, tipo_evento) para categoría + tipo de señal + vertical.

    Compone base del ecosistema + vertical (HD) + cada VARIANTE del tipo. Se
    emite una consulta por variante para ampliar el descubrimiento (sobre todo
    en el bucket de fricción "queja"). Las consultas duplicadas se colapsan
    conservando el orden. La zona geográfica se añade aparte en el pipeline.
    """
    variantes = TIPO_KEYWORDS.get(tipo_evento) or [""]
    vkw = VERTICALES_HD.get(vertical, "")
    salida: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for base in _bases(categoria):
        for kw in variantes:
            texto = " ".join(x for x in (base, vkw, kw) if x).strip()
            if texto and texto not in vistos:
                vistos.add(texto)
                salida.append((texto, tipo_evento))
    return salida
