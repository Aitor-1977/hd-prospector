"""Conector de texto 100% GRATUITO (RSS/HTML): noticias → /webhook/ingesta.

Reemplaza al conector Apify (de pago) por lectura directa de feeds públicos:
Google News RSS (por consulta) o cualquier URL de feed RSS/Atom. Sin claves ni
servicios de pago. Mapea cada entrada a ``{texto, url, org_name}`` y la postea.
La descarga y el envío se inyectan para testear sin red. No interpreta: reenvía.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional
from urllib.parse import quote_plus

import feedparser
from bs4 import BeautifulSoup

from . import config
from .webhook import enviar

log = logging.getLogger("hd_scraper.ingesta.noticias")

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
USER_AGENT = "hd-prospector/1.0 (+https://hamaca.digital) ingesta-noticias"


def google_news_url(query: str, hl: str = "es-419", gl: str = "MX", ceid: str = "MX:es") -> str:
    return (f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}"
            f"&hl={hl}&gl={gl}&ceid={quote_plus(ceid)}")


def _limpiar_html(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


def entrada_a_payload(entry) -> Optional[dict]:
    """Mapea una entrada de feed (título + resumen + fuente) al payload del webhook."""
    titulo = (entry.get("title") or "").strip()
    resumen = _limpiar_html(entry.get("summary") or "")
    texto = (f"{titulo}. {resumen}".strip() if resumen else titulo).strip()
    url = entry.get("link") or ""
    fuente = None
    src = entry.get("source")
    if isinstance(src, dict):
        fuente = src.get("title")
    elif src is not None:
        fuente = getattr(src, "title", None) or str(src)
    if not texto:
        return None
    return {"texto": texto, "url": url, "org_name": fuente}


def parse_feed(xml_text: str) -> list[dict]:
    """Parsea un feed RSS/Atom (texto) y devuelve los payloads no vacíos."""
    feed = feedparser.parse(xml_text)
    payloads: list[dict] = []
    for e in feed.entries:
        p = entrada_a_payload(e)
        if p:
            payloads.append(p)
    return payloads


def _http_get(url: str) -> str:
    import httpx

    r = httpx.get(url, timeout=config.REQUEST_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.text


def correr(
    query: Optional[str] = None,
    feed_url: Optional[str] = None,
    *,
    limite: int = 25,
    http_get: Optional[Callable[[str], str]] = None,
    enviar_fn: Callable[[dict], dict] = enviar,
) -> dict:
    """Lee un feed (por consulta de Google News o URL directa) y postea cada nota.

    Un item que falla al enviar no tumba el lote (se registra y se sigue).
    """
    http_get = http_get or _http_get
    if feed_url:
        url = feed_url
    elif query:
        url = google_news_url(query)
    else:
        raise ValueError("indica --query (Google News) o --feed (URL de RSS)")

    xml = http_get(url)
    payloads = parse_feed(xml)[: max(1, limite)]
    enviados = detectadas = 0
    for p in payloads:
        try:
            resp = enviar_fn(p)
            enviados += 1
            detectadas += int((resp or {}).get("senales_detectadas", 0))
        except Exception as exc:
            log.error("noticias: no se pudo enviar (%s): %s", p.get("url"), exc)
    return {"conector": "noticias", "items": len(payloads), "enviados": enviados,
            "senales_detectadas": detectadas}
