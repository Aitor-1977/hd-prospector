"""Enriquecimiento de un prospecto: web + discurso corporativo + enlaces.

Dado el nombre de una entidad, intenta:
  1. RESOLVER su sitio oficial con varias estrategias y un nivel de CONFIANZA
     (búsqueda + adivinación de dominio verificada), no una sola fuente frágil.
  2. extraer su discurso corporativo (meta description + titulares/párrafos).
  3. dar enlaces de búsqueda a LinkedIn y Google.

Sobre "no interpreta": el motor ALMACENA el texto que la propia empresa publica;
no lo puntúa ni deduce nada. El sitio es un CANDIDATO con nivel de confianza; el
operador confirma. LinkedIn NO se raspa (términos + bloqueo): solo enlace.

Las funciones de red reciben un ``http_get`` inyectable (para testear con
fixtures y reutilizar el cliente httpx del endpoint). ``http_get(url)`` devuelve
el cuerpo (str) o lanza excepción si falla.
"""
from __future__ import annotations

import logging
import os
import time
import unicodedata
from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, urlsplit

from bs4 import BeautifulSoup

log = logging.getLogger("hd_scraper.enrich")

# Hosts que NO son el sitio oficial (redes, directorios, buscadores).
NO_OFICIALES = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "tiktok.com", "crunchbase.com", "wikipedia.org", "google.",
    "duckduckgo.com", "bing.com", "medium.com", "github.com", "gob.mx", "youtu.be",
)

# TLDs candidatos para adivinar el dominio (LATAM + genéricos frecuentes en startups).
TLDS_CANDIDATOS = (".com", ".com.mx", ".mx", ".io", ".co", ".vc", ".ai", ".lat", ".app")

# Niveles de confianza del sitio resuelto.
CONF_CONFIRMADA = "confirmada"      # 🟢 dominio coincide con el nombre o verificado
CONF_PROBABLE = "probable"          # 🟡 hay un candidato pero no se pudo confirmar
CONF_NO = "no_confirmada"           # 🟠 nada claro; usar enlaces


# ── Normalización / coincidencia de nombre ──────────────────────────────────

def _sin_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _tokens(nombre: str) -> list[str]:
    """Tokens significativos del nombre (>=3 chars, sin genéricos)."""
    stop = {"de", "la", "el", "los", "las", "inc", "sa", "sapi", "cv", "ltd",
            "the", "and", "com", "grupo", "capital", "ventures", "partners"}
    limpio = _sin_acentos(nombre).lower()
    return [t for t in "".join(c if c.isalnum() else " " for c in limpio).split()
            if len(t) >= 3 and t not in stop]


def _label_dominio(nombre: str) -> str:
    """Etiqueta base para adivinar dominio: nombre sin acentos ni separadores."""
    return "".join(c for c in _sin_acentos(nombre).lower() if c.isalnum())


def dominios_candidatos(nombre: str) -> list[str]:
    """URLs candidatas a sitio oficial, de la más a la menos probable."""
    label = _label_dominio(nombre)
    if not label:
        return []
    toks = _tokens(nombre)
    primer = "".join(c for c in _sin_acentos(toks[0]).lower() if c.isalnum()) if toks else label
    bases = list(dict.fromkeys([label, primer]))  # dedup preservando orden
    return [f"https://{b}{tld}" for b in bases for tld in TLDS_CANDIDATOS]


def _host_coincide(host: str, nombre: str) -> bool:
    """True si algún token del nombre aparece en la etiqueta principal del host."""
    host = (host or "").lower().lstrip("www.")
    etiqueta = host.split(".")[0] if host else ""
    return any(t in etiqueta or etiqueta in t for t in _tokens(nombre))


def _texto_menciona(texto: str, nombre: str) -> bool:
    t = _sin_acentos(texto or "").lower()
    toks = _tokens(nombre)
    return bool(toks) and any(tok in t for tok in toks)


# ── Extracción ──────────────────────────────────────────────────────────────

def _titulo_y_meta(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    partes = []
    if soup.title and soup.title.string:
        partes.append(soup.title.string)
    for attrs in ({"name": "description"}, {"property": "og:site_name"}, {"property": "og:title"}):
        m = soup.find("meta", attrs=attrs)
        if m and m.get("content"):
            partes.append(m["content"])
    return " ".join(partes)


def extraer_discurso(html: str, limite: int = 1500) -> str:
    """Extrae texto representativo del sitio: meta description + titulares/párrafos."""
    soup = BeautifulSoup(html, "html.parser")
    partes: list[str] = []
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            partes.append(meta["content"].strip())
    for tag in soup.find_all(["h1", "h2", "p"]):
        t = tag.get_text(" ", strip=True)
        if len(t) >= 40:
            partes.append(t)
        if sum(len(x) for x in partes) > limite:
            break
    texto = "\n".join(dict.fromkeys(p for p in partes if p))
    return texto[:limite].strip()


# ── Búsqueda (fallback) ─────────────────────────────────────────────────────

def _ddg_lite_url(query: str) -> str:
    # La versión "lite" es mucho más tolerante a peticiones desde servidor que
    # html.duckduckgo.com (que suele responder challenge/vacío a los bots).
    return "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)


def parse_resultados_busqueda(html: str) -> list[str]:
    """Extrae URLs de una página de resultados (soporta DDG html y lite)."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if "uddg=" in href:  # DDG envuelve el destino en /l/?uddg=<encoded>
            destino = parse_qs(urlsplit(href).query).get("uddg", [""])[0]
            if destino:
                urls.append(destino)
        elif href.startswith("http") and "duckduckgo.com" not in href:
            urls.append(href)
    return urls


def elegir_sitio_oficial(urls: list[str]) -> Optional[str]:
    for u in urls:
        host = urlsplit(u).netloc.lower()
        if host and not any(s in host for s in NO_OFICIALES):
            return u
    return None


def extraer_snippets_busqueda(html: str, limite: int = 800) -> str:
    """Extrae el texto DESCRIPTIVO (snippets) de una página de resultados.

    Camino 1 del auto-investiga: muchos sitios modernos se arman con JavaScript y
    NO exponen texto al servidor, así que leer su HTML da vacío. Los snippets del
    buscador sí traen una descripción utilizable de la empresa. Determinista.
    """
    soup = BeautifulSoup(html, "html.parser")
    partes: list[str] = []
    celdas = soup.find_all("td", class_="result-snippet") or soup.find_all("td")
    for td in celdas:
        t = td.get_text(" ", strip=True)
        if len(t) >= 40 and "duckduckgo" not in t.lower():
            partes.append(t)
        if sum(len(x) for x in partes) > limite:
            break
    texto = "\n".join(dict.fromkeys(partes))
    return texto[:limite].strip()


# ── Resolver (núcleo del arreglo) ───────────────────────────────────────────

def resolver_sitio(nombre: str, http_get: Callable[[str], str]) -> tuple[Optional[str], str, list[str]]:
    """Resuelve el sitio oficial con varias estrategias + nivel de confianza.

    Devuelve (sitio_web | None, confianza, notas). Nunca lanza.
    Estrategia:
      1) Adivinar dominio y VERIFICAR (determinista): si responde y menciona el
         nombre -> confirmada; si responde pero no lo menciona -> probable.
      2) Búsqueda (DDG lite): primer resultado no-social; confirmada si el host
         coincide con el nombre, probable si no.
    """
    notas: list[str] = []
    probable: Optional[str] = None

    # Presupuesto de tiempo: en serverless probar ~18 dominios en serie puede
    # agotar el límite de la función. Acotamos a los candidatos más probables y a
    # un presupuesto de reloj; si se agota, pasamos a la búsqueda (más certera).
    t0 = time.monotonic()
    presupuesto = float(os.getenv("HD_ENRICH_BUDGET_S", "6"))
    max_candidatos = int(os.getenv("HD_ENRICH_MAX_CANDIDATOS", "8"))

    # 1) Adivinación de dominio + verificación.
    for cand in dominios_candidatos(nombre)[:max_candidatos]:
        if time.monotonic() - t0 > presupuesto:
            notas.append("adivinación de dominio acotada por tiempo")
            break
        try:
            html = http_get(cand)
        except Exception:
            log.debug("enrich: dominio candidato no responde: %s", cand)
            continue
        host = urlsplit(cand).netloc
        confirma = _host_coincide(host, nombre) and _texto_menciona(_titulo_y_meta(html), nombre)
        log.debug("enrich: candidato %s respondió; confirma=%s", cand, confirma)
        if confirma:
            return cand, CONF_CONFIRMADA, notas
        if probable is None:
            probable = cand  # respondió 200 pero sin confirmar el nombre

    # 2) Búsqueda como respaldo.
    try:
        html = http_get(_ddg_lite_url(f"{nombre} sitio oficial"))
        sitio = elegir_sitio_oficial(parse_resultados_busqueda(html))
        log.debug("enrich: búsqueda devolvió sitio=%s", sitio)
        if sitio:
            host = urlsplit(sitio).netloc
            conf = CONF_CONFIRMADA if _host_coincide(host, nombre) else CONF_PROBABLE
            return sitio, conf, notas
    except Exception as exc:
        notas.append(f"búsqueda falló: {exc}")
        log.debug("enrich: búsqueda falló: %s", exc)

    if probable:
        notas.append("dominio probable sin confirmar el nombre en el contenido")
        return probable, CONF_PROBABLE, notas

    notas.append("no se pudo confirmar un sitio; usa los enlaces")
    return None, CONF_NO, notas


# ── Vertical ────────────────────────────────────────────────────────────────

VERTICAL_HINTS: dict[str, tuple[str, ...]] = {
    "fintech": ("fintech", "pagos", "crédito", "banca", "financ", "wallet", "remesas"),
    "edtech": ("edtech", "educación", "aprendizaje", "cursos", "e-learning", "estudiantes"),
    "salud mental": ("salud mental", "terapia", "bienestar emocional", "psicolog"),
    "healthtech": ("healthtech", "salud", "clínic", "pacientes", "telemedicina"),
    "logística agrícola": ("logística", "agrícola", "agtech", "agro", "cadena de suministro", "campo"),
    "identidad": ("identidad digital", "kyc", "verificación de identidad", "onboarding de identidad"),
}


def sugerir_vertical(texto: str) -> Optional[str]:
    """Sugiere una vertical HD si el texto contiene sus palabras clave. Estructural."""
    t = (texto or "").lower()
    for vertical, claves in VERTICAL_HINTS.items():
        if any(c in t for c in claves):
            return vertical
    return None


def linkedin_search_url(nombre: str) -> str:
    return "https://www.linkedin.com/search/results/companies/?keywords=" + quote_plus(nombre)


def google_search_url(nombre: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(nombre)


# ── Orquestación ────────────────────────────────────────────────────────────

def enriquecer(nombre: str, http_get: Callable[[str], str]) -> dict:
    """Orquesta el enriquecimiento. Nunca lanza: devuelve lo que logre + enlaces."""
    resultado = {
        "nombre": nombre,
        "sitio_web": None,
        "sitio_confianza": CONF_NO,   # confirmada | probable | no_confirmada
        "discurso": "",
        "vertical_sugerida": None,
        "vertical_confianza": 0.0,
        "linkedin": linkedin_search_url(nombre),
        "google": google_search_url(nombre),
        "fuentes": [],
        "notas": [],
    }

    sitio, confianza, notas = resolver_sitio(nombre, http_get)
    resultado["sitio_web"] = sitio
    resultado["sitio_confianza"] = confianza
    resultado["notas"].extend(notas)

    if sitio:
        try:
            html = http_get(sitio)
            resultado["discurso"] = extraer_discurso(html)
            sug = sugerir_vertical(resultado["discurso"])
            resultado["vertical_sugerida"] = sug
            resultado["vertical_confianza"] = 0.5 if sug else 0.0
            resultado["fuentes"].append(sitio)
        except Exception as exc:
            resultado["notas"].append(f"no se pudo leer el sitio: {exc}")
            log.debug("enrich: no se pudo leer %s: %s", sitio, exc)

    # Camino 1: si no se logró discurso del sitio (web armada en JavaScript que
    # no expone texto al servidor, o sin sitio confirmado), usar la DESCRIPCIÓN
    # de los resultados de búsqueda. Sigue siendo captura objetiva: guardamos el
    # texto que el buscador ya muestra, sin interpretarlo.
    if not resultado["discurso"]:
        try:
            html = http_get(_ddg_lite_url(f"{nombre} empresa"))
            snip = extraer_snippets_busqueda(html)
            if snip:
                resultado["discurso"] = snip
                resultado["fuentes"].append("búsqueda")
                if not resultado["vertical_sugerida"]:
                    sug = sugerir_vertical(snip)
                    resultado["vertical_sugerida"] = sug
                    resultado["vertical_confianza"] = 0.4 if sug else 0.0
                resultado["notas"].append(
                    "descripción tomada de resultados de búsqueda "
                    "(la web no expone texto al servidor)")
        except Exception as exc:
            resultado["notas"].append(f"búsqueda de descripción falló: {exc}")
            log.debug("enrich: búsqueda de descripción falló: %s", exc)

    log.debug("enrich: nombre=%r -> sitio=%r confianza=%s", nombre, sitio, confianza)
    return resultado
