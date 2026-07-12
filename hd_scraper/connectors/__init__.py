"""Arquitectura de conectores intercambiables.

Cada fuente pública se implementa como un ``Connector`` con cuatro métodos:
``search``, ``fetch``, ``normalize`` y ``validate``. El pipeline los orquesta
de forma idéntica sin conocer detalles de la fuente.

Fase 1 (solo estos conectores están planificados):
  - google_news  (Google News RSS) .............. IMPLEMENTADO
  - gdelt        (GDELT DOC 2.0 API) ............. IMPLEMENTADO
  - rss_fijos    (feeds RSS fijos) ............... IMPLEMENTADO
  - job_boards   (Greenhouse / Lever / Ashby) ... IMPLEMENTADO

Fase 1 completa. Regla de la sesión: cada conector debe funcionar de punta a
punta antes de escribir el siguiente. El registro solo expone conectores probados.
"""
from .base import Connector
from .gdelt import GdeltConnector
from .google_news import GoogleNewsConnector
from .job_boards import JobBoardsConnector
from .rss_fijos import RssFijosConnector

# Registro de conectores disponibles. Se irá poblando conforme cada conector
# quede probado de punta a punta (NO antes).
REGISTRY: dict[str, type[Connector]] = {
    "google_news": GoogleNewsConnector,
    "gdelt": GdeltConnector,
    "rss_fijos": RssFijosConnector,
    "job_boards": JobBoardsConnector,
}

__all__ = [
    "Connector",
    "GoogleNewsConnector",
    "GdeltConnector",
    "RssFijosConnector",
    "JobBoardsConnector",
    "REGISTRY",
]
