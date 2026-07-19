"""Directorio de empresas reales (Wikidata) — VOLUMEN sin depender de noticias.

Google News encuentra empresas CON noticia fresca; muchas empresas reales quedan
fuera. Este módulo trae VOLUMEN real de una base pública y gratuita (Wikidata,
sin clave ni pago): por país e (opcional) vertical, con su sitio web y
descripción. Alimenta la tabla de prospectos con empresas accionables (dominio →
contacto; descripción → vertical/ICP).

Tres capas de robustez (pedidas por el operador):
  1) CASCADA DE RELAJACIÓN: si país+vertical da 0, reintenta país+todas; si sigue
     en 0, con toda LATAM. Devuelve resultados con nota "filtro ampliado
     automáticamente" en vez de un error seco.
  2) CACHÉ (SQLite/Postgres): guarda cada respuesta exitosa con timestamp y sirve
     las consultas idénticas de los últimos 7 días sin llamar al endpoint.
  3) RESILIENCIA: User-Agent que identifica la app + si Wikidata falla o bloquea,
     espera 5 s y reintenta UNA vez; solo si ese reintento falla se avisa.

Sigue siendo captura de HECHOS: se guarda lo que Wikidata publica, sin
interpretar. La red se inyecta (``http_get_json``) para testear con fixtures.
Cobertura honesta: Wikidata cubre mejor empresas notables/medianas que micro-
startups; da volumen real, no exhaustividad.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote_plus

from .db.models import ahora_iso
from .enrich import sugerir_vertical
from .relevance import GIGANTES, _norm

log = logging.getLogger("hd_scraper.directorio")

WDQS = "https://query.wikidata.org/sparql"

# User-Agent que IDENTIFICA la app (Wikidata pide UA descriptivo con contacto).
USER_AGENT = "hd-prospector/1.0 (https://hamaca.digital; contacto@hamaca.digital) directorio-empresas"

# País → identificador Wikidata (QID). Zona LATAM del laboratorio.
PAIS_QID: dict[str, str] = {
    "México": "Q96", "Colombia": "Q739", "Chile": "Q298", "Perú": "Q419",
    "Argentina": "Q414", "Brasil": "Q155", "Costa Rica": "Q800", "Panamá": "Q804",
}

# Valor de región para "toda LATAM" (coincide con la clave de REGIONES).
REGION_LATAM = "LATAM"

# Caché: vigencia de una respuesta guardada.
CACHE_TTL_DIAS = 7

# Resiliencia: espera antes del único reintento.
ESPERA_REINTENTO_S = 5.0

HttpGetJson = Callable[[str], dict]


# ── Construcción de la consulta ──────────────────────────────────────────────

def _sparql(qids: list[str], limite: int) -> str:
    # Empresas (instancia de "empresa" o subclase) de uno o varios países, con
    # sitio web y, si existe, descripción en español. VALUES permite consultar
    # varios países en UNA sola llamada (toda LATAM sin multiplicar peticiones).
    valores = " ".join(f"wd:{q}" for q in qids)
    return (
        "SELECT ?empresa ?empresaLabel ?sitio ?descripcion WHERE { "
        "?empresa wdt:P31/wdt:P279* wd:Q4830453 . "
        f"VALUES ?pais {{ {valores} }} "
        "?empresa wdt:P17 ?pais . "
        "?empresa wdt:P856 ?sitio . "
        "OPTIONAL { ?empresa schema:description ?descripcion . "
        "FILTER(LANG(?descripcion) = \"es\") } "
        "SERVICE wikibase:label { bd:serviceParam wikibase:language \"es,en\". } "
        f"}} LIMIT {int(limite)}"
    )


def url_consulta_qids(qids: list[str], limite: int = 50) -> str:
    return f"{WDQS}?format=json&query={quote_plus(_sparql(qids, limite))}"


def url_consulta(pais: str, limite: int = 50) -> str:
    """URL de la consulta SPARQL para un país. '' si el país no está en la zona."""
    qid = PAIS_QID.get(pais)
    if not qid:
        return ""
    return url_consulta_qids([qid], limite)


def _qids_de_region(region: str) -> tuple[list[str], bool]:
    """Devuelve (qids, es_pais). LATAM -> los 8 países; país -> uno; inválida -> []."""
    if region == REGION_LATAM:
        return list(PAIS_QID.values()), False
    qid = PAIS_QID.get(region)
    if qid:
        return [qid], True
    return [], False


# ── Parseo (filtra gigantes, sin-label y por vertical) ───────────────────────

def _es_qid(texto: str) -> bool:
    t = (texto or "").strip()
    return len(t) > 1 and t[0] == "Q" and t[1:].isdigit()


def _coincide_vertical(nombre: str, descripcion: str, vertical: str) -> bool:
    if not vertical or vertical == "todas":
        return True
    texto = f"{nombre} {descripcion}"
    if sugerir_vertical(texto) == vertical:
        return True
    return _norm(vertical) in _norm(texto)


def parse_empresas(data: dict, vertical: str = "todas") -> list[dict]:
    """Convierte la respuesta SPARQL en empresas. Filtra gigantes y por vertical."""
    filas = ((data or {}).get("results") or {}).get("bindings") or []
    vistos: set[str] = set()
    salida: list[dict] = []
    for b in filas:
        nombre = ((b.get("empresaLabel") or {}).get("value") or "").strip()
        if not nombre or _es_qid(nombre):
            continue
        clave = nombre.lower()
        if clave in vistos:
            continue
        if any(g in _norm(nombre) for g in GIGANTES):   # gigantes no son ICP de HD
            continue
        descripcion = ((b.get("descripcion") or {}).get("value") or "").strip()
        if not _coincide_vertical(nombre, descripcion, vertical):
            continue
        vistos.add(clave)
        salida.append({
            "nombre": nombre,
            "sitio_web": ((b.get("sitio") or {}).get("value") or "").strip(),
            "descripcion": descripcion,
            "vertical_sugerida": sugerir_vertical(f"{nombre} {descripcion}") or "",
            "fuente": "Wikidata",
        })
    return salida


# ── Caché (SQLite/Postgres, 7 días) ──────────────────────────────────────────

def _clave_cache(qids: list[str], limite: int) -> str:
    # La respuesta cruda depende de país(es) + límite (NO de la vertical: el
    # filtro por vertical se aplica al parsear). Orden estable para acertar caché.
    return ",".join(sorted(qids)) + f"|{int(limite)}"


def cache_get(db, clave: str) -> Optional[dict]:
    """Respuesta cacheada si existe y tiene < 7 días; si no, None. Nunca lanza."""
    if db is None:
        return None
    try:
        row = db.fetch_one(
            "SELECT data_json, creado_en FROM directorio_cache WHERE clave = ?", (clave,))
    except Exception as exc:
        log.debug("directorio: cache_get falló: %s", exc)
        return None
    if not row:
        return None
    try:
        creado = datetime.fromisoformat(row["creado_en"])
        if creado.tzinfo is None:
            creado = creado.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if datetime.now(timezone.utc) - creado > timedelta(days=CACHE_TTL_DIAS):
        return None
    try:
        return json.loads(row["data_json"])
    except Exception:
        return None


def cache_put(db, clave: str, data: dict) -> None:
    """Guarda (o refresca) una respuesta exitosa en caché. Nunca lanza."""
    if db is None:
        return
    try:
        db.execute(
            "INSERT INTO directorio_cache (clave, data_json, creado_en) VALUES (?, ?, ?) "
            "ON CONFLICT (clave) DO UPDATE SET data_json = EXCLUDED.data_json, "
            "creado_en = EXCLUDED.creado_en",
            (clave, json.dumps(data, ensure_ascii=False), ahora_iso()),
        )
    except Exception as exc:
        log.debug("directorio: cache_put falló: %s", exc)


# ── Resiliencia: fetch con un reintento tras esperar ─────────────────────────

def _fetch_reintento(url: str, http_get_json: HttpGetJson,
                     sleep: Callable[[float], None]) -> dict:
    """Llama a Wikidata; si falla, espera 5 s y reintenta UNA vez. Puede lanzar."""
    try:
        return http_get_json(url)
    except Exception as exc:
        log.debug("directorio: Wikidata falló (%s); reintento en %ss", exc, ESPERA_REINTENTO_S)
        sleep(ESPERA_REINTENTO_S)
        return http_get_json(url)   # si vuelve a fallar, propaga


def _raw_con_cache(qids: list[str], limite: int, http_get_json: HttpGetJson,
                   db, sleep: Callable[[float], None]) -> tuple[Optional[dict], Optional[str], bool]:
    """Respuesta cruda: primero caché (7 días), si no, red con reintento.

    Devuelve (data | None, error | None, desde_cache).
    """
    clave = _clave_cache(qids, limite)
    cacheada = cache_get(db, clave)
    if cacheada is not None:
        return cacheada, None, True
    try:
        data = _fetch_reintento(url_consulta_qids(qids, limite), http_get_json, sleep)
    except Exception as exc:
        return None, str(exc), False
    cache_put(db, clave, data)
    return data, None, False


# ── Cascada de relajación ────────────────────────────────────────────────────

def buscar_empresas_cascada(
    region: str, vertical: str, http_get_json: HttpGetJson,
    *, db=None, sleep: Optional[Callable[[float], None]] = None, limite: int = 40,
) -> dict:
    """Busca empresas con relajación automática del filtro.

    Orden de intentos:
      país+vertical → país+todas → LATAM+todas   (si la región es un país)
      LATAM+vertical → LATAM+todas               (si la región ya es LATAM)

    El primero con resultados gana. Devuelve dict:
      {empresas, ampliado, nivel, cache, error}. ``ampliado=True`` cuando se
      usó un filtro más amplio que el pedido (para la nota de la UI). ``error``
      no vacío = fallo de red tras el reintento (mostrar aviso). Nunca lanza.
    """
    sleep = sleep or time.sleep
    qids_region, es_pais = _qids_de_region(region)
    if not qids_region:
        return {"empresas": [], "ampliado": False, "nivel": "", "cache": False,
                "error": "region_invalida"}

    todos = list(PAIS_QID.values())
    # (qids, vertical_efectiva, ampliado, etiqueta_nivel)
    intentos: list[tuple[list[str], str, bool, str]] = []
    if es_pais:
        intentos.append((qids_region, vertical, False, f"{region} · {vertical}"))
        if vertical != "todas":
            intentos.append((qids_region, "todas", True, f"{region} · todas las verticales"))
        intentos.append((todos, "todas", True, "toda LATAM · todas las verticales"))
    else:
        intentos.append((todos, vertical, False, f"LATAM · {vertical}"))
        if vertical != "todas":
            intentos.append((todos, "todas", True, "toda LATAM · todas las verticales"))

    for qids, vert, ampliado, nivel in intentos:
        data, err, desde_cache = _raw_con_cache(qids, limite, http_get_json, db, sleep)
        if err:
            return {"empresas": [], "ampliado": False, "nivel": "", "cache": False,
                    "error": err}
        empresas = parse_empresas(data, vert)
        if empresas:
            return {"empresas": empresas, "ampliado": ampliado, "nivel": nivel,
                    "cache": desde_cache, "error": ""}

    # Todos los intentos válidos dieron 0 de verdad (sin error de red).
    return {"empresas": [], "ampliado": False, "nivel": "", "cache": False, "error": ""}


def buscar_empresas(pais: str, vertical: str, http_get_json: HttpGetJson,
                    limite: int = 50, *, sleep: Optional[Callable[[float], None]] = None) -> list[dict]:
    """Búsqueda simple de un país (sin caché ni cascada). Nunca lanza.

    Se conserva por compatibilidad; el endpoint usa ``buscar_empresas_cascada``.
    """
    qids, _ = _qids_de_region(pais)
    if not qids:
        return []
    data, err, _ = _raw_con_cache(qids, limite, http_get_json, None, sleep or time.sleep)
    if err or data is None:
        return []
    return parse_empresas(data, vertical)
