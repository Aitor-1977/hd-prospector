"""Clase base abstracta de los conectores.

Contrato de comportamiento de un conector:

    search(query)     -> list[RawItem]     descubre ítems crudos para una consulta
    fetch(url)        -> RawItem            trae el crudo de una URL puntual
    normalize(raw)    -> EvidenceRecord     mapea crudo -> registro del contrato
    validate(record)  -> ValidationResult   aplica el contrato (delegado al validador)

Invariante de todo el sistema: un conector EXTRAE y NORMALIZA; nunca puntúa,
clasifica ni interpreta. ``tipo_evento`` y ``origen_declaracion`` se derivan de
la estructura de la consulta o de la fuente, jamás leyendo/entendiendo el texto.
"""
from __future__ import annotations

import abc
from typing import Iterable

import httpx

from ..config import settings
from ..db.models import EvidenceRecord, QuerySpec, RawItem
from ..governance.rate_limit import RateLimiter
from ..validation.validator import ValidationResult, validate_record


class Connector(abc.ABC):
    """Base intercambiable para toda fuente pública.

    Subclases deben definir ``name`` y los métodos ``search``, ``fetch`` y
    ``normalize``. ``validate`` ya viene implementado delegando en el validador
    del contrato (no debería sobrescribirse salvo razón muy fuerte).
    """

    #: Identificador estable de la fuente (clave en salud_fuentes / jobs).
    name: str = "base"

    #: origen_declaracion estructural por defecto para esta fuente.
    origen_declaracion_default: str = "prensa"

    #: True si la fuente se consulta por slug de empresa (job boards), no por
    #: nombre. El scheduler lo usa para saber qué consulta construir.
    requires_slug: bool = False

    def __init__(self, client: httpx.Client | None = None,
                 rate_limiter: RateLimiter | None = None) -> None:
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=settings.request_timeout_s,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
        self.rate_limiter = rate_limiter or RateLimiter(self.name)
        # Eventos de salud por sub-fuente. Un conector que agrega varias fuentes
        # (p. ej. feeds RSS fijos) emite un evento por fuente; el pipeline los
        # drena y los registra en salud_fuentes. Conectores de una sola fuente
        # no emiten nada y el pipeline registra su salud a nivel de conector.
        self._health_events: list[tuple[str, bool, str]] = []

    # -- Ciclo de vida --------------------------------------------------
    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self) -> "Connector":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- Contrato de conector ------------------------------------------
    @abc.abstractmethod
    def search(self, query: QuerySpec) -> Iterable[RawItem]:
        """Descubre ítems crudos (``RawItem``) para una consulta estructural."""

    @abc.abstractmethod
    def fetch(self, url: str) -> RawItem:
        """Trae el crudo de una URL puntual como ``RawItem``."""

    @abc.abstractmethod
    def normalize(self, raw: RawItem) -> EvidenceRecord:
        """Mapea un ``RawItem`` a un ``EvidenceRecord`` del contrato.

        NO interpreta el contenido: solo reordena y limpia campos ya presentes.
        """

    def validate(self, record: EvidenceRecord) -> ValidationResult:
        """Aplica el contrato. Delegado al validador único del sistema."""
        return validate_record(record)

    # -- Salud por sub-fuente (opcional) -------------------------------
    def emit_health(self, fuente: str, ok: bool, detalle: str = "") -> None:
        """Registra el resultado de una sub-fuente para que el pipeline lo persista."""
        self._health_events.append((fuente, ok, detalle))

    def drain_health_events(self) -> list[tuple[str, bool, str]]:
        """Devuelve y limpia los eventos de salud acumulados."""
        eventos, self._health_events = self._health_events, []
        return eventos
