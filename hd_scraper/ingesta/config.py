"""Configuración de los conectores de ingesta (desde variables de entorno).

CERO hardcoding de credenciales: todo sale de ``.env`` / entorno. Se carga ``.env``
si existe (python-dotenv), sin fallar si no está.
"""
from __future__ import annotations

import os

try:  # carga .env si está disponible; nunca obligatorio
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _f(nombre: str, default: float) -> float:
    try:
        return float(os.getenv(nombre, str(default)))
    except (TypeError, ValueError):
        return default


def _i(nombre: str, default: int) -> int:
    try:
        return int(os.getenv(nombre, str(default)))
    except (TypeError, ValueError):
        return default


# Destino: el webhook de Capa 0 (p. ej. https://hd-prospector.vercel.app/webhook/ingesta).
WEBHOOK_URL: str = os.getenv("HD_WEBHOOK_URL", "http://localhost:8000/webhook/ingesta")
# Token de escritura del webhook (mismo HD_INGEST_TOKEN del backend).
INGEST_TOKEN: str = os.getenv("HD_INGEST_TOKEN", "")

# Credenciales de Apify.
APIFY_TOKEN: str = os.getenv("APIFY_TOKEN", "")

# Resiliencia (reintentos + backoff exponencial).
MAX_RETRIES: int = _i("HD_INGESTA_MAX_RETRIES", 4)
BACKOFF_BASE_S: float = _f("HD_INGESTA_BACKOFF_S", 1.0)
REQUEST_TIMEOUT_S: float = _f("HD_INGESTA_TIMEOUT_S", 30.0)

# Tamaño de ventana (segundos) para agrupar la transcripción de video.
VENTANA_VIDEO_S: int = _i("HD_INGESTA_VENTANA_S", 60)
