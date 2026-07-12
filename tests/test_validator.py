from hd_scraper.db.models import (
    ESTADO_NO_FECHADO,
    ESTADO_OK,
    EvidenceRecord,
    ahora_iso,
    calcular_hash_dedup,
)
from hd_scraper.validation.validator import validate_record


def _record(**overrides) -> EvidenceRecord:
    empresa = overrides.pop("empresa_mencionada", "Nubank")
    url = overrides.pop("url_fuente", "https://medio.com/nota-1")
    base = dict(
        cita_textual="Nubank levanta una ronda Serie F",
        fecha_extraccion=ahora_iso(),
        url_fuente=url,
        nombre_medio="Bloomberg Línea",
        empresa_mencionada=empresa,
        tipo_evento="ronda",
        origen_declaracion="prensa",
        hash_dedup=calcular_hash_dedup(empresa, url),
        fecha_publicacion="2026-07-01T10:00:00+00:00",
    )
    base.update(overrides)
    return EvidenceRecord(**base)


def test_registro_completo_es_ok():
    r = validate_record(_record())
    assert r.ok and r.estado == ESTADO_OK


def test_falta_campo_obligatorio_se_rechaza():
    r = validate_record(_record(cita_textual="  "))
    assert not r.ok and r.motivo.startswith("campo_obligatorio_vacio:cita_textual")


def test_tipo_evento_invalido_se_rechaza():
    r = validate_record(_record(tipo_evento="opinion"))
    assert not r.ok and r.motivo.startswith("tipo_evento_invalido")


def test_origen_declaracion_invalido_se_rechaza():
    r = validate_record(_record(origen_declaracion="bot"))
    assert not r.ok and r.motivo.startswith("origen_declaracion_invalido")


def test_hash_inconsistente_se_rechaza():
    r = validate_record(_record(hash_dedup="deadbeef"))
    assert not r.ok and r.motivo == "hash_dedup_inconsistente"


def test_sin_fecha_publicacion_es_no_fechado_no_rechazo():
    r = validate_record(_record(fecha_publicacion=None))
    assert r.ok and r.estado == ESTADO_NO_FECHADO


def test_fecha_publicacion_no_iso_se_rechaza():
    r = validate_record(_record(fecha_publicacion="ayer"))
    assert not r.ok and r.motivo == "fecha_publicacion_no_iso8601"
