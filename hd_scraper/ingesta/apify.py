"""Conector Apify (texto): LinkedIn / Jobs / News → /webhook/ingesta.

Lee los items de un dataset de Apify (resultado de un actor), los mapea al payload
del webhook y los postea. La descarga y el envío se inyectan para testear sin red.
No interpreta: solo formatea y reenvía el texto público.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from . import config
from .webhook import enviar

log = logging.getLogger("hd_scraper.ingesta.apify")

API = "https://api.apify.com/v2"


def dataset_items_url(dataset_id: str, token: str) -> str:
    return f"{API}/datasets/{dataset_id}/items?clean=true&format=json&token={token}"


def _http_get_json(url: str) -> list:
    import httpx

    r = httpx.get(url, timeout=config.REQUEST_TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def item_a_payload(item: dict) -> Optional[dict]:
    """Mapea un item de Apify (campos comunes de LinkedIn/Jobs/News) al payload.

    Devuelve None si no hay texto aprovechable (no se envía).
    """
    texto = (
        item.get("text") or item.get("description") or item.get("content")
        or item.get("snippet") or item.get("title") or ""
    )
    url = (
        item.get("url") or item.get("link") or item.get("jobUrl")
        or item.get("companyUrl") or item.get("postUrl") or ""
    )
    org = (
        item.get("companyName") or item.get("company") or item.get("organization")
        or item.get("author") or item.get("source") or None
    )
    if not str(texto).strip():
        return None
    return {"texto": str(texto), "url": str(url), "org_name": org}


def correr(
    dataset_id: str,
    *,
    token: Optional[str] = None,
    http_get_json: Optional[Callable[[str], list]] = None,
    enviar_fn: Callable[[dict], dict] = enviar,
) -> dict:
    """Descarga el dataset, mapea y postea cada item al webhook. Nunca peta por un item."""
    token = config.APIFY_TOKEN if token is None else token
    if not token:
        raise RuntimeError("Falta APIFY_TOKEN (configúralo en .env)")
    http_get_json = http_get_json or _http_get_json

    items = http_get_json(dataset_items_url(dataset_id, token)) or []
    enviados = detectadas = 0
    for it in items:
        payload = item_a_payload(it)
        if not payload:
            continue
        try:
            resp = enviar_fn(payload)
            enviados += 1
            detectadas += int((resp or {}).get("senales_detectadas", 0))
        except Exception as exc:
            log.error("apify: no se pudo enviar item (%s): %s", payload.get("url"), exc)
    return {"conector": "apify", "items": len(items), "enviados": enviados,
            "senales_detectadas": detectadas}
