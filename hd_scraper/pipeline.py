"""Pipeline de extracción de punta a punta.

Orquesta un conector de forma idéntica para cualquier fuente:

    search(query) -> normalize(raw) -> validate(record)
        -> si válido:   guardar crudo comprimido + escribir en `evidencias` (dedup)
        -> si inválido: escribir en `rechazos` con motivo (NUNCA en evidencias)

Además aplica gobernanza: dedup en escritura por hash_dedup, log de salud por
conector y retención del crudo.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .connectors.base import Connector
from .db.database import Database
from .db.models import ESTADO_NO_FECHADO, EvidenceRecord, QuerySpec, ahora_iso
from .governance.health import registrar_corrida
from .storage.raw_store import guardar_crudo

log = logging.getLogger("hd_scraper.pipeline")


@dataclass
class RunResult:
    connector: str
    empresa: str
    tipo_evento: str
    vistos: int = 0
    escritos: int = 0
    no_fechados: int = 0
    duplicados: int = 0
    rechazados: int = 0
    errores: list[str] = field(default_factory=list)

    def resumen(self) -> str:
        return (
            f"{self.connector}[{self.empresa}/{self.tipo_evento}] "
            f"vistos={self.vistos} escritos={self.escritos} "
            f"no_fechados={self.no_fechados} duplicados={self.duplicados} "
            f"rechazados={self.rechazados} errores={len(self.errores)}"
        )


def _escribir_evidencia(db: Database, record: EvidenceRecord) -> bool:
    """Inserta en `evidencias` con dedup por hash_dedup. True si insertó."""
    cur = db.execute(
        """
        INSERT OR IGNORE INTO evidencias (
            cita_textual, fecha_extraccion, url_fuente, nombre_medio,
            empresa_mencionada, tipo_evento, origen_declaracion, hash_dedup,
            fecha_publicacion, persona_citada, cargo,
            connector, estado, raw_hash, creado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.cita_textual, record.fecha_extraccion, record.url_fuente,
            record.nombre_medio, record.empresa_mencionada, record.tipo_evento,
            record.origen_declaracion, record.hash_dedup, record.fecha_publicacion,
            record.persona_citada, record.cargo, record.connector, record.estado,
            record.raw_hash, record.creado_en,
        ),
    )
    return cur.rowcount > 0


def _drenar_salud_subfuentes(db: Database, connector: Connector) -> None:
    """Persiste en salud_fuentes los eventos por sub-fuente que emitió el conector."""
    for fuente, ok, detalle in connector.drain_health_events():
        registrar_corrida(db, fuente, ok=ok, detalle=detalle)


def _escribir_rechazo(db: Database, connector: str, motivo: str, payload: dict) -> None:
    db.execute(
        "INSERT INTO rechazos (connector, motivo, payload_json, creado_en) VALUES (?, ?, ?, ?)",
        (connector, motivo, json.dumps(payload, ensure_ascii=False, default=str), ahora_iso()),
    )


def run_connector(db: Database, connector: Connector, query: QuerySpec) -> RunResult:
    """Ejecuta un conector para una consulta y persiste el resultado."""
    res = RunResult(connector=connector.name, empresa=query.empresa,
                    tipo_evento=query.tipo_evento)
    corrida_ok = True
    try:
        crudos = list(connector.search(query))
    except Exception as exc:  # fallo de la fuente: salud lo registra
        log.exception("[%s] search falló", connector.name)
        _drenar_salud_subfuentes(db, connector)  # eventos emitidos antes del fallo
        registrar_corrida(db, connector.name, ok=False, detalle=f"search: {exc}")
        res.errores.append(f"search: {exc}")
        return res

    # Salud por sub-fuente (conectores multi-fuente como rss_fijos).
    _drenar_salud_subfuentes(db, connector)

    for raw in crudos:
        res.vistos += 1
        try:
            record = connector.normalize(raw)
            veredicto = connector.validate(record)

            if not veredicto.ok:
                _escribir_rechazo(db, connector.name, veredicto.motivo or "desconocido",
                                  {"meta": raw.meta, "url": raw.url})
                res.rechazados += 1
                continue

            record.estado = veredicto.estado
            # Retención del crudo comprimido, vinculado por hash antes de escribir.
            guardar_crudo(db, record.hash_dedup, raw)
            record.raw_hash = record.hash_dedup

            if _escribir_evidencia(db, record):
                res.escritos += 1
                if veredicto.estado == ESTADO_NO_FECHADO:
                    res.no_fechados += 1
            else:
                res.duplicados += 1  # ya existía por hash_dedup
        except Exception as exc:
            corrida_ok = False
            log.exception("[%s] error procesando item", connector.name)
            res.errores.append(str(exc))

    detalle = res.resumen()
    registrar_corrida(db, connector.name, ok=(corrida_ok and not res.errores),
                      detalle=detalle)
    log.info(detalle)
    return res
