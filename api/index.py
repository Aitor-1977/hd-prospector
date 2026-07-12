"""Entrypoint serverless de Vercel.

Vercel usa el runtime @vercel/python y busca la variable ``app`` (ASGI) en este
archivo. Reexportamos la app FastAPI de solo lectura.

Nota importante: en Vercel solo corre la API de LECTURA. El scraper (scheduler
cada 12 h + escritura en la base) NO puede correr en serverless: no hay proceso
de larga vida y el disco es efímero. Ver README ("Despliegue") para el plan de
extracción en un host always-on + base persistente.
"""
import sys
from pathlib import Path

# Asegura que el paquete hd_scraper (en la raíz del repo) sea importable.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hd_scraper.api.app import app  # noqa: E402

# Vercel toma esta variable como la aplicación ASGI a servir.
__all__ = ["app"]
