"""Cola de trabajos simple sobre SQLite (sin Redis).

La tabla `jobs` actúa como cola: se encolan corridas (connector + QuerySpec) y
un worker las toma en orden. Suficiente para el volumen de Fase 1; migrable a
una cola real más adelante sin tocar el pipeline.
"""
from __future__ import annotations

import json
import logging

from .connectors import REGISTRY
from .db.database import Database
from .db.models import QuerySpec, ahora_iso
from .pipeline import run_connector

log = logging.getLogger("hd_scraper.jobs")


def encolar(db: Database, connector: str, query: QuerySpec) -> int:
    """Encola un job. Devuelve su id."""
    if connector not in REGISTRY:
        raise ValueError(f"conector desconocido: {connector}")
    ahora = ahora_iso()
    cur = db.execute(
        """INSERT INTO jobs (connector, query_json, estado, creado_en, actualizado_en)
           VALUES (?, ?, 'pending', ?, ?)""",
        (connector, json.dumps(query.to_dict(), ensure_ascii=False), ahora, ahora),
    )
    return cur.lastrowid


def _marcar(db: Database, job_id: int, estado: str, resultado: str | None = None) -> None:
    db.execute(
        "UPDATE jobs SET estado = ?, resultado = ?, actualizado_en = ? WHERE id = ?",
        (estado, resultado, ahora_iso(), job_id),
    )


def procesar_pendientes(db: Database, limite: int = 100) -> int:
    """Procesa jobs pendientes. Devuelve cuántos se procesaron."""
    pendientes = db.fetch_all(
        "SELECT * FROM jobs WHERE estado = 'pending' ORDER BY id ASC LIMIT ?", (limite,)
    )
    procesados = 0
    for job in pendientes:
        _marcar(db, job["id"], "running")
        db.execute(
            "UPDATE jobs SET intentos = intentos + 1 WHERE id = ?", (job["id"],)
        )
        try:
            connector_cls = REGISTRY[job["connector"]]
            query = QuerySpec.from_dict(json.loads(job["query_json"]))
            with connector_cls() as connector:
                res = run_connector(db, connector, query)
            _marcar(db, job["id"], "done", res.resumen())
            procesados += 1
        except Exception as exc:
            log.exception("job %s falló", job["id"])
            _marcar(db, job["id"], "error", str(exc))
    return procesados
