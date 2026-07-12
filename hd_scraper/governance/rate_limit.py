"""Rate limiting con backoff exponencial, por fuente.

Cada conector tiene su propio ``RateLimiter``: respeta un intervalo mínimo
entre peticiones y reintenta con backoff exponencial (2s, 4s, 8s, 16s...)
ante errores transitorios (timeouts, 429, 5xx).
"""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

import httpx

from ..config import settings

log = logging.getLogger("hd_scraper.rate_limit")

T = TypeVar("T")

# Códigos HTTP considerados transitorios (merecen reintento con backoff).
TRANSIENT_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    def __init__(self, fuente: str,
                 min_interval_s: float | None = None,
                 max_retries: int | None = None,
                 backoff_base_s: float | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.fuente = fuente
        self.min_interval_s = settings.min_interval_s if min_interval_s is None else min_interval_s
        self.max_retries = settings.max_retries if max_retries is None else max_retries
        self.backoff_base_s = settings.backoff_base_s if backoff_base_s is None else backoff_base_s
        self._sleep = sleep
        self._last_call = 0.0

    def _respetar_intervalo(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval_s:
            self._sleep(self.min_interval_s - elapsed)
        self._last_call = time.monotonic()

    def run(self, fn: Callable[[], T]) -> T:
        """Ejecuta ``fn`` respetando intervalo y reintentando con backoff.

        ``fn`` debe lanzar ``httpx.HTTPStatusError`` / ``httpx.TransportError``
        en fallo. Errores no transitorios se propagan de inmediato.
        """
        intento = 0
        while True:
            self._respetar_intervalo()
            try:
                return fn()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                transient = status in TRANSIENT_STATUS
                if not transient or intento >= self.max_retries:
                    raise
                espera = self._backoff(intento, exc.response)
                log.warning("[%s] HTTP %s, backoff %.1fs (intento %d)",
                            self.fuente, status, espera, intento + 1)
            except httpx.TransportError as exc:
                if intento >= self.max_retries:
                    raise
                espera = self.backoff_base_s * (2 ** intento)
                log.warning("[%s] transporte %s, backoff %.1fs (intento %d)",
                            self.fuente, type(exc).__name__, espera, intento + 1)
            self._sleep(espera)
            intento += 1

    def _backoff(self, intento: int, response: httpx.Response) -> float:
        # Respeta Retry-After si la fuente lo indica.
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self.backoff_base_s * (2 ** intento)
