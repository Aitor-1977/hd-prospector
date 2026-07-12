"""Validador del contrato de evidencia.

Único guardián de la tabla `evidencias`. Un registro que no cumple el contrato
NUNCA llega a `evidencias`: se manda a `rechazos` con un motivo. La única
excepción a "completo" es ``fecha_publicacion``: si falta, el registro es
válido pero se marca ``no_fechado`` (no consumible por la API), no se rechaza.
"""
from __future__ import annotations

from dataclasses import dataclass

from dateutil import parser as date_parser

from ..db.models import (
    ESTADO_NO_FECHADO,
    ESTADO_OK,
    ORIGENES_DECLARACION,
    TIPOS_EVENTO,
    EvidenceRecord,
    calcular_hash_dedup,
)

# Campos obligatorios del contrato (fecha_publicacion NO entra aquí: su ausencia
# marca no_fechado, no rechazo).
CAMPOS_OBLIGATORIOS = (
    "cita_textual",
    "fecha_extraccion",
    "url_fuente",
    "nombre_medio",
    "empresa_mencionada",
    "tipo_evento",
    "origen_declaracion",
    "hash_dedup",
)


@dataclass
class ValidationResult:
    ok: bool
    estado: str | None = None   # ESTADO_OK | ESTADO_NO_FECHADO cuando ok=True
    motivo: str | None = None   # razón del rechazo cuando ok=False

    def __bool__(self) -> bool:
        return self.ok


def _es_iso8601(valor: str) -> bool:
    try:
        date_parser.isoparse(valor)
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def validate_record(record: EvidenceRecord) -> ValidationResult:
    """Valida un ``EvidenceRecord`` contra el contrato.

    Devuelve un ``ValidationResult``. Si ``ok`` es ``True``, ``estado`` indica
    si el registro es consumible (``ok``) o ``no_fechado``. Si ``ok`` es
    ``False``, ``motivo`` explica el rechazo (se persiste en `rechazos`).
    """
    # 1) Presencia de campos obligatorios (no vacíos tras strip).
    for campo in CAMPOS_OBLIGATORIOS:
        valor = getattr(record, campo, None)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            return ValidationResult(False, motivo=f"campo_obligatorio_vacio:{campo}")

    # 2) Vocabularios literales.
    if record.tipo_evento not in TIPOS_EVENTO:
        return ValidationResult(False, motivo=f"tipo_evento_invalido:{record.tipo_evento}")
    if record.origen_declaracion not in ORIGENES_DECLARACION:
        return ValidationResult(
            False, motivo=f"origen_declaracion_invalido:{record.origen_declaracion}"
        )

    # 3) fecha_extraccion debe ser ISO 8601 válida.
    if not _es_iso8601(record.fecha_extraccion):
        return ValidationResult(False, motivo="fecha_extraccion_no_iso8601")

    # 4) Integridad del hash_dedup (empresa + URL normalizada).
    esperado = calcular_hash_dedup(record.empresa_mencionada, record.url_fuente)
    if record.hash_dedup != esperado:
        return ValidationResult(False, motivo="hash_dedup_inconsistente")

    # 5) fecha_publicacion: si viene, debe ser ISO 8601; si no, no_fechado.
    if record.fecha_publicacion is None or not str(record.fecha_publicacion).strip():
        return ValidationResult(True, estado=ESTADO_NO_FECHADO)
    if not _es_iso8601(record.fecha_publicacion):
        return ValidationResult(False, motivo="fecha_publicacion_no_iso8601")

    return ValidationResult(True, estado=ESTADO_OK)
