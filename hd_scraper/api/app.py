"""API interna de SOLO LECTURA (FastAPI).

Expone la evidencia ya validada para que Radar (u otros consumidores) la lean.
No hay endpoints de escritura: la extracción es responsabilidad del pipeline /
scheduler, no de la API.

Regla del contrato: la API SOLO sirve registros consumibles (estado = 'ok').
Los registros ``no_fechado`` existen en la base pero NO son consumibles y no se
devuelven por los endpoints de evidencia.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from ..db.database import get_db
from ..db.models import ESTADO_OK, TIPOS_EVENTO

app = FastAPI(
    title="hd-scraper API (solo lectura)",
    description="Capa de evidencia de Hamaca Digital. Extrae y almacena; no interpreta.",
    version="0.1.0",
)


def _row_a_evidencia(row) -> dict:
    return {
        "id": row["id"],
        "cita_textual": row["cita_textual"],
        "fecha_publicacion": row["fecha_publicacion"],
        "fecha_extraccion": row["fecha_extraccion"],
        "url_fuente": row["url_fuente"],
        "nombre_medio": row["nombre_medio"],
        "empresa_mencionada": row["empresa_mencionada"],
        "persona_citada": row["persona_citada"],
        "cargo": row["cargo"],
        "tipo_evento": row["tipo_evento"],
        "origen_declaracion": row["origen_declaracion"],
        "hash_dedup": row["hash_dedup"],
    }


@app.get("/")
def raiz() -> dict:
    """Banner de bienvenida y mapa de endpoints (la raíz no sirve evidencia)."""
    return {
        "service": "hd-prospector",
        "role": "evidence-extraction",
        "descripcion": "Extrae, normaliza y almacena señales públicas. No interpreta.",
        "estado": "vivo",
        "endpoints": {
            "GET /health": "estado del servicio",
            "GET /evidencias": "evidencia consumible (filtros: empresa, tipo_evento, desde, hasta)",
            "GET /evidencias/{id}": "una evidencia por id",
            "GET /salud-fuentes": "salud por fuente/conector",
            "GET /stats": "contadores agregados",
            "GET /docs": "documentación interactiva (OpenAPI)",
        },
        "nota": "API de solo lectura. La extracción corre en un host aparte (ver README).",
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "hd-prospector", "role": "evidence-extraction"}


@app.get("/evidencias")
def listar_evidencias(
    empresa: Optional[str] = Query(None, description="Filtra por empresa_mencionada"),
    tipo_evento: Optional[str] = Query(None, description="Filtra por tipo_evento"),
    desde: Optional[str] = Query(None, description="fecha_publicacion >= (ISO 8601)"),
    hasta: Optional[str] = Query(None, description="fecha_publicacion <= (ISO 8601)"),
    limite: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    if tipo_evento is not None and tipo_evento not in TIPOS_EVENTO:
        raise HTTPException(400, f"tipo_evento inválido: {tipo_evento}")

    # Solo consumibles: estado = 'ok' (excluye no_fechado por contrato).
    where = ["estado = ?"]
    params: list = [ESTADO_OK]
    if empresa:
        where.append("empresa_mencionada = ?")
        params.append(empresa)
    if tipo_evento:
        where.append("tipo_evento = ?")
        params.append(tipo_evento)
    if desde:
        where.append("fecha_publicacion >= ?")
        params.append(desde)
    if hasta:
        where.append("fecha_publicacion <= ?")
        params.append(hasta)

    clausula = " AND ".join(where)
    db = get_db()
    total = db.fetch_one(f"SELECT COUNT(*) AS n FROM evidencias WHERE {clausula}", params)["n"]
    filas = db.fetch_all(
        f"SELECT * FROM evidencias WHERE {clausula} "
        f"ORDER BY fecha_publicacion DESC, id DESC LIMIT ? OFFSET ?",
        params + [limite, offset],
    )
    return {
        "total": total,
        "limite": limite,
        "offset": offset,
        "items": [_row_a_evidencia(f) for f in filas],
    }


@app.get("/evidencias/{evidencia_id}")
def obtener_evidencia(evidencia_id: int) -> dict:
    db = get_db()
    row = db.fetch_one(
        "SELECT * FROM evidencias WHERE id = ? AND estado = ?", (evidencia_id, ESTADO_OK)
    )
    if row is None:
        raise HTTPException(404, "evidencia no encontrada o no consumible")
    return _row_a_evidencia(row)


@app.get("/salud-fuentes")
def salud_fuentes() -> dict:
    db = get_db()
    filas = db.fetch_all("SELECT * FROM salud_fuentes ORDER BY fuente ASC")
    return {"items": [dict(f) for f in filas]}


@app.get("/stats")
def stats() -> dict:
    db = get_db()
    return {
        "evidencias_consumibles": db.fetch_one(
            "SELECT COUNT(*) AS n FROM evidencias WHERE estado = ?", (ESTADO_OK,))["n"],
        "evidencias_no_fechadas": db.fetch_one(
            "SELECT COUNT(*) AS n FROM evidencias WHERE estado = 'no_fechado'")["n"],
        "rechazos": db.fetch_one("SELECT COUNT(*) AS n FROM rechazos")["n"],
        "fuentes_en_alerta": db.fetch_one(
            "SELECT COUNT(*) AS n FROM salud_fuentes WHERE alerta = 1")["n"],
    }
