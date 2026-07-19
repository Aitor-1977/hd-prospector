"""Cliente resiliente del webhook de Capa 0 (POST /webhook/ingesta).

Reintenta con backoff exponencial y registra cada fallo. La función de red se
inyecta (``http_post``) para poder testear sin red.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from . import config

log = logging.getLogger("hd_scraper.ingesta.webhook")

# http_post(url, json, headers, timeout) -> dict
HttpPost = Callable[[str, dict, dict, float], dict]


def _post_httpx(url: str, json: dict, headers: dict, timeout: float) -> dict:
    import httpx

    r = httpx.post(url, json=json, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def enviar(
    payload: dict,
    *,
    url: Optional[str] = None,
    token: Optional[str] = None,
    http_post: Optional[HttpPost] = None,
    max_retries: Optional[int] = None,
    backoff_base: Optional[float] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """POSTea un payload al webhook con reintentos. Lanza si agota los intentos."""
    url = url or config.WEBHOOK_URL
    token = config.INGEST_TOKEN if token is None else token
    http_post = http_post or _post_httpx
    max_retries = config.MAX_RETRIES if max_retries is None else max_retries
    backoff = config.BACKOFF_BASE_S if backoff_base is None else backoff_base

    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Ingest-Token"] = token

    ultima_exc: Optional[Exception] = None
    for intento in range(1, max(1, max_retries) + 1):
        try:
            return http_post(url, payload, headers, config.REQUEST_TIMEOUT_S)
        except Exception as exc:  # red, 5xx, timeout…
            ultima_exc = exc
            log.error("webhook falló (intento %d/%d): %s", intento, max_retries, exc)
            if intento < max_retries:
                sleep(backoff * (2 ** (intento - 1)))  # 1, 2, 4, 8… s
    raise RuntimeError(f"webhook agotó {max_retries} intentos: {ultima_exc}")
