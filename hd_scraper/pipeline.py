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

from .config import settings
from .connectors.base import Connector
from .db.database import Database
from .db.models import (
    ESTADO_NO_FECHADO,
    EvidenceRecord,
    QuerySpec,
    ahora_iso,
    calcular_hash_dedup,
    clave_contenido,
    hash_contenido,
)
from .governance.health import registrar_corrida
from .relevance import calcular_calidad, detectar_empresa, evaluar_relevancia
from .signals import calcular_confianza, detectar_keywords, fuente_confiable
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
    filtrados: int = 0          # descartados por el filtro de relevancia (Captura Inteligente)
    errores: list[str] = field(default_factory=list)

    def resumen(self) -> str:
        return (
            f"{self.connector}[{self.empresa}/{self.tipo_evento}] "
            f"vistos={self.vistos} escritos={self.escritos} "
            f"no_fechados={self.no_fechados} duplicados={self.duplicados} "
            f"rechazados={self.rechazados} filtrados={self.filtrados} "
            f"errores={len(self.errores)}"
        )


def _escribir_evidencia(db: Database, record: EvidenceRecord) -> bool:
    """Inserta en `evidencias` con dedup por hash_dedup. True si insertó."""
    cur = db.execute(
        """
        INSERT INTO evidencias (
            cita_textual, fecha_extraccion, url_fuente, nombre_medio,
            empresa_mencionada, tipo_evento, origen_declaracion, hash_dedup,
            fecha_publicacion, persona_citada, cargo,
            connector, estado, raw_hash, categoria, keywords, confianza,
            clave_contenido, hash_contenido, calidad_captura, creado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (hash_dedup) DO NOTHING
        """,
        (
            record.cita_textual, record.fecha_extraccion, record.url_fuente,
            record.nombre_medio, record.empresa_mencionada, record.tipo_evento,
            record.origen_declaracion, record.hash_dedup, record.fecha_publicacion,
            record.persona_citada, record.cargo, record.connector, record.estado,
            record.raw_hash, record.categoria,
            json.dumps(record.keywords, ensure_ascii=False), record.confianza,
            record.clave_contenido, record.hash_contenido, record.calidad_captura,
            record.creado_en,
        ),
    )
    return cur.rowcount > 0


def _es_duplicado_contenido(db: Database, record: EvidenceRecord) -> bool:
    """Dedup robusto: ¿ya existe una evidencia con la misma identidad de contenido?

    Compara por ``clave_contenido`` (URL canónica/normalizada) O por
    ``hash_contenido`` (título normalizado). El segundo colapsa el mismo artículo
    republicado en URLs distintas. Independiente de ``empresa_mencionada``, por lo
    que un artículo capturado por varias consultas de descubrimiento se guarda
    UNA sola vez.
    """
    condiciones = []
    params: list = []
    if record.clave_contenido:
        condiciones.append("clave_contenido = ?")
        params.append(record.clave_contenido)
    if record.hash_contenido:
        condiciones.append("hash_contenido = ?")
        params.append(record.hash_contenido)
    if not condiciones:
        return False
    fila = db.fetch_one(
        f"SELECT 1 AS x FROM evidencias WHERE {' OR '.join(condiciones)} LIMIT 1",
        params,
    )
    return fila is not None


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
            record.categoria = query.categoria  # etiqueta de ecosistema (descubrimiento por categoría)
            # Extracción objetiva Nivel 1 (Motor A): señales genéricas + confianza.
            record.keywords = detectar_keywords(record.cita_textual)
            record.confianza = calcular_confianza(
                record.fecha_publicacion, record.nombre_medio, record.keywords)

            # --- Captura Inteligente: criterios objetivos (sin IA) ---
            titulo = record.cita_textual
            # Empresa identificable: en consulta dirigida (exact) la empresa la
            # declara el operador; en descubrimiento se detecta un nombre propio.
            detectada = detectar_empresa(titulo)
            empresa_ok = bool(query.exact) or bool(detectada)
            # En descubrimiento por ecosistema el "empresa" de la consulta es un
            # GRUPO TEMÁTICO (p. ej. "(startup OR ...)"), no una compañía. La
            # organización real se DETECTA del titular; sin ella no es prospecto.
            if not query.exact:
                record.empresa_mencionada = detectada or ""
                record.hash_dedup = calcular_hash_dedup(
                    record.empresa_mencionada, record.url_fuente)
            evento_ok = bool(record.keywords)
            fuente_ok = fuente_confiable(record.nombre_medio)

            # Filtro de relevancia: SOLO en descubrimiento amplio (not exact), que
            # es donde entra el ruido (op-eds, tendencias, notas sin empresa,
            # gigantes, crímenes/sucesos). Las consultas dirigidas por nombre de
            # empresa no se filtran. Se EXIGE un evento de negocio: sin él, la nota
            # casi nunca es un prospecto (era la puerta por la que entraba basura).
            if not query.exact:
                relevante, motivo = evaluar_relevancia(titulo, record.keywords, empresa_ok)
                if not relevante:
                    _escribir_rechazo(db, connector.name, motivo,
                                      {"meta": raw.meta, "url": raw.url})
                    res.filtrados += 1
                    continue

            veredicto = connector.validate(record)

            if not veredicto.ok:
                _escribir_rechazo(db, connector.name, veredicto.motivo or "desconocido",
                                  {"meta": raw.meta, "url": raw.url})
                res.rechazados += 1
                continue

            record.estado = veredicto.estado

            # Dedup robusto por identidad de contenido (independiente de empresa).
            record.clave_contenido = clave_contenido(record.url_fuente, raw.meta, titulo)
            record.hash_contenido = hash_contenido(titulo)
            if _es_duplicado_contenido(db, record):
                res.duplicados += 1
                continue

            # Calidad de captura (informativa; no altera el scoring del Motor B).
            record.calidad_captura = calcular_calidad(empresa_ok, evento_ok, fuente_ok)

            # Retención del crudo comprimido, vinculado por hash antes de escribir.
            # Se omite si el disco es efímero (p. ej. Vercel): HD_RAW_ENABLED=0.
            if settings.raw_enabled:
                guardar_crudo(db, record.hash_dedup, raw)
                record.raw_hash = record.hash_dedup

            if _escribir_evidencia(db, record):
                res.escritos += 1
                if veredicto.estado == ESTADO_NO_FECHADO:
                    res.no_fechados += 1
            else:
                res.duplicados += 1  # ya existía por hash_dedup (última barrera)
        except Exception as exc:
            corrida_ok = False
            log.exception("[%s] error procesando item", connector.name)
            res.errores.append(str(exc))

    detalle = res.resumen()
    registrar_corrida(db, connector.name, ok=(corrida_ok and not res.errores),
                      detalle=detalle)
    log.info(detalle)
    return res
