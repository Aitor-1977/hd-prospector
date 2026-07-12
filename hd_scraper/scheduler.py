"""Corridas programadas con APScheduler (cada 12 horas por defecto).

En cada corrida:
  1. purga el crudo vencido (retención 90 días),
  2. encola una consulta por (empresa seguida x tipo_evento) para los
     conectores activos,
  3. procesa la cola de jobs.

Fase 1: solo el conector google_news está activo. Los tipos de evento a barrer
son configurables; por defecto se barren todos los literales del contrato.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .connectors import REGISTRY
from .db.database import Database
from .db.models import TIPOS_EVENTO, QuerySpec
from .jobs import encolar, procesar_pendientes
from .storage.raw_store import purgar_expirados

log = logging.getLogger("hd_scraper.scheduler")


def corrida(db: Database, tipos_evento: tuple[str, ...] | None = None) -> None:
    """Una corrida completa: purga + encolado + procesamiento."""
    purgados = purgar_expirados(db)
    if purgados:
        log.info("crudos purgados por retención: %d", purgados)

    tipos = tipos_evento or tuple(sorted(TIPOS_EVENTO))
    encolados = 0
    for connector, cls in REGISTRY.items():
        if cls.requires_slug:
            # Job boards: se consultan por slug y su tipo_evento es estructural
            # (contratacion). Solo se encolan empresas con slug configurado.
            for empresa, slug in settings.tracked_slugs.items():
                encolar(db, connector,
                        QuerySpec(empresa=empresa, tipo_evento="contratacion", slug=slug))
                encolados += 1
        else:
            for empresa in settings.tracked_empresas:
                for tipo in tipos:
                    encolar(db, connector, QuerySpec(empresa=empresa, tipo_evento=tipo))
                    encolados += 1
    log.info("jobs encolados: %d", encolados)

    procesados = procesar_pendientes(db)
    log.info("jobs procesados: %d", procesados)


def build_scheduler(db: Database) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: corrida(db),
        trigger="interval",
        hours=settings.schedule_hours,
        id="corrida_periodica",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
