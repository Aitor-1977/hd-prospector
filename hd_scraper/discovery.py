"""Consultas temáticas por ecosistema y zona geográfica (descubrimiento).

Cada consulta se compone de partes DECLARADAS (no inferidas):

    base del ecosistema  +  palabra del tipo de señal   ( +  zona geográfica )

La zona geográfica (Región) se añade aparte con el operador OR de Google News,
de modo que "Toda LATAM" cubre los ocho países en una sola búsqueda:
México, Colombia, Chile, Perú, Argentina, Brasil, Costa Rica y Panamá.

El motor trae titulares que coinciden y los etiqueta con la categoría; NO decide
qué empresa menciona cada nota (eso lo hace el operador al curar). Ajustable con
HD_DISCOVERY_<CATEGORIA> (bases separadas por '|').
"""
from __future__ import annotations

import os

# Bases por ecosistema, país-neutrales (el CONTEXTO del sector). La zona va aparte.
CATEGORIA_BASE_DEFAULT: dict[str, list[str]] = {
    "VC": ["venture capital", "fondo de inversión para startups"],
    "Startup": ["startup", "startup tecnológica"],
    "Incubadora": ["aceleradora de startups", "incubadora de startups"],
    "Corporativo": ["corporativo innovación abierta", "corporate venture capital"],
}

# Palabra(s) por tipo de señal (el EVENTO buscado).
TIPO_KEYWORDS: dict[str, str] = {
    "ronda": "ronda de inversión",
    "contratacion": "contratación nuevo ejecutivo",
    "despido": "despidos recorte de personal",
    "lanzamiento": "lanzamiento de producto",
    "queja": "quejas usuarios problema",
    "cambio_sitio": "nuevo sitio web rebranding",
}

# Zona geográfica: LATAM (los ocho países) o un país concreto. La clave "LATAM"
# usa OR para cubrir todos en una sola búsqueda.
PAISES_LATAM = ["México", "Colombia", "Chile", "Perú", "Argentina", "Brasil",
                "Costa Rica", "Panamá"]

REGIONES: dict[str, list[str]] = {"LATAM": PAISES_LATAM, **{p: [p] for p in PAISES_LATAM}}


def _comilla_si_espacio(pais: str) -> str:
    return f'"{pais}"' if " " in pais else pais


def region_clause(region: str) -> str:
    """Cláusula de zona para añadir a la búsqueda, p. ej. (México OR Colombia OR …).

    Región desconocida => sin cláusula (búsqueda global).
    """
    paises = REGIONES.get(region)
    if not paises:
        return ""
    return "(" + " OR ".join(_comilla_si_espacio(p) for p in paises) + ")"


def _bases(categoria: str) -> list[str]:
    override = os.getenv(f"HD_DISCOVERY_{categoria.upper()}")
    if override:
        return [b.strip() for b in override.split("|") if b.strip()]
    return CATEGORIA_BASE_DEFAULT.get(categoria, [])


def queries_para(categoria: str, tipo_evento: str) -> list[tuple[str, str]]:
    """Consultas (texto, tipo_evento) para una categoría y un tipo de señal.

    Compone base del ecosistema + palabra del tipo. La zona geográfica se añade
    aparte en el pipeline (vía QuerySpec.terminos) para no ensuciar la etiqueta.
    """
    kw = TIPO_KEYWORDS.get(tipo_evento, "")
    return [(f"{base} {kw}".strip(), tipo_evento) for base in _bases(categoria)]
