"""Enriquecimiento de un prospecto: web + discurso corporativo + enlaces.

Dado el nombre de una entidad, intenta:
  1. descubrir su sitio oficial (búsqueda en DuckDuckGo HTML, sin API key),
  2. extraer su discurso corporativo (meta description + titulares/párrafos),
  3. dar enlaces de búsqueda a LinkedIn y Google.

Sobre "no interpreta": el motor ALMACENA el texto que la propia empresa publica
(su about/tesis); no lo puntúa ni deduce nada. El sitio descubierto es un
CANDIDATO: el operador confirma. LinkedIn NO se raspa (términos + bloqueo): solo
se ofrece un enlace de búsqueda.

Las funciones de red reciben un ``http_get`` inyectable (para poder testear con
fixtures y para reutilizar el cliente httpx del endpoint).
"""
from __future__ import annotations

from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, urlsplit

from bs4 import BeautifulSoup

# Hosts que NO son el sitio oficial (redes, directorios, buscadores).
NO_OFICIALES = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "tiktok.com", "crunchbase.com", "wikipedia.org", "google.",
    "duckduckgo.com", "bing.com", "medium.com", "github.com",
)


# Pistas de vertical (HD) por palabras clave. Coincidencia estructural (no
# interpretación profunda): si el texto de la empresa las contiene, se SUGIERE.
# Orden importa: la vertical más específica se evalúa antes (p. ej. "salud
# mental" antes que "healthtech", que también contiene "salud").
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


def _ddg_url(query: str) -> str:
    return "https://html.duckduckgo.com/html/?q=" + quote_plus(query)


def parse_ddg_resultados(html: str) -> list[str]:
    """Extrae las URLs de resultados de la página HTML de DuckDuckGo."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for a in soup.select("a.result__a"):
        href = a.get("href") or ""
        if "uddg=" in href:  # DDG envuelve el destino en /l/?uddg=<encoded>
            qs = parse_qs(urlsplit(href).query)
            destino = qs.get("uddg", [""])[0]
            if destino:
                urls.append(destino)
        elif href.startswith("http"):
            urls.append(href)
    return urls


def elegir_sitio_oficial(urls: list[str]) -> Optional[str]:
    for u in urls:
        host = urlsplit(u).netloc.lower()
        if host and not any(s in host for s in NO_OFICIALES):
            return u
    return None


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
    # dedup preservando orden
    texto = "\n".join(dict.fromkeys(p for p in partes if p))
    return texto[:limite].strip()


def enriquecer(nombre: str, http_get: Callable[[str], str]) -> dict:
    """Orquesta el enriquecimiento. Nunca lanza: devuelve lo que logre + enlaces."""
    resultado = {
        "nombre": nombre,
        "sitio_web": None,
        "discurso": "",
        "vertical_sugerida": None,   # sugerencia objetiva (keyword), NO dato duro
        "vertical_confianza": 0.0,
        "linkedin": linkedin_search_url(nombre),
        "google": google_search_url(nombre),
        "fuentes": [],
        "notas": [],
    }
    try:
        ddg_html = http_get(_ddg_url(f"{nombre} sitio oficial"))
        urls = parse_ddg_resultados(ddg_html)
        sitio = elegir_sitio_oficial(urls)
        resultado["sitio_web"] = sitio
        if sitio:
            try:
                html = http_get(sitio)
                resultado["discurso"] = extraer_discurso(html)
                sug = sugerir_vertical(resultado["discurso"])
                resultado["vertical_sugerida"] = sug
                resultado["vertical_confianza"] = 0.5 if sug else 0.0  # keyword-based: baja
                resultado["fuentes"].append(sitio)
            except Exception as exc:
                resultado["notas"].append(f"no se pudo leer el sitio: {exc}")
        else:
            resultado["notas"].append("no se encontró un sitio oficial claro; usa los enlaces")
    except Exception as exc:
        resultado["notas"].append(f"búsqueda falló: {exc}")
    return resultado
