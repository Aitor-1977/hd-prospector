"""API interna de SOLO LECTURA (FastAPI).

Expone la evidencia ya validada para que Radar (u otros consumidores) la lean.
No hay endpoints de escritura: la extracción es responsabilidad del pipeline /
scheduler, no de la API.

Regla del contrato: la API SOLO sirve registros consumibles (estado = 'ok').
Los registros ``no_fechado`` existen en la base pero NO son consumibles y no se
devuelven por los endpoints de evidencia.
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from ..config import settings
from ..connectors import REGISTRY
from ..db.database import get_db
from ..db.models import CATEGORIAS, ESTADO_OK, TIPOS_EVENTO, QuerySpec, ahora_iso
import httpx

from .. import directorio, hunter
from ..analisis import analizar
from ..engine.rule_engine import RuleEngine
from ..engine.schemas import Prospecto, SeñalCapa0
from ..ingesta import noticias as _noticias
from ..contacto import dominio_de, rutas_contacto
from ..discovery import REGIONES, VERTICALES_HD, queries_para, region_clause
from ..enrich import enriquecer, google_search_url, linkedin_search_url, sugerir_vertical
from ..pipeline import run_connector
from ..relevance import detectar_empresa, evaluar_relevancia
from ..signals import detectar_keywords
from ..prospectos import nuevo_prospecto, upsert_prospecto
from ..validation.validator import validate_prospecto

app = FastAPI(
    title="hd-prospector API",
    description=(
        "Capa de evidencia + prospectos de los cuatro ecosistemas. Extrae y "
        "almacena; no interpreta. Evidencia: solo lectura. Prospectos: lectura "
        "pública + intake autenticada del operador."
    ),
    version="0.2.0",
)


# --- Intake de prospectos (escritura autenticada del operador) -----------

class ProspectoIn(BaseModel):
    nombre: str
    categoria: str
    vertical: Optional[str] = None
    sitio_web: Optional[str] = None
    linkedin: Optional[str] = None
    discurso_corporativo: Optional[str] = None
    tipo_discurso: Optional[str] = None
    url_perfil: Optional[str] = None
    fuente_discurso: Optional[str] = None
    fecha_captura: Optional[str] = None


def _exigir_token(token: Optional[str]) -> None:
    """Autoriza la intake. Sin HD_INGEST_TOKEN configurado, la escritura se apaga."""
    esperado = settings.ingest_token
    if not esperado:
        raise HTTPException(503, "intake deshabilitada: configura HD_INGEST_TOKEN")
    if not token or not hmac.compare_digest(token, esperado):
        raise HTTPException(401, "token de ingesta inválido")


def _alta(payload: ProspectoIn) -> dict:
    record = nuevo_prospecto(
        payload.nombre, payload.categoria,
        vertical=payload.vertical,
        sitio_web=payload.sitio_web,
        linkedin=payload.linkedin,
        discurso_corporativo=payload.discurso_corporativo,
        tipo_discurso=payload.tipo_discurso,
        url_perfil=payload.url_perfil,
        fuente_discurso=payload.fuente_discurso,
        fecha_captura=payload.fecha_captura,
    )
    veredicto = validate_prospecto(record)
    if not veredicto.ok:
        raise HTTPException(400, veredicto.motivo)
    return upsert_prospecto(get_db(), record)


def _keywords(row) -> list:
    try:
        return json.loads(row["keywords"]) if row["keywords"] else []
    except (ValueError, TypeError):
        return []


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
        "categoria": row["categoria"],
        "keywords": _keywords(row),
        "confianza": row["confianza"],
        "calidad_captura": row["calidad_captura"],  # informativa (Captura Inteligente)
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
        "ecosistemas": sorted(CATEGORIAS),
        "endpoints": {
            "GET /health": "estado del servicio",
            "GET /evidencias": "evidencia consumible (filtros: empresa, tipo_evento, desde, hasta)",
            "GET /evidencias/{id}": "una evidencia por id",
            "GET /corpus": "corpus estable Motor A→B (empresa·fuente·fecha·texto·keywords·confianza)",
            "GET /prospectos": "prospectos por ecosistema (filtros: categoria, q, con_discurso)",
            "GET /prospectos/categorias": "conteo de prospectos por ecosistema",
            "GET /prospectos/{id}": "un prospecto por id (incluye Thick Data)",
            "GET /prospectos/export.csv": "descarga los prospectos en CSV (filtro: categoria)",
            "GET /prospectos/export.md": "descarga los prospectos en Markdown (filtro: categoria)",
            "GET /prospectos/export.json": "descarga los prospectos en JSON (filtro: categoria)",
            "POST /prospectos": "alta de prospecto (requiere X-Ingest-Token)",
            "POST /scrape": "rastreo bajo demanda de una empresa (requiere X-Ingest-Token)",
            "POST /enrich": "descubre web + discurso + enlaces de un nombre (requiere X-Ingest-Token)",
            "GET /informe": "informe profundo: prioriza empresas por scoring + Deuda Cultural™ + ICP (filtro: categoria)",
            "GET /informe.md": "descarga el informe profundo en Markdown (filtros: categoria|categorias)",
            "GET /informe.csv": "descarga el informe profundo en CSV (filtros: categoria|categorias)",
            "POST /informe/guardar": "genera y GUARDA la investigación de las categorías elegidas (requiere X-Ingest-Token)",
            "GET /informes": "lista las investigaciones guardadas",
            "GET /informes/{id}.md": "descarga una investigación guardada (Markdown)",
            "DELETE /informes/{id}": "borra una investigación guardada (requiere X-Ingest-Token)",
            "POST /analizar": "análisis profundo determinista de un título o señales (scoring/Deuda/ICP/decisor)",
            "POST /verificar-contacto": "verifica el correo del decisor con Hunter (requiere X-Ingest-Token y HUNTER_API_KEY)",
            "POST /directorio": "trae empresas reales de Wikidata (base pública) y las guarda como prospectos (requiere X-Ingest-Token)",
            "POST /webhook/ingesta": "Capa 0: evalúa texto con el motor de reglas determinista y persiste señales (requiere X-Ingest-Token)",
            "POST /ingesta/noticias": "Capa 0 EN LA APP: lee RSS (Google News o feed) y procesa cada nota (requiere X-Ingest-Token)",
            "GET /senales-capa0": "lista las señales de Capa 0 registradas (filtro: nivel_alerta)",
            "GET /admin": "panel web: buscar señales, revisar y dar de alta prospectos",
            "GET /salud-fuentes": "salud por fuente/conector",
            "GET /stats": "contadores agregados",
            "GET /docs": "documentación interactiva (OpenAPI)",
        },
        "nota": "Lectura pública; escritura (prospectos/scrape) autenticada con X-Ingest-Token.",
    }


@app.get("/health")
def health() -> dict:
    # Expone el motor de base activo (postgres|sqlite) para diagnóstico.
    # NO revela credenciales: solo el dialecto.
    return {
        "status": "ok",
        "service": "hd-prospector",
        "role": "evidence-extraction",
        "db": get_db().dialect,
    }


@app.get("/evidencias")
def listar_evidencias(
    empresa: Optional[str] = Query(None, description="Filtra por empresa_mencionada"),
    tipo_evento: Optional[str] = Query(None, description="Filtra por tipo_evento"),
    categoria: Optional[str] = Query(None, description="Filtra por ecosistema (descubrimiento)"),
    desde: Optional[str] = Query(None, description="fecha_publicacion >= (ISO 8601)"),
    hasta: Optional[str] = Query(None, description="fecha_publicacion <= (ISO 8601)"),
    limite: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    limpio: bool = Query(False, description="Deriva la organización del titular y omite ruido/basura (para la UI)"),
) -> dict:
    if tipo_evento is not None and tipo_evento not in TIPOS_EVENTO:
        raise HTTPException(400, f"tipo_evento inválido: {tipo_evento}")
    if categoria is not None and categoria not in CATEGORIAS:
        raise HTTPException(400, f"categoria inválida: {categoria}")

    # Solo consumibles: estado = 'ok' (excluye no_fechado por contrato).
    where = ["estado = ?"]
    params: list = [ESTADO_OK]
    if empresa:
        where.append("empresa_mencionada = ?")
        params.append(empresa)
    if tipo_evento:
        where.append("tipo_evento = ?")
        params.append(tipo_evento)
    if categoria:
        where.append("categoria = ?")
        params.append(categoria)
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
    # La organización real se DERIVA del titular (la evidencia). En datos viejos,
    # empresa_mencionada podía ser el término de búsqueda; aquí se corrige para
    # mostrar el sujeto correcto. Con ``limpio`` además se omite el ruido/basura
    # que quedó en la base antes de reforzar los filtros (no altera la tabla).
    items: list = []
    for f in filas:
        item = _row_a_evidencia(f)
        titulo = item["cita_textual"]
        org = detectar_empresa(titulo)
        if limpio:
            ok, _ = evaluar_relevancia(titulo, item.get("keywords") or [], bool(org),
                                       exigir_evento=False)
            if not org or not ok:
                continue
        item["organizacion"] = org or (item.get("empresa_mencionada") or "")
        items.append(item)
    return {
        "total": total,
        "limite": limite,
        "offset": offset,
        "items": items,
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


def _row_a_corpus(row) -> dict:
    """Contrato del corpus (Motor A → Motor B / RadarHD). Solo hechos objetivos.

    ``calidad_captura`` es una extensión ADITIVA y retrocompatible del contrato
    (misma versión ``motor_a.corpus.v1``): una etiqueta OBJETIVA de la captura
    (Alta|Media|Baja), no una interpretación. Los consumidores previos que no la
    esperan la ignoran; RadarHD la usa como contexto para reducir falsos
    positivos. NO se añade Deuda Cultural™, ICP ni hipótesis (eso es Motor B).
    """
    return {
        "empresa": row["empresa_mencionada"],
        "fuente": row["nombre_medio"],
        "fecha": row["fecha_publicacion"],
        "texto": row["cita_textual"],
        "url": row["url_fuente"],
        "keywords": _keywords(row),
        "confianza": row["confianza"],
        "calidad_captura": row["calidad_captura"],
        "categoria": row["categoria"],
        "tipo_evento": row["tipo_evento"],
        "hash": row["hash_dedup"],
    }


@app.get("/corpus")
def corpus(
    empresa: Optional[str] = Query(None),
    categoria: Optional[str] = Query(None, description="Ecosistema (VC|Startup|Incubadora|Corporativo)"),
    tipo_evento: Optional[str] = Query(None),
    min_confianza: float = Query(0.0, ge=0.0, le=1.0, description="Confianza mínima"),
    desde: Optional[str] = Query(None, description="fecha >= (ISO 8601)"),
    hasta: Optional[str] = Query(None, description="fecha <= (ISO 8601)"),
    limite: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Corpus de evidencia verificable (Motor A). Contrato estable para RadarHD.

    Solo hechos observables: empresa, fuente, fecha, texto, url, keywords
    (señales Nivel 1 objetivas) y confianza. NO incluye Deuda Cultural™, ICP ni
    hipótesis: eso lo aplica el Motor B (RadarHD) al consumir este corpus.
    """
    if categoria is not None and categoria not in CATEGORIAS:
        raise HTTPException(400, f"categoria inválida: {categoria}")
    if tipo_evento is not None and tipo_evento not in TIPOS_EVENTO:
        raise HTTPException(400, f"tipo_evento inválido: {tipo_evento}")

    where = ["estado = ?", "confianza >= ?"]
    params: list = [ESTADO_OK, min_confianza]
    for col, val in (("empresa_mencionada", empresa), ("categoria", categoria),
                     ("tipo_evento", tipo_evento)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
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
        f"ORDER BY confianza DESC, fecha_publicacion DESC, id DESC LIMIT ? OFFSET ?",
        params + [limite, offset],
    )
    return {"contrato": "motor_a.corpus.v1", "total": total, "limite": limite,
            "offset": offset, "items": [_row_a_corpus(f) for f in filas]}


@app.get("/salud-fuentes")
def salud_fuentes() -> dict:
    db = get_db()
    filas = db.fetch_all("SELECT * FROM salud_fuentes ORDER BY fuente ASC")
    return {"items": [dict(f) for f in filas]}


def _row_a_prospecto(row) -> dict:
    return {
        "id": row["id"],
        "nombre": row["nombre"],
        "categoria": row["categoria"],
        "vertical": row["vertical"],
        "sitio_web": row["sitio_web"],
        "linkedin": row["linkedin"],
        "discurso_corporativo": row["discurso_corporativo"],
        "tipo_discurso": row["tipo_discurso"],
        "url_perfil": row["url_perfil"],
        "fuente_discurso": row["fuente_discurso"],
        "fecha_captura": row["fecha_captura"],
        "creado_en": row["creado_en"],
        "actualizado_en": row["actualizado_en"],
    }


@app.get("/prospectos")
def listar_prospectos(
    categoria: Optional[str] = Query(None, description="Filtra por ecosistema (VC|Startup|Incubadora|Corporativo)"),
    q: Optional[str] = Query(None, description="Búsqueda por nombre (subcadena)"),
    con_discurso: Optional[bool] = Query(None, description="Solo con/sin Thick Data"),
    limite: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    if categoria is not None and categoria not in CATEGORIAS:
        raise HTTPException(400, f"categoria inválida: {categoria}")

    where: list[str] = ["1 = 1"]
    params: list = []
    if categoria:
        where.append("categoria = ?")
        params.append(categoria)
    if q:
        where.append("LOWER(nombre) LIKE ?")
        params.append(f"%{q.lower()}%")
    if con_discurso is True:
        where.append("discurso_corporativo IS NOT NULL AND TRIM(discurso_corporativo) <> ''")
    elif con_discurso is False:
        where.append("(discurso_corporativo IS NULL OR TRIM(discurso_corporativo) = '')")

    clausula = " AND ".join(where)
    db = get_db()
    total = db.fetch_one(f"SELECT COUNT(*) AS n FROM prospectos WHERE {clausula}", params)["n"]
    filas = db.fetch_all(
        f"SELECT * FROM prospectos WHERE {clausula} ORDER BY nombre ASC LIMIT ? OFFSET ?",
        params + [limite, offset],
    )
    return {"total": total, "limite": limite, "offset": offset,
            "items": [_row_a_prospecto(f) for f in filas]}


@app.get("/prospectos/categorias")
def prospectos_por_categoria() -> dict:
    db = get_db()
    filas = db.fetch_all(
        "SELECT categoria, COUNT(*) AS n FROM prospectos GROUP BY categoria")
    conteo = {c: 0 for c in sorted(CATEGORIAS)}
    for f in filas:
        conteo[f["categoria"]] = f["n"]
    return {"categorias": conteo}


_EXPORT_COLS = ["id", "nombre", "categoria", "vertical", "sitio_web", "linkedin",
                "tipo_discurso", "url_perfil", "fuente_discurso", "fecha_captura",
                "discurso_corporativo", "creado_en", "actualizado_en"]


def _prospectos_filtrados(categoria: Optional[str]) -> list:
    if categoria is not None and categoria not in CATEGORIAS:
        raise HTTPException(400, f"categoria inválida: {categoria}")
    db = get_db()
    if categoria:
        return db.fetch_all(
            "SELECT * FROM prospectos WHERE categoria = ? ORDER BY nombre ASC", (categoria,))
    return db.fetch_all("SELECT * FROM prospectos ORDER BY categoria, nombre ASC")


@app.get("/prospectos/export.csv")
def export_csv(categoria: Optional[str] = Query(None, description="Filtra por ecosistema")) -> Response:
    """Descarga los prospectos como CSV (abrible en Excel/Sheets)."""
    filas = _prospectos_filtrados(categoria)
    buf = io.StringIO()
    buf.write("﻿")  # BOM: Excel abre bien el UTF-8
    w = csv.writer(buf)
    w.writerow(_EXPORT_COLS)
    for f in filas:
        w.writerow([f[c] if f[c] is not None else "" for c in _EXPORT_COLS])
    nombre = f"prospectos_{categoria or 'todos'}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/prospectos/export.json")
def export_json(categoria: Optional[str] = Query(None, description="Filtra por ecosistema")) -> Response:
    """Descarga los prospectos como JSON."""
    filas = _prospectos_filtrados(categoria)
    datos = [_row_a_prospecto(f) for f in filas]
    nombre = f"prospectos_{categoria or 'todos'}.json"
    return Response(json.dumps(datos, ensure_ascii=False, indent=2),
                    media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


def _prospectos_a_markdown(filas: list) -> str:
    out = ["# Prospectos — hd-prospector", "",
           f"_Exportado: {ahora_iso()}_ · {len(filas)} prospecto(s)", ""]
    categoria_actual = None
    for f in filas:
        if f["categoria"] != categoria_actual:
            categoria_actual = f["categoria"]
            out += [f"## {categoria_actual}", ""]
        out.append(f"### {f['nombre']}")
        if f["vertical"]:
            out.append(f"- **Vertical:** {f['vertical']}")
        if f["sitio_web"]:
            out.append(f"- **Web:** <{f['sitio_web']}>")
        if f["linkedin"]:
            out.append(f"- **LinkedIn:** <{f['linkedin']}>")
        if f["tipo_discurso"]:
            out.append(f"- **Tipo de discurso:** {f['tipo_discurso']}")
        fuente, url = f["fuente_discurso"] or "", f["url_perfil"] or ""
        if fuente or url:
            detalle = " · ".join(x for x in (fuente, f"<{url}>" if url else "") if x)
            out.append(f"- **Fuente:** {detalle}")
        if f["fecha_captura"]:
            out.append(f"- **Capturado:** {f['fecha_captura']}")
        out.append("")
        disc = (f["discurso_corporativo"] or "").strip()
        if disc:
            out += [f"> {linea}" for linea in disc.splitlines()] + [""]
    return "\n".join(out)


@app.get("/prospectos/export.md")
def export_md(categoria: Optional[str] = Query(None, description="Filtra por ecosistema")) -> Response:
    """Descarga los prospectos como documento Markdown (agrupado por ecosistema)."""
    filas = _prospectos_filtrados(categoria)
    nombre = f"prospectos_{categoria or 'todos'}.md"
    return Response(_prospectos_a_markdown(filas), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/prospectos/{prospecto_id}")
def obtener_prospecto(prospecto_id: int) -> dict:
    db = get_db()
    row = db.fetch_one("SELECT * FROM prospectos WHERE id = ?", (prospecto_id,))
    if row is None:
        raise HTTPException(404, "prospecto no encontrado")
    return _row_a_prospecto(row)


@app.post("/prospectos", status_code=201)
def crear_prospecto(payload: ProspectoIn,
                    x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Alta/actualización de un prospecto (intake autenticada del operador)."""
    _exigir_token(x_ingest_token)
    return _alta(payload)


@app.post("/prospectos/bulk", status_code=201)
def crear_prospectos_bulk(payloads: list[ProspectoIn] = Body(...),
                          x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Alta masiva de prospectos. Reporta el resultado por cada uno."""
    _exigir_token(x_ingest_token)
    resultados = []
    for p in payloads:
        try:
            r = _alta(p)
            resultados.append({"nombre": p.nombre, "categoria": p.categoria, **r})
        except HTTPException as exc:
            resultados.append({"nombre": p.nombre, "categoria": p.categoria,
                               "ok": False, "accion": "rechazado", "motivo": exc.detail})
    return {"total": len(payloads), "resultados": resultados}


# --- Scraping bajo demanda (descubrimiento manual) -----------------------

# Conectores rápidos aptos para correr dentro de una función serverless (una
# petición HTTP por conector). Se excluyen los que consultan muchas fuentes
# (rss_fijos) o requieren slug (job_boards) para no agotar el tiempo de la función.
CONECTORES_SCRAPE = ("google_news", "gdelt")


class ScrapeIn(BaseModel):
    # Modo por nombre: empresa. Modo descubrimiento por ecosistema: categoria.
    empresa: Optional[str] = None
    categoria: Optional[str] = None
    tipo_evento: str = "ronda"
    vertical: str = "todas"   # vertical HD: fintech, edtech, healthtech, salud mental…
    region: str = "LATAM"     # zona geográfica: LATAM (8 países) o un país
    connectors: list[str] = list(CONECTORES_SCRAPE)


def _correr_query(db, query, connectors) -> list[dict]:
    salida = []
    for cname in connectors:
        cls = REGISTRY.get(cname)
        if cls is None or cls.requires_slug or cname not in CONECTORES_SCRAPE:
            salida.append({"connector": cname, "error": "no disponible para scraping bajo demanda"})
            continue
        with cls() as conn:
            res = run_connector(db, conn, query)
        salida.append({
            "connector": cname, "consulta": query.empresa, "vistos": res.vistos,
            "escritos": res.escritos, "no_fechados": res.no_fechados,
            "duplicados": res.duplicados, "rechazados": res.rechazados,
            "filtrados": res.filtrados,  # descartados por el filtro de relevancia
            "errores": len(res.errores),
        })
    return salida


@app.post("/scrape")
def scrape(payload: ScrapeIn, x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Rastrea señales AL MOMENTO y las guarda (evidencia). Dos modos:

    - Por ecosistema (``categoria``): corre las consultas temáticas declaradas
      del ecosistema y etiqueta la evidencia con esa categoría. Para descubrir.
    - Por nombre (``empresa``): rastrea una empresa concreta. Para profundizar.
    """
    _exigir_token(x_ingest_token)
    if payload.region not in REGIONES:
        raise HTTPException(400, f"region inválida: {payload.region}")
    zona = region_clause(payload.region)  # p. ej. (México OR Colombia OR …)
    db = get_db()
    resultados: list[dict] = []

    if payload.categoria:
        if payload.categoria not in CATEGORIAS:
            raise HTTPException(400, f"categoria inválida: {payload.categoria}")
        if payload.tipo_evento not in TIPOS_EVENTO:
            raise HTTPException(400, f"tipo_evento inválido: {payload.tipo_evento}")
        if payload.vertical not in VERTICALES_HD:
            raise HTTPException(400, f"vertical inválida: {payload.vertical}")
        # Descubrimiento: Google News sobre ecosistema + vertical (HD) + señal,
        # acotado a la zona geográfica (terminos).
        #
        # Presupuesto de tiempo: cada consulta es una llamada de red. En serverless
        # la función tiene un límite corto; si nos pasamos, el navegador recibe un
        # "Internal Server Error" en TEXTO (no JSON) y la UI truena. Por eso cortamos
        # las consultas restantes al agotar el presupuesto y devolvemos lo logrado
        # con parcial=True (siempre JSON válido).
        presupuesto_s = float(os.getenv("HD_SCRAPE_BUDGET_S", "7"))
        t0 = time.monotonic()
        parcial = False
        consultas = queries_para(payload.categoria, payload.tipo_evento, payload.vertical)
        for i, (termino, tipo) in enumerate(consultas):
            if i > 0 and time.monotonic() - t0 > presupuesto_s:
                parcial = True
                break
            query = QuerySpec(empresa=termino, tipo_evento=tipo, terminos=zona,
                              categoria=payload.categoria, exact=False)
            resultados += _correr_query(db, query, ["google_news"])
        modo = {"modo": "categoria", "categoria": payload.categoria,
                "tipo_evento": payload.tipo_evento, "vertical": payload.vertical,
                "region": payload.region, "parcial": parcial}
    elif payload.empresa and payload.empresa.strip():
        if payload.tipo_evento not in TIPOS_EVENTO:
            raise HTTPException(400, f"tipo_evento inválido: {payload.tipo_evento}")
        query = QuerySpec(empresa=payload.empresa.strip(), tipo_evento=payload.tipo_evento,
                          terminos=zona)
        resultados += _correr_query(db, query, payload.connectors)
        modo = {"modo": "empresa", "empresa": payload.empresa.strip(),
                "tipo_evento": payload.tipo_evento, "region": payload.region}
    else:
        raise HTTPException(400, "indica una empresa o una categoria")

    total = sum(r.get("escritos", 0) for r in resultados)
    return {**modo, "total_escritos": total, "resultados": resultados}


# --- PWA: instalable como app (base para generar el APK con PWABuilder) ---

_STATIC = Path(__file__).resolve().parent / "static"

_MANIFEST = {
    "name": "hd-prospector · Radar",
    "short_name": "Radar",
    "description": "Descubre y cura prospectos de VC, Startup, Incubadora y Corporativo.",
    "start_url": "/admin",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0b0c0e",
    "theme_color": "#2563eb",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}

_SW_JS = (
    "self.addEventListener('install', e => self.skipWaiting());\n"
    "self.addEventListener('activate', e => self.clients.claim());\n"
    "self.addEventListener('fetch', e => {});\n"
)


@app.get("/manifest.webmanifest")
def manifest() -> Response:
    import json as _json
    return Response(_json.dumps(_MANIFEST), media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> Response:
    return Response(_SW_JS, media_type="application/javascript")


@app.get("/icon-192.png")
def icon_192() -> FileResponse:
    return FileResponse(_STATIC / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
def icon_512() -> FileResponse:
    return FileResponse(_STATIC / "icon-512.png", media_type="image/png")


@app.get("/apple-touch-icon.png")
def apple_icon() -> FileResponse:
    return FileResponse(_STATIC / "apple-touch-icon.png", media_type="image/png")


class EnrichIn(BaseModel):
    nombre: str


@app.post("/enrich")
def enrich(payload: EnrichIn, x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Enriquece un prospecto: descubre su web, extrae su discurso y da enlaces.

    Best-effort: nunca falla; devuelve lo que logre más enlaces a LinkedIn/Google.
    LinkedIn NO se raspa (términos): solo se da un enlace de búsqueda.
    """
    _exigir_token(x_ingest_token)
    if not payload.nombre.strip():
        raise HTTPException(400, "nombre vacío")

    with httpx.Client(timeout=settings.request_timeout_s,
                      headers={"User-Agent": settings.user_agent},
                      follow_redirects=True) as client:
        def http_get(url: str) -> str:
            r = client.get(url)
            r.raise_for_status()
            return r.text
        return enriquecer(payload.nombre.strip(), http_get)


# --- Análisis profundo (INTERPRETACIÓN determinista, sin IA ni red) --------
#
# El operador pidió que hd-prospector, además de capturar, entregue análisis
# profundo (scoring A/B/C, Deuda Cultural™, ICP, decisor). Se hace de forma
# determinista sobre las señales YA capturadas (ver hd_scraper/analisis.py).

_ORDEN_SCORING = {"A": 0, "B": 1, "C": 2}


def _analizar_evidencia(row, sitios: Optional[dict] = None) -> dict:
    """Aplica el análisis profundo a una fila de evidencia y arma la tarjeta.

    ``sitios`` (opcional): mapa {empresa_lower: sitio_web} de prospectos ya
    guardados, para derivar dominio y rutas de contacto (hipótesis).
    """
    kws = _keywords(row)
    titulo = row["cita_textual"] or ""
    empresa = (row["empresa_mencionada"] or "").strip() or (detectar_empresa(titulo) or "")
    vertical = sugerir_vertical(titulo) or ""
    a = analizar(
        kws, vertical=vertical, confianza=row["confianza"] or 0.0,
        calidad=row["calidad_captura"] or "Baja", categoria=row["categoria"] or "",
    )
    # Rutas de contacto (hipótesis): si tenemos el sitio del prospecto, usamos su
    # dominio; el nombre del decisor no se conoce aún, así que van buzones genéricos.
    sitio = (sitios or {}).get(empresa.lower(), "") if empresa else ""
    contacto = rutas_contacto(sitio, "") if sitio else {
        "dominio": "", "emails_candidatos": [], "email_sugerido": "",
        "verificado": False, "nota": "sin sitio confirmado; usa LinkedIn/Google o enriquece el prospecto",
    }
    return {
        "empresa": empresa,
        "categoria": row["categoria"],
        "vertical": vertical,
        "titulo": titulo,
        "url_fuente": row["url_fuente"],
        "nombre_medio": row["nombre_medio"],
        "fecha_publicacion": (row["fecha_publicacion"] or "")[:10],
        "keywords": kws,
        "confianza": row["confianza"],
        "calidad_captura": row["calidad_captura"],
        **a,
        "contacto": contacto,
        "linkedin": linkedin_search_url(empresa) if empresa else "",
        "google": google_search_url(empresa) if empresa else "",
    }


def _analizar_prospecto(row) -> dict:
    """Analiza un prospecto guardado (p. ej. del directorio): empresa real sin
    evento de prensa. Deriva señales de su descripción; ICP por vertical; contacto
    por su sitio. Scoring típico C (sin dolor), pero es una empresa REAL con datos."""
    nombre = (row["nombre"] or "").strip()
    discurso = row["discurso_corporativo"] or ""
    vertical = (row["vertical"] or "") or (sugerir_vertical(f"{nombre} {discurso}") or "")
    kws = detectar_keywords(discurso)
    a = analizar(kws, vertical=vertical, confianza=0.4, calidad="Baja")
    sitio = row["sitio_web"] or ""
    contacto = rutas_contacto(sitio, "") if sitio else {
        "dominio": "", "emails_candidatos": [], "email_sugerido": "",
        "verificado": False, "nota": "sin sitio; enriquece el prospecto"}
    return {
        "empresa": nombre, "categoria": row["categoria"], "vertical": vertical,
        "titulo": (discurso[:140] or f"{nombre} — empresa del directorio"),
        "url_fuente": sitio, "nombre_medio": (row["fuente_discurso"] or "directorio"),
        "fecha_publicacion": (row["fecha_captura"] or "")[:10],
        "keywords": kws, "confianza": 0.4, "calidad_captura": "Baja",
        **a, "contacto": contacto,
        "linkedin": linkedin_search_url(nombre) if nombre else "",
        "google": google_search_url(nombre) if nombre else "",
    }


def _cats_validas(categoria: Optional[str], categorias: Optional[str]) -> list[str]:
    """Lista de ecosistemas pedidos (una, varias por coma, o todas si vacío). Valida."""
    crudas: list[str] = []
    if categorias:
        crudas = [c.strip() for c in categorias.split(",") if c.strip()]
    elif categoria:
        crudas = [categoria]
    for c in crudas:
        if c not in CATEGORIAS:
            raise HTTPException(400, f"categoria inválida: {c}")
    # dedup preservando orden
    return list(dict.fromkeys(crudas))


def _construir_informe(categorias: Optional[list[str]], limite: int) -> dict:
    """Calcula el informe profundo (compartido por /informe y las exportaciones).

    ``categorias`` vacío/None = todos los ecosistemas; si trae varios, filtra por
    todos ellos (IN).
    """
    db = get_db()
    cats = list(categorias or [])
    if cats:
        marc = ",".join("?" for _ in cats)
        clausula = f"estado = ? AND categoria IN ({marc})"
        params: list = [ESTADO_OK, *cats]
    else:
        clausula, params = "estado = ?", [ESTADO_OK]
    filas = db.fetch_all(
        f"SELECT * FROM evidencias WHERE {clausula} ORDER BY creado_en DESC LIMIT 500",
        tuple(params),
    )

    # Sitios web de prospectos ya guardados (para derivar dominio/contacto).
    sitios: dict[str, str] = {}
    for p in db.fetch_all("SELECT nombre, sitio_web FROM prospectos WHERE sitio_web IS NOT NULL AND sitio_web <> ''"):
        sitios[(p["nombre"] or "").strip().lower()] = p["sitio_web"]

    # Una tarjeta por empresa: nos quedamos con la de mejor scoring y luego ICP.
    mejor: dict[str, dict] = {}
    for row in filas:
        t = _analizar_evidencia(row, sitios)
        clave = (t["empresa"] or t["titulo"]).lower()
        prev = mejor.get(clave)
        if prev is None or (
            (_ORDEN_SCORING.get(t["scoring"], 9), -t["score_icp"])
            < (_ORDEN_SCORING.get(prev["scoring"], 9), -prev["score_icp"])
        ):
            mejor[clave] = t

    # Empresas del DIRECTORIO (prospectos sin noticia): dan volumen real. Se
    # añaden si no aparecieron ya por una noticia (esas tienen prioridad, traen evento).
    if cats:
        marc = ",".join("?" for _ in cats)
        pclaus, pparams = f"categoria IN ({marc})", list(cats)
    else:
        pclaus, pparams = "1=1", []
    for p in db.fetch_all(
        f"SELECT * FROM prospectos WHERE {pclaus} ORDER BY creado_en DESC LIMIT 500",
        tuple(pparams),
    ):
        t = _analizar_prospecto(p)
        clave = (t["empresa"] or t["titulo"]).lower()
        if clave not in mejor:
            mejor[clave] = t

    tarjetas = sorted(
        mejor.values(),
        key=lambda x: (_ORDEN_SCORING.get(x["scoring"], 9), -x["score_icp"]),
    )[:limite]

    resumen = {"A": 0, "B": 0, "C": 0}
    for t in tarjetas:
        resumen[t["scoring"]] = resumen.get(t["scoring"], 0) + 1

    return {
        "categorias": cats or sorted(CATEGORIAS),
        "categoria": ", ".join(cats) if cats else "Todas",
        "total": len(tarjetas),
        "resumen_scoring": resumen,
        "prospectos": tarjetas,
    }


@app.get("/informe")
def informe(
    categoria: Optional[str] = Query(None, description="Un ecosistema (VC|Startup|Incubadora|Corporativo)"),
    categorias: Optional[str] = Query(None, description="Varios ecosistemas separados por coma"),
    limite: int = Query(50, ge=1, le=200),
) -> dict:
    """Informe profundo: prioriza las empresas capturadas por scoring + Deuda + ICP.

    Interpreta (de forma determinista) la evidencia consumible: una tarjeta por
    empresa (la señal más fuerte gana), ordenada A→B→C y por Score ICP. Acepta una
    o varias categorías (o todas si no se indica).
    """
    return _construir_informe(_cats_validas(categoria, categorias), limite)


def _informe_a_markdown(inf: dict) -> str:
    s = inf["resumen_scoring"]
    lineas = [
        "# Informe profundo — hd-prospector",
        "",
        f"- Ecosistema(s): **{inf.get('categoria') or 'Todas'}**",
        f"- Empresas: **{inf['total']}**  ·  A: {s.get('A',0)}  ·  B: {s.get('B',0)}  ·  C: {s.get('C',0)}",
        "",
        "> Análisis determinista sobre hechos capturados. Los correos son "
        "hipótesis **sin verificar**.",
        "",
    ]
    for i, t in enumerate(inf["prospectos"], 1):
        c = t.get("contacto") or {}
        lineas += [
            f"## {i}. {t['empresa'] or '(sin nombre)'} · {t['scoring']} · ICP {t['score_icp']} · intensidad {t.get('intensidad','')}",
            f"- Titular: {t['titulo']}",
        ]
        if t.get("tipo_deuda"):
            sec = f" (secundaria: {t['deuda_secundaria']})" if t.get("deuda_secundaria") else ""
            lineas.append(f"- Deuda Cultural™: **{t['tipo_deuda']}**{sec} — {t.get('deuda_razon','')}")
        if t.get("angulo_conversacion"):
            lineas.append(f"- Ángulo de conversación: {t['angulo_conversacion']}")
        lineas.append(f"- Decisor sugerido: **{t['decisor_sugerido']}**")
        if c.get("email_sugerido"):
            lineas.append(f"- Correo candidato (sin verificar): `{c['email_sugerido']}`")
        meta = " · ".join(x for x in (t.get("nombre_medio",""), t.get("vertical",""), t.get("categoria",""), t.get("fecha_publicacion","")) if x)
        if meta:
            lineas.append(f"- {meta}")
        if t.get("url_fuente"):
            lineas.append(f"- Fuente: {t['url_fuente']}")
        lineas.append("")
    return "\n".join(lineas)


@app.get("/informe.md")
def informe_md(
    categoria: Optional[str] = Query(None),
    categorias: Optional[str] = Query(None),
    limite: int = Query(50, ge=1, le=200),
) -> Response:
    """Descarga el informe profundo como Markdown."""
    md = _informe_a_markdown(_construir_informe(_cats_validas(categoria, categorias), limite))
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="informe-hd.md"'})


@app.get("/informe.csv")
def informe_csv(
    categoria: Optional[str] = Query(None),
    categorias: Optional[str] = Query(None),
    limite: int = Query(50, ge=1, le=200),
) -> Response:
    """Descarga el informe profundo como CSV."""
    inf = _construir_informe(_cats_validas(categoria, categorias), limite)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["empresa", "scoring", "score_icp", "intensidad", "tipo_deuda",
                "deuda_secundaria", "angulo_conversacion", "decisor_sugerido",
                "email_candidato", "email_verificado", "vertical", "categoria",
                "titulo", "fecha_publicacion", "fuente", "url_fuente"])
    for t in inf["prospectos"]:
        c = t.get("contacto") or {}
        w.writerow([
            t.get("empresa",""), t.get("scoring",""), t.get("score_icp",""),
            t.get("intensidad",""), t.get("tipo_deuda",""), t.get("deuda_secundaria",""),
            t.get("angulo_conversacion",""), t.get("decisor_sugerido",""),
            c.get("email_sugerido",""), "no", t.get("vertical",""), t.get("categoria",""),
            t.get("titulo",""), t.get("fecha_publicacion",""), t.get("nombre_medio",""),
            t.get("url_fuente",""),
        ])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="informe-hd.csv"'})


class GuardarInformeIn(BaseModel):
    categorias: Optional[str] = None   # coma-separadas; vacío = todas
    limite: int = 50


@app.post("/informe/guardar")
def guardar_informe(payload: GuardarInformeIn,
                    x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Genera el informe de las categorías pedidas y lo GUARDA (snapshot con su
    Markdown). Autenticado (escribe). Devuelve el id para recuperarlo/descargarlo."""
    _exigir_token(x_ingest_token)
    cats = _cats_validas(None, payload.categorias)
    inf = _construir_informe(cats, payload.limite)
    md = _informe_a_markdown(inf)
    titulo = f"Investigación · {inf['categoria']} · {inf['total']} empresas"
    db = get_db()
    rid = db.insert_returning_id(
        """INSERT INTO informes_guardados
             (titulo, categorias, total, resumen_json, markdown, creado_en)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (titulo, ", ".join(inf["categorias"]), inf["total"],
         json.dumps(inf["resumen_scoring"]), md, ahora_iso()),
    )
    return {"id": rid, "titulo": titulo, "total": inf["total"],
            "resumen_scoring": inf["resumen_scoring"]}


@app.get("/informes")
def listar_informes(limite: int = Query(50, ge=1, le=200)) -> dict:
    """Lista las investigaciones guardadas (sin el cuerpo Markdown)."""
    db = get_db()
    filas = db.fetch_all(
        "SELECT id, titulo, categorias, total, resumen_json, creado_en "
        "FROM informes_guardados ORDER BY id DESC LIMIT ?", (limite,))
    items = []
    for f in filas:
        d = dict(f)
        try:
            d["resumen_scoring"] = json.loads(d.pop("resumen_json") or "{}")
        except (ValueError, TypeError):
            d["resumen_scoring"] = {}
        items.append(d)
    return {"total": len(items), "items": items}


@app.get("/informes/{informe_id}.md")
def descargar_informe_guardado(informe_id: int) -> Response:
    """Descarga el Markdown de una investigación guardada."""
    row = get_db().fetch_one(
        "SELECT markdown FROM informes_guardados WHERE id = ?", (informe_id,))
    if not row:
        raise HTTPException(404, "investigación no encontrada")
    return Response(row["markdown"] or "", media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="investigacion-{informe_id}.md"'})


@app.delete("/informes/{informe_id}")
def borrar_informe_guardado(informe_id: int,
                            x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Borra una investigación guardada. Autenticado (escribe)."""
    _exigir_token(x_ingest_token)
    db = get_db()
    if not db.fetch_one("SELECT id FROM informes_guardados WHERE id = ?", (informe_id,)):
        raise HTTPException(404, "investigación no encontrada")
    db.execute("DELETE FROM informes_guardados WHERE id = ?", (informe_id,))
    return {"ok": True, "id": informe_id}


class AnalizarIn(BaseModel):
    titulo: Optional[str] = None
    keywords: Optional[list[str]] = None
    vertical: str = ""
    confianza: float = 0.0
    calidad: str = "Baja"
    categoria: str = ""
    dominio: str = ""            # opcional: para rutas de contacto (hipótesis)
    nombre_decisor: str = ""     # opcional: afina los patrones de correo


@app.post("/analizar")
def analizar_endpoint(payload: AnalizarIn) -> dict:
    """Análisis profundo bajo demanda de un título o de señales dadas.

    Público (solo interpreta datos que se le pasan; no escribe ni raspa). Si se
    da ``titulo`` y no ``keywords``, deriva las señales del título de forma
    determinista. Devuelve scoring, Deuda Cultural™ (hipótesis), ICP, decisor y,
    si se da ``dominio``, correos candidatos (sin verificar).
    """
    kws = payload.keywords
    if kws is None:
        kws = detectar_keywords(payload.titulo or "")
    vertical = payload.vertical or (sugerir_vertical(payload.titulo or "") or "")
    a = analizar(kws, vertical=vertical, confianza=payload.confianza,
                 calidad=payload.calidad, categoria=payload.categoria)
    salida = {"keywords": kws, "vertical": vertical, **a}
    if payload.dominio:
        salida["contacto"] = rutas_contacto(payload.dominio, payload.nombre_decisor)
    return salida


class VerificarContactoIn(BaseModel):
    dominio: str = ""
    sitio_web: str = ""          # alternativa: se deriva el dominio del sitio
    nombre_decisor: str = ""


@app.post("/verificar-contacto")
def verificar_contacto(payload: VerificarContactoIn,
                       x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Verifica el correo del decisor con Hunter (BAJO DEMANDA, consume cuota).

    Autenticado (X-Ingest-Token) porque gasta cuota de pago. Devuelve el correo
    verificado si Hunter lo confirma; si no hay clave o falla, cae a la HIPÓTESIS
    determinista (correos candidatos sin verificar) para no dejar al operador sin
    nada. Nunca lanza por fallos de Hunter.
    """
    _exigir_token(x_ingest_token)
    dominio = dominio_de(payload.dominio or payload.sitio_web) or (payload.dominio or "").strip().lower()
    hipotesis = rutas_contacto(dominio, payload.nombre_decisor)

    if not hunter.disponible(settings.hunter_api_key):
        return {"verificado": False, "modo": "hipotesis",
                "nota": "verificación no configurada (falta HUNTER_API_KEY); se muestran candidatos sin verificar)",
                "hipotesis": hipotesis}

    with httpx.Client(timeout=settings.request_timeout_s,
                      headers={"User-Agent": settings.user_agent},
                      follow_redirects=True) as client:
        def http_get_json(url: str) -> dict:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
        res = hunter.enriquecer_contacto(dominio, payload.nombre_decisor,
                                         settings.hunter_api_key, http_get_json)
    return {"modo": "hunter", **res, "hipotesis": hipotesis}


class DirectorioIn(BaseModel):
    region: str = "México"       # país (un QID de Wikidata; LATAM no aplica aquí)
    categoria: str = "Startup"   # ecosistema al que se asignan (VC|Startup|…)
    vertical: str = "todas"
    limite: int = 40


@app.post("/directorio")
def directorio_endpoint(payload: DirectorioIn,
                        x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Trae EMPRESAS REALES de Wikidata (base pública, gratis) y las guarda como
    prospectos. Da VOLUMEN sin depender de que haya una noticia fresca.

    Autenticado (escribe prospectos). Cada empresa entra con su vertical sugerida,
    sitio web y descripción; se deduplica por nombre+categoría. Zona = un país.
    """
    _exigir_token(x_ingest_token)
    qids, _es_pais = directorio._qids_de_region(payload.region)
    if not qids:
        raise HTTPException(400, f"region debe ser un país o '{directorio.REGION_LATAM}': "
                                 f"{sorted(directorio.PAIS_QID)}")
    if payload.categoria not in CATEGORIAS:
        raise HTTPException(400, f"categoria inválida: {payload.categoria}")
    if payload.vertical not in VERTICALES_HD:
        raise HTTPException(400, f"vertical inválida: {payload.vertical}")

    limite = max(1, min(int(payload.limite or 40), 100))
    db = get_db()
    with httpx.Client(timeout=settings.request_timeout_s,
                      headers={"User-Agent": directorio.USER_AGENT,
                               "Accept": "application/sparql-results+json"},
                      follow_redirects=True) as client:
        def http_get_json(url: str) -> dict:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
        res = directorio.buscar_empresas_cascada(
            payload.region, payload.vertical, http_get_json, db=db, limite=limite)

    # Fallo de red tras el reintento: aviso claro (no se llegó a resultados).
    if res.get("error"):
        return {
            "region": payload.region, "categoria": payload.categoria,
            "vertical": payload.vertical, "fuente": "Wikidata",
            "encontradas": 0, "nuevos": 0, "actualizados": 0, "ampliado": False,
            "nota": ("Wikidata no respondió (falló también el reintento). "
                     "Intenta de nuevo en un momento."),
        }

    empresas = res["empresas"]
    nuevos = actualizados = 0
    for e in empresas:
        rec = nuevo_prospecto(
            e["nombre"], payload.categoria,
            vertical=e.get("vertical_sugerida") or (payload.vertical if payload.vertical != "todas" else None),
            sitio_web=e.get("sitio_web") or None,
            discurso_corporativo=e.get("descripcion") or None,
            fuente_discurso="directorio:wikidata",
            fecha_captura=ahora_iso(),
        )
        r = upsert_prospecto(db, rec)
        if r.get("accion") == "insertado":
            nuevos += 1
        elif r.get("accion") == "actualizado":
            actualizados += 1

    if not empresas:
        nota = ("0 empresas para esa zona/vertical, incluso ampliando el filtro. "
                "Wikidata tiene cobertura limitada de micro-startups; prueba otro país.")
    elif res.get("ampliado"):
        nota = f"filtro ampliado automáticamente ({res.get('nivel','')}) para traer resultados."
    elif res.get("cache"):
        nota = "resultados servidos desde caché (consulta reciente)."
    else:
        nota = ""

    return {
        "region": payload.region, "categoria": payload.categoria,
        "vertical": payload.vertical, "fuente": "Wikidata",
        "encontradas": len(empresas), "nuevos": nuevos, "actualizados": actualizados,
        "ampliado": bool(res.get("ampliado")), "nivel": res.get("nivel", ""),
        "cache": bool(res.get("cache")), "nota": nota,
    }


# --- Capa 0: motor de reglas determinista sobre texto (ingesta) ------------
#
# Punto de entrada para señales de texto/transcripción (los conectores de
# noticias RSS / yt-dlp postean aquí). Evalúa con RuleEngine, puntúa y persiste. No
# interpreta cualitativamente (eso es Motor B): solo registra matches auditables.

_rule_engine = RuleEngine()


def normalizar_texto(texto: str) -> str:
    """Normalización mínima para el match determinista (minúsculas, sin bordes)."""
    return " ".join((texto or "").lower().split())


class IngestaIn(BaseModel):
    texto: str
    url: str = ""
    timestamp: Optional[str] = None
    org_id: Optional[str] = None
    org_name: Optional[str] = None


def _guardar_senales_capa0(db, prospecto: Prospecto) -> int:
    """Persiste las señales de un prospecto (dedup por id determinista). Devuelve nuevas."""
    nuevas = 0
    for s in prospecto.señales:
        cur = db.execute(
            """INSERT INTO senales_capa0
                 (id, url, timestamp_video, fragmento_literal, tipo_senal, score_deuda,
                  motivo_match, org_id, org_nombre, score_total, nivel_alerta, creado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (id) DO NOTHING""",
            (s.id, s.url, s.timestamp_video, s.fragmento_literal, s.tipo_señal,
             s.score_deuda, s.motivo_match, prospecto.id, prospecto.nombre_organizacion,
             prospecto.score_total, prospecto.nivel_alerta, s.creado_en.isoformat()),
        )
        if getattr(cur, "rowcount", 0):
            nuevas += 1
    return nuevas


def _procesar_capa0(db, texto: str, url: str = "", timestamp: Optional[str] = None,
                    org_id: Optional[str] = None, org_name: Optional[str] = None) -> dict:
    """Núcleo Capa 0 (compartido): evalúa reglas, puntúa y persiste. Sin red."""
    limpio = normalizar_texto(texto)
    if not limpio:
        return {"senales_detectadas": 0, "senales_nuevas": 0, "score_total": 0.0,
                "nivel_alerta": "Normal", "senales": []}
    señales: list[SeñalCapa0] = _rule_engine.evaluar(limpio, url, timestamp)
    score, alerta = _rule_engine.calcular_alerta(señales)
    nuevas = 0
    if señales:
        prospecto = Prospecto(
            id=org_id or (url or limpio[:40]),
            nombre_organizacion=org_name or "(organización sin nombre)",
            señales=señales, score_total=score, nivel_alerta=alerta,
        )
        nuevas = _guardar_senales_capa0(db, prospecto)
    return {
        "senales_detectadas": len(señales), "senales_nuevas": nuevas,
        "score_total": score, "nivel_alerta": alerta,
        "senales": [s.model_dump(mode="json") for s in señales],
    }


@app.post("/webhook/ingesta")
def ingesta_capa0(payload: IngestaIn, x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Ingesta de texto para la Capa 0: evalúa reglas, puntúa y persiste señales.

    Autenticado (escribe). Determinista: mismo texto → mismas señales. Si no hay
    match, no persiste nada. Devuelve el resumen y las señales detectadas.
    """
    _exigir_token(x_ingest_token)
    if not normalizar_texto(payload.texto):
        raise HTTPException(400, "texto vacío")
    return {"status": "procesado", **_procesar_capa0(
        get_db(), payload.texto, payload.url, payload.timestamp,
        payload.org_id, payload.org_name)}


class IngestaNoticiasIn(BaseModel):
    query: Optional[str] = None
    feed: Optional[str] = None
    limite: int = 25


@app.post("/ingesta/noticias")
def ingesta_noticias(payload: IngestaNoticiasIn,
                     x_ingest_token: Optional[str] = Header(None)) -> dict:
    """Ingesta de noticias EN LA APP: el servidor lee el RSS (Google News gratis o
    un feed) y procesa cada nota con la Capa 0. Sin depender de scripts externos.
    """
    _exigir_token(x_ingest_token)
    if not (payload.query or payload.feed):
        raise HTTPException(400, "indica 'query' (Google News) o 'feed' (URL de RSS)")
    db = get_db()

    def procesar(p: dict) -> dict:
        # Descarta ruido/gigantes/geo antes de la Capa 0 (calidad de la señal).
        ok, _ = evaluar_relevancia(p.get("texto", ""), [], True, exigir_evento=False)
        if not ok:
            return {"senales_detectadas": 0}
        return _procesar_capa0(db, p.get("texto", ""), p.get("url", ""),
                               None, None, p.get("org_name"))

    try:
        res = _noticias.correr(query=payload.query, feed_url=payload.feed,
                               limite=payload.limite, enviar_fn=procesar)
    except Exception as exc:
        raise HTTPException(502, f"no se pudo leer el feed: {exc}")
    return res


@app.get("/senales-capa0")
def listar_senales_capa0(
    nivel_alerta: Optional[str] = Query(None, description="Filtra por Normal|Crítica"),
    limite: int = Query(50, ge=1, le=500),
) -> dict:
    """Lista las señales de Capa 0 registradas (lectura pública)."""
    db = get_db()
    where, params = "1=1", []
    if nivel_alerta:
        where, params = "nivel_alerta = ?", [nivel_alerta]
    filas = db.fetch_all(
        f"SELECT * FROM senales_capa0 WHERE {where} ORDER BY creado_en DESC LIMIT ?",
        params + [limite],
    )
    return {"total": len(filas), "items": [dict(f) for f in filas]}


# --- Expedientes Vivos: evidencia agrupada por organización ----------------
#
# Cada expediente agrupa TODAS las evidencias de una organización, corre el
# análisis determinista sobre las señales combinadas, detecta patrones
# (COMBINACIONES de señales) y genera la hipótesis de Dolor Cultural.

from ..analisis import COMBINACIONES, DEUDA_POR_SENAL, ANGULO_POR_DEUDA


def _detectar_patrones(keywords: list[str]) -> list[dict]:
    """Detecta patrones (combinaciones de 2+ señales) presentes en los keywords."""
    ks = set(keywords or [])
    patrones = []
    for tags, label, razon in COMBINACIONES:
        if tags <= ks:
            patrones.append({"patron": label, "razonamiento": razon,
                             "senales": sorted(tags)})
    return patrones


def _construir_expedientes(categorias: list[str] | None, limite: int = 30) -> dict:
    """Agrupa evidencia por organización y enriquece con análisis completo."""
    db = get_db()
    cats = list(categorias or [])
    if cats:
        marc = ",".join("?" for _ in cats)
        clausula = f"estado = ? AND categoria IN ({marc})"
        params: list = [ESTADO_OK, *cats]
    else:
        clausula, params = "estado = ?", [ESTADO_OK]

    filas = db.fetch_all(
        f"SELECT * FROM evidencias WHERE {clausula} ORDER BY creado_en DESC LIMIT 500",
        tuple(params),
    )

    orgs: dict[str, dict] = {}
    for row in filas:
        titulo = row["cita_textual"] or ""
        org = detectar_empresa(titulo) or (row["empresa_mencionada"] or "").strip()
        if not org:
            continue
        kws = _keywords(row)
        ok, _ = evaluar_relevancia(titulo, kws, bool(org), exigir_evento=False)
        if not ok:
            continue
        key = org.lower()
        if key not in orgs:
            orgs[key] = {"nombre": org, "evidencias_raw": [],
                         "keywords_set": set(),
                         "categoria": row["categoria"] or "",
                         "mejor_confianza": 0.0, "mejor_calidad": "Baja"}
        orgs[key]["evidencias_raw"].append(row)
        orgs[key]["keywords_set"].update(kws)
        c = row["confianza"] or 0.0
        if c > orgs[key]["mejor_confianza"]:
            orgs[key]["mejor_confianza"] = c
            orgs[key]["mejor_calidad"] = row["calidad_captura"] or "Baja"

    sitios: dict[str, str] = {}
    for p in db.fetch_all(
        "SELECT nombre, sitio_web FROM prospectos "
        "WHERE sitio_web IS NOT NULL AND sitio_web <> ''"
    ):
        sitios[(p["nombre"] or "").strip().lower()] = p["sitio_web"]

    expedientes = []
    for key, data in orgs.items():
        all_kws = list(data["keywords_set"])
        vertical = ""
        for row in data["evidencias_raw"]:
            v = sugerir_vertical(row["cita_textual"] or "")
            if v:
                vertical = v
                break

        a = analizar(
            all_kws, vertical=vertical,
            confianza=data["mejor_confianza"],
            calidad=data["mejor_calidad"],
            categoria=data["categoria"],
        )

        evidencias = []
        for row in data["evidencias_raw"]:
            evidencias.append({
                "texto": row["cita_textual"],
                "fuente": row["nombre_medio"],
                "fecha": (row["fecha_publicacion"] or "")[:10],
                "url": row["url_fuente"],
                "tipo_evento": row["tipo_evento"],
                "confianza": row["confianza"],
            })

        patrones = _detectar_patrones(all_kws)

        sitio = sitios.get(key, "")
        contacto = rutas_contacto(sitio, "") if sitio else {
            "dominio": "", "email_sugerido": "", "verificado": False}

        expedientes.append({
            "nombre": data["nombre"],
            "categoria": data["categoria"],
            "vertical": vertical,
            "scoring": a["scoring"],
            "score_icp": a["score_icp"],
            "intensidad": a["intensidad"],
            "tipo_deuda": a["tipo_deuda"],
            "deuda_razon": a["deuda_razon"],
            "deuda_secundaria": a.get("deuda_secundaria", ""),
            "angulo_conversacion": a["angulo_conversacion"],
            "decisor_sugerido": a["decisor_sugerido"],
            "senal_dominante": a.get("senal_dominante", ""),
            "evidencias": evidencias,
            "total_evidencias": len(evidencias),
            "patrones": patrones,
            "keywords": all_kws,
            "contacto": contacto,
            "linkedin": linkedin_search_url(data["nombre"]),
            "google": google_search_url(data["nombre"]),
        })

    expedientes.sort(
        key=lambda x: (_ORDEN_SCORING.get(x["scoring"], 9), -x["score_icp"]))

    resumen = {"A": 0, "B": 0, "C": 0}
    for e in expedientes:
        resumen[e["scoring"]] = resumen.get(e["scoring"], 0) + 1

    return {
        "total": len(expedientes[:limite]),
        "resumen_scoring": resumen,
        "expedientes": expedientes[:limite],
    }


@app.get("/expedientes")
def listar_expedientes(
    categoria: str | None = Query(None),
    categorias: str | None = Query(None),
    limite: int = Query(30, ge=1, le=100),
) -> dict:
    """Expedientes Vivos: evidencia agrupada por organización + análisis completo.

    Cada expediente incluye todas las evidencias de esa organización, patrones
    detectados, hipótesis de Dolor Cultural, scoring, ICP y decisor sugerido.
    """
    return _construir_expedientes(_cats_validas(categoria, categorias), limite)


@app.get("/admin", response_class=HTMLResponse)
def admin_form() -> str:
    """Pantalla de descubrimiento (scraping) y alta de prospectos (PWA instalable)."""
    return _ADMIN_HTML


_ADMIN_HTML = """<!doctype html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>hd-prospector · Radar</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#2563eb">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Radar">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="icon" type="image/png" href="/icon-192.png">
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0;
         padding: 1.2rem; max-width: 680px; margin-inline: auto; line-height: 1.4; }
  h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
  h2 { font-size: 1.05rem; margin: 1.6rem 0 .4rem; }
  p.sub { margin: 0 0 1rem; opacity: .7; font-size: .9rem; }
  section { border: 1px solid rgba(128,128,128,.25); border-radius: .7rem;
    padding: .9rem 1rem 1.1rem; margin-top: 1.1rem; }
  label { display: block; font-weight: 600; margin: .7rem 0 .25rem; font-size: .9rem; }
  input, select, textarea { width: 100%; padding: .6rem .7rem; border-radius: .5rem;
    border: 1px solid rgba(128,128,128,.4); background: transparent; color: inherit;
    font-size: 1rem; font-family: inherit; }
  textarea { min-height: 100px; resize: vertical; }
  .req::after { content: " *"; color: #e11; }
  button { margin-top: 1rem; width: 100%; padding: .8rem; border: 0; border-radius: .5rem;
    background: #2563eb; color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; }
  button.sec { background: transparent; color: inherit; border: 1px solid rgba(128,128,128,.5);
    margin-top: .5rem; padding: .5rem; font-weight: 500; font-size: .85rem; }
  button:disabled { opacity: .5; }
  .msg { margin-top: .8rem; padding: .7rem; border-radius: .5rem; display: none; font-size: .9rem; }
  .msg.ok { background: rgba(22,163,74,.15); border: 1px solid rgba(22,163,74,.5); display: block; }
  .msg.err { background: rgba(220,38,38,.15); border: 1px solid rgba(220,38,38,.5); display: block; }
  .counts { display: flex; gap: .5rem; flex-wrap: wrap; margin: .6rem 0 0; }
  .chip { padding: .3rem .6rem; border-radius: 1rem; border: 1px solid rgba(128,128,128,.4); font-size: .85rem; }
  .hint { font-size: .8rem; opacity: .65; margin-top: .2rem; }
  .card { border: 1px solid rgba(128,128,128,.25); border-radius: .5rem; padding: .6rem .7rem; margin-top: .6rem; }
  .card .meta { font-size: .78rem; opacity: .7; margin-top: .25rem; }
  .card a { color: #3b82f6; text-decoration: none; }
  .row { display: flex; gap: .5rem; }
  .row > * { flex: 1; }
  .cats { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; margin-top: .6rem; }
  .cat { margin-top: 0; background: transparent; color: inherit; border: 1px solid #2563eb; font-weight: 600; }
  .dir { margin-top: 0; background: transparent; color: inherit; border: 1px solid #16a34a; font-weight: 600; }
  .org { font-size: 1.05rem; margin-bottom: .25rem; }
  .chips-sel { display: flex; flex-wrap: wrap; gap: .8rem; margin: .6rem 0; }
  .chips-sel label { display: inline-flex; align-items: center; gap: .3rem; font-size: .95rem; }
  .dl { display: block; text-align: center; text-decoration: none; color: inherit;
    padding: .7rem; border: 1px solid rgba(128,128,128,.5); border-radius: .5rem; font-weight: 600; }
  /* Expediente Vivo styles */
  .exp { border: 1px solid rgba(128,128,128,.25); border-radius: .7rem; padding: .8rem; margin-top: .8rem; }
  .exp-a { border-left: 4px solid #dc2626; }
  .exp-b { border-left: 4px solid #f59e0b; }
  .exp-c { border-left: 4px solid #6b7280; }
  .exp-header { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; margin-bottom: .4rem; }
  .exp-header .org-name { font-size: 1.1rem; font-weight: 700; }
  .badge { display: inline-block; padding: .15rem .45rem; border-radius: .3rem; font-size: .75rem; font-weight: 700; }
  .badge-a { background: #dc2626; color: #fff; }
  .badge-b { background: #f59e0b; color: #000; }
  .badge-c { background: #6b7280; color: #fff; }
  .badge-icp { background: rgba(37,99,235,.15); color: #2563eb; border: 1px solid rgba(37,99,235,.3); }
  .badge-int { background: rgba(128,128,128,.12); }
  .badge-deuda { background: rgba(168,85,247,.12); color: #7c3aed; border: 1px solid rgba(168,85,247,.3); }
  .deuda-box { background: rgba(168,85,247,.06); border: 1px solid rgba(168,85,247,.2); border-radius: .5rem; padding: .5rem .6rem; margin: .4rem 0; }
  .deuda-title { font-weight: 700; color: #7c3aed; font-size: .9rem; }
  .deuda-reason { font-size: .82rem; opacity: .8; margin-top: .15rem; }
  .angulo { font-size: .82rem; margin-top: .3rem; padding: .3rem .5rem; background: rgba(37,99,235,.06); border-radius: .4rem; border: 1px solid rgba(37,99,235,.15); }
  .patron-box { background: rgba(245,158,11,.08); border: 1px solid rgba(245,158,11,.2); border-radius: .4rem; padding: .35rem .5rem; margin-top: .3rem; font-size: .82rem; }
  .ev-toggle { cursor: pointer; font-size: .82rem; color: #3b82f6; margin-top: .4rem; display: inline-block; }
  .ev-list { display: none; margin-top: .3rem; }
  .ev-list.open { display: block; }
  .ev-item { font-size: .8rem; padding: .3rem .4rem; border-left: 2px solid rgba(128,128,128,.3); margin: .3rem 0 .3rem .3rem; }
  .ev-item a { font-size: .78rem; }
  .exp-actions { display: flex; gap: .4rem; flex-wrap: wrap; margin-top: .5rem; }
  .exp-actions button, .exp-actions a { font-size: .8rem; padding: .35rem .6rem; margin-top: 0; width: auto; }
  .resumen-bar { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; margin: .5rem 0; }
  .resumen-bar .chip { font-weight: 700; }
</style></head><body>
<h1>hd-prospector · Radar de Inteligencia Antropológica</h1>
<p class="sub">① Rastrea señales por ecosistema → ② <b>Expedientes Vivos</b>: evidencia agrupada por organización con <b>patrones</b>, <b>Dolor Cultural™</b> (hipótesis determinista) y <b>scoring A/B/C</b> → ③ auto-investiga y guarda el prospecto. Internet → Evidencia → Patrón → Dolor → Prospecto.</p>

<label class="req">Token de acceso</label>
<input id="token" type="password" placeholder="HD_INGEST_TOKEN" autocomplete="off">
<div class="hint">Se guarda solo en este dispositivo. Es el valor que pusiste en Vercel.</div>
<label>Región (zona geográfica)</label>
<select id="region">
  <option value="LATAM">Toda LATAM (8 países)</option>
  <option value="México">México</option>
  <option value="Colombia">Colombia</option>
  <option value="Chile">Chile</option>
  <option value="Perú">Perú</option>
  <option value="Argentina">Argentina</option>
  <option value="Brasil">Brasil</option>
  <option value="Costa Rica">Costa Rica</option>
  <option value="Panamá">Panamá</option>
</select>
<div class="counts" id="counts"></div>

<section>
  <h2>① Buscar por ecosistema</h2>
  <div class="hint">Elige el tipo de señal y toca un ecosistema. Rastrea ese sector con ese tipo de evento y etiqueta las señales. Para descubrir sin teclear nombres.</div>
  <div class="row">
    <div>
      <label>Tipo de señal</label>
      <select id="c_tipo">
        <option value="queja">fricción / churn</option>
        <option value="ronda">ronda</option>
        <option value="contratacion">contratación</option>
        <option value="despido">despido / estancamiento</option>
        <option value="lanzamiento">lanzamiento</option>
        <option value="cambio_sitio">pivote / rediseño</option>
      </select>
    </div>
    <div>
      <label>Vertical (HD)</label>
      <select id="c_vertical">
        <option value="todas">Todas</option>
        <option value="fintech">Fintech</option>
        <option value="edtech">Edtech</option>
        <option value="healthtech">Healthtech</option>
        <option value="salud mental">Salud mental</option>
        <option value="logística agrícola">Logística agrícola</option>
        <option value="identidad">Identidad</option>
      </select>
    </div>
  </div>
  <div class="cats">
    <button class="cat" data-cat="VC">VC</button>
    <button class="cat" data-cat="Startup">Startup</button>
    <button class="cat" data-cat="Incubadora">Incubadora</button>
    <button class="cat" data-cat="Corporativo">Corporativo</button>
  </div>
  <div class="msg" id="s_msg"></div>

  <div class="hint" style="margin-top:1rem">¿Pocas noticias? Trae <b>empresas reales</b> de una base pública (Wikidata) para tener volumen sin depender de la prensa. Elige un <b>país</b> (arriba), la vertical y el ecosistema al que se asignan.</div>
  <div class="cats">
    <button class="dir" data-cat="VC">🏢 Empresas → VC</button>
    <button class="dir" data-cat="Startup">🏢 Empresas → Startup</button>
    <button class="dir" data-cat="Incubadora">🏢 Empresas → Incubadora</button>
    <button class="dir" data-cat="Corporativo">🏢 Empresas → Corporativo</button>
  </div>
  <div class="msg" id="d_msg"></div>

  <h2 style="margin-top:1.4rem">…o buscar por nombre</h2>
  <label>Empresa</label>
  <input id="s_empresa" placeholder="p. ej. Nubank">
  <div class="row">
    <div>
      <label>Tipo de señal</label>
      <select id="s_tipo">
        <option value="ronda">ronda</option>
        <option value="contratacion">contratación</option>
        <option value="despido">despido</option>
        <option value="lanzamiento">lanzamiento</option>
        <option value="queja">queja</option>
        <option value="cambio_sitio">cambio_sitio</option>
      </select>
    </div>
    <div>
      <label>Fuentes</label>
      <select id="s_fuentes">
        <option value="google_news,gdelt">Google News + GDELT</option>
        <option value="google_news">Google News</option>
        <option value="gdelt">GDELT</option>
      </select>
    </div>
  </div>
  <button id="s_btn">🔎 Buscar por nombre</button>
</section>

<section>
  <h2>② Expedientes Vivos</h2>
  <div class=”hint”>Cada tarjeta es un <b>expediente de organización</b> construido a partir de <b>múltiples evidencias</b>. Las señales se combinan para detectar <b>patrones</b> y generar una <b>hipótesis de Dolor Cultural™</b> (tipo + intensidad + razonamiento). Scoring A = atacar · B = observar · C = archivo.</div>
  <div class=”resumen-bar” id=”exp_resumen”></div>
  <div id=”expedientes”></div>
</section>

<section>
  <h2>②·⁵ Informe profundo (análisis)</h2>
  <div class="hint">Lee lo capturado y lo <b>prioriza</b>: para cada empresa calcula scoring A/B/C, hipótesis de <b>Deuda Cultural™</b>, Score ICP y a qué <b>decisor</b> contactar. Determinista (sin IA): mismos hechos, mismo resultado.</div>
  <div class="hint" style="margin-top:.4rem">Elige <b>categorías</b> (todas o las que quieras) y luego <b>genera</b>, <b>guarda</b> o <b>exporta</b> la investigación.</div>
  <div class="chips-sel" id="inf_cats">
    <label><input type="checkbox" id="inf_todas" checked> Todas</label>
    <label><input type="checkbox" class="inf-cat" value="VC"> VC</label>
    <label><input type="checkbox" class="inf-cat" value="Startup"> Startup</label>
    <label><input type="checkbox" class="inf-cat" value="Incubadora"> Incubadora</label>
    <label><input type="checkbox" class="inf-cat" value="Corporativo"> Corporativo</label>
  </div>
  <div class="row">
    <button id="inf_btn" class="sec">📊 Generar</button>
    <button id="inf_guardar" class="sec">💾 Guardar</button>
    <a id="inf_md" class="sec" href="#" style="display:none">⬇︎ Markdown</a>
    <a id="inf_csv" class="sec" href="#" style="display:none">⬇︎ CSV</a>
  </div>
  <div id="inf_resumen" class="hint"></div>
  <div id="informe"></div>
  <div class="hint" style="margin-top:.8rem"><b>Investigaciones guardadas</b> <button id="inf_ver_guardadas" class="sec">🔄 Actualizar</button></div>
  <div id="inf_guardadas"></div>
</section>

<section>
  <h2>②·⁷ Capa 0 — Señales de Deuda (en la app)</h2>
  <div class="hint">Corre <b>dentro de la app</b>: el servidor lee noticias (RSS gratis) o analiza el texto que pegues, con el motor de reglas determinista (Operativa/Discursiva/Rescate). No necesitas terminal.</div>
  <div class="row">
    <input id="cap_query" placeholder="Ej. fintech México ronda">
    <button id="cap_noticias" class="sec">📡 Ingerir noticias</button>
  </div>
  <label style="margin-top:.6rem">…o pega un texto / transcripción</label>
  <textarea id="cap_texto" placeholder="Pega aquí una transcripción de video, un post o cualquier texto…"></textarea>
  <div class="row">
    <input id="cap_org" placeholder="Organización (opcional)">
    <button id="cap_analizar" class="sec">🔬 Analizar texto</button>
    <button id="cap_ver" class="sec">👁️ Ver señales</button>
  </div>
  <div id="cap_msg" class="msg"></div>
  <div id="cap_lista"></div>
</section>

<section>
  <h2>③ Expediente del prospecto (auto-investigado)</h2>
  <div class="hint">Toca <b>Enriquecer</b> (o promueve un candidato) y web, tesis y vertical se llenan solos. Tú revisas y ajustas antes de guardar.</div>
  <label class="req">Nombre</label>
  <input id="nombre" placeholder="p. ej. Kaszek">
  <label class="req">Categoría (ecosistema)</label>
  <select id="categoria">
    <option value="VC">VC — fondo / venture capital</option>
    <option value="Startup">Startup</option>
    <option value="Incubadora">Incubadora / Aceleradora</option>
    <option value="Corporativo">Corporativo</option>
  </select>

  <button id="enrich_btn" class="sec">🔎 Enriquecer (buscar web + tesis)</button>
  <div class="msg" id="e_msg"></div>
  <div id="e_links" style="margin:.4rem 0; font-size:.85rem"></div>

  <label>Vertical / sector</label>
  <input id="vertical" placeholder="fintech, salud, logística…">
  <label>Sitio web</label>
  <input id="sitio_web" placeholder="https://…">
  <label>LinkedIn</label>
  <input id="linkedin" placeholder="https://www.linkedin.com/…">

  <label>Discurso corporativo (Thick Data)</label>
  <textarea id="discurso" placeholder="Tesis de inversión, promesa de valor, programa, comunicado…"></textarea>
  <label>Tipo de discurso</label>
  <input id="tipo_discurso" placeholder="tesis_inversion | promesa_valor | programa…">
  <label>URL / perfil de origen</label>
  <input id="url_perfil" placeholder="https://…">
  <label>Fuente del discurso</label>
  <input id="fuente_discurso" placeholder="sitio_oficial, linkedin, prensa…">
  <button id="enviar">Guardar prospecto</button>
  <div class="msg" id="msg"></div>

  <h2 style="margin-top:1.3rem">Exportar</h2>
  <div class="hint">Descarga tus prospectos guardados (todos los ecosistemas).</div>
  <div class="row" style="margin-top:.6rem">
    <a class="dl" href="/prospectos/export.csv">⬇️ CSV</a>
    <a class="dl" href="/prospectos/export.md">⬇️ Markdown</a>
    <a class="dl" href="/prospectos/export.json">⬇️ JSON</a>
  </div>
</section>

<script>
  const $ = id => document.getElementById(id);
  const esc = s => (s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const safeUrl = u => /^https?:\\/\\//i.test(u||"") ? u : "#";   // solo http(s) en enlaces
  $("token").value = localStorage.getItem("hd_ingest_token") || "";
  const tok = () => { const t = $("token").value.trim(); localStorage.setItem("hd_ingest_token", t); return t; };

  // Lee la respuesta con tolerancia: si el servidor devolvió TEXTO (p. ej. un
  // "Internal Server Error" por timeout del serverless), no revienta con
  // "SyntaxError: is not valid JSON"; devuelve un mensaje entendible.
  async function leerJson(r) {
    const cuerpo = await r.text();
    try { return { ok: r.ok, status: r.status, data: JSON.parse(cuerpo) }; }
    catch (e) {
      const esTimeout = /timeout|timed out|internal server error|gateway|504|FUNCTION_INVOCATION/i.test(cuerpo);
      const msg = esTimeout
        ? "la búsqueda tardó demasiado y el servidor la cortó. Intenta de nuevo, elige un solo país (zona) o una vertical más específica."
        : ("respuesta inesperada del servidor (" + r.status + ").");
      return { ok: false, status: r.status, data: null, error: msg };
    }
  }

  async function refrescarConteo() {
    try {
      const d = await (await fetch("/prospectos/categorias")).json();
      $("counts").innerHTML = Object.entries(d.categorias)
        .map(([k, v]) => `<span class="chip">${k}: <b>${v}</b></span>`).join("");
    } catch (e) {}
  }
  refrescarConteo();

  // ① Descubrimiento por ecosistema
  async function scrapear(body, etiqueta, cargar) {
    const m = $("s_msg"), token = tok();
    if (!token) { m.className = "msg err"; m.textContent = "Falta el token."; return; }
    document.querySelectorAll("button").forEach(b => b.disabled = true);
    m.className = "msg"; m.style.display = "block"; m.textContent = "Rastreando " + etiqueta + "… (unos segundos)";
    try {
      const r = await fetch("/scrape", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify(body) });
      const res = await leerJson(r);
      if (!res.ok) { m.className = "msg err"; m.textContent = "Error: " + (res.error || (res.data && res.data.detail) || r.status); return; }
      const d = res.data;
      const vistas = (d.resultados||[]).reduce((a,x)=>a+(x.vistos||0),0);
      const dup = (d.resultados||[]).reduce((a,x)=>a+(x.duplicados||0),0);
      const fil = (d.resultados||[]).reduce((a,x)=>a+(x.filtrados||0),0);
      const err = (d.resultados||[]).reduce((a,x)=>a+(x.errores||0)+(x.error?1:0),0);
      const nota = d.parcial ? " (parcial: se agotó el tiempo; toca de nuevo para seguir)" : "";
      m.className = "msg ok";
      m.textContent = `✓ ${d.total_escritos} nuevas · ${vistas} titulares de Google News · ${dup} repetidas · ${fil} descartadas por filtro · ${err} errores — ${etiqueta}${nota}.`;
      if (vistas === 0) {
        m.className = "msg err";
        m.textContent = `⚠️ Google News no devolvió titulares para esta búsqueda${nota}. Puede ser bloqueo temporal desde el servidor o que no haya noticias de «${etiqueta}» ahora. Prueba otra señal, otra vertical, o un solo país.`;
      }
      cargar();
    } catch (e) { m.className = "msg err"; m.textContent = "Error de red: " + e; }
    finally { document.querySelectorAll("button").forEach(b => b.disabled = false); }
  }

  const region = () => $("region").value;

  document.querySelectorAll(".cat").forEach(btn => btn.addEventListener("click", () => {
    const cat = btn.dataset.cat, tipo = $("c_tipo").value, vert = $("c_vertical").value;
    if ($("categoria")) $("categoria").value = cat;   // precarga categoría en ③
    scrapear({ categoria: cat, tipo_evento: tipo, vertical: vert, region: region() },
             `${cat} · ${tipo} · ${vert} · ${region()}`, () => cargarExpedientes({ categoria: cat }));
  }));

  // Directorio: trae empresas reales (Wikidata) como prospectos (volumen).
  document.querySelectorAll(".dir").forEach(btn => btn.addEventListener("click", async () => {
    const cat = btn.dataset.cat, vert = $("c_vertical").value, m = $("d_msg"), token = tok();
    if (!token) { m.className = "msg err"; m.style.display = "block"; m.textContent = "Falta el token."; return; }
    document.querySelectorAll("button").forEach(b => b.disabled = true);
    m.className = "msg"; m.style.display = "block"; m.textContent = `Trayendo empresas reales de ${region()} (${cat})…`;
    try {
      const r = await fetch("/directorio", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify({ region: region(), categoria: cat, vertical: vert, limite: 40 }) });
      const out = await leerJson(r);
      if (!out.ok) { m.className = "msg err"; m.textContent = "Error: " + (out.error || (out.data && out.data.detail) || r.status); return; }
      const d = out.data;
      if (d.encontradas === 0) { m.className = "msg err"; m.textContent = "⚠️ " + (d.nota || "Sin empresas para esa zona/vertical."); }
      else { m.className = "msg ok"; m.textContent = `✓ ${d.nuevos} nuevas · ${d.actualizados} actualizadas · ${d.encontradas} de ${d.fuente} — ${cat} · ${region()}.` + (d.nota ? " " + d.nota : "") + " Míralas en el Informe profundo."; }
      refrescarConteo();
    } catch (e) { m.className = "msg err"; m.textContent = "Error de red: " + e; }
    finally { document.querySelectorAll("button").forEach(b => b.disabled = false); }
  }));

  $("s_btn").addEventListener("click", () => {
    const empresa = $("s_empresa").value.trim();
    if (!empresa) { const m = $("s_msg"); m.className = "msg err"; m.style.display = "block"; m.textContent = "Escribe una empresa."; return; }
    $("nombre").value = empresa;                       // precarga para ③
    scrapear({ empresa, tipo_evento: $("s_tipo").value, region: region(), connectors: $("s_fuentes").value.split(",") },
             empresa, () => cargarExpedientes({ empresa }));
  });

  // ② Cargar Expedientes Vivos (agrupados por organización, con análisis)
  let _expId = 0;
  async function cargarExpedientes(filtro) {
    const cont = $("expedientes"), res = $("exp_resumen");
    cont.innerHTML = '<div class="hint">Cargando expedientes…</div>';
    const qs = new URLSearchParams({ limite: "30", ...filtro });
    try {
      const d = await (await fetch("/expedientes?" + qs)).json();
      if (!d.expedientes || !d.expedientes.length) {
        cont.innerHTML = '<div class="hint">Sin expedientes todavía. Rastrea un ecosistema arriba.</div>';
        res.innerHTML = "";
        return;
      }
      const s = d.resumen_scoring || {};
      res.innerHTML = `<span class="chip">${d.total} org.</span>` +
        (s.A ? `<span class="chip badge badge-a">A: ${s.A}</span>` : "") +
        (s.B ? `<span class="chip badge badge-b">B: ${s.B}</span>` : "") +
        (s.C ? `<span class="chip badge badge-c">C: ${s.C}</span>` : "");
      cont.innerHTML = d.expedientes.map(e => {
        const sc = (e.scoring||"C").toUpperCase();
        const bcls = sc === "A" ? "badge-a" : sc === "B" ? "badge-b" : "badge-c";
        const ecls = sc === "A" ? "exp-a" : sc === "B" ? "exp-b" : "exp-c";
        const uid = "ev_" + (++_expId);

        let deudaHtml = "";
        if (e.tipo_deuda) {
          deudaHtml = `<div class="deuda-box">
            <div class="deuda-title">🧩 ${esc(e.tipo_deuda)}${e.deuda_secundaria ? ' <span style="font-weight:400;opacity:.7">+ ' + esc(e.deuda_secundaria) + '</span>' : ''}</div>
            <div class="deuda-reason">${esc(e.deuda_razon)}</div>
          </div>`;
        }

        let anguloHtml = "";
        if (e.angulo_conversacion) {
          anguloHtml = `<div class="angulo">💬 <b>Ángulo:</b> ${esc(e.angulo_conversacion)}</div>`;
        }

        let patronesHtml = "";
        if (e.patrones && e.patrones.length) {
          patronesHtml = e.patrones.map(p =>
            `<div class="patron-box">🔗 <b>${esc(p.patron)}</b> — ${esc(p.razonamiento)}</div>`
          ).join("");
        }

        const evItems = (e.evidencias || []).map(ev =>
          `<div class="ev-item">
            ${esc(ev.texto)}
            <div><b>${esc(ev.fuente)}</b>${ev.fecha ? " · " + esc(ev.fecha) : ""}${ev.tipo_evento ? " · " + esc(ev.tipo_evento) : ""}
            ${ev.url ? ' · <a href="' + esc(safeUrl(ev.url)) + '" target="_blank" rel="noopener">fuente ↗</a>' : ""}</div>
          </div>`
        ).join("");

        return `
        <div class="exp ${ecls}">
          <div class="exp-header">
            <span class="org-name">🏢 ${esc(e.nombre)}</span>
            <span class="badge ${bcls}">${sc}</span>
            <span class="badge badge-icp">ICP ${e.score_icp}</span>
            <span class="badge badge-int">${esc(e.intensidad||"")}</span>
            ${e.categoria ? '<span class="chip">' + esc(e.categoria) + '</span>' : ""}
            ${e.vertical ? '<span class="chip">' + esc(e.vertical) + '</span>' : ""}
          </div>
          ${deudaHtml}
          ${patronesHtml}
          ${anguloHtml}
          <div class="meta" style="margin-top:.3rem">🎯 Decisor: <b>${esc(e.decisor_sugerido)}</b>${e.contacto && e.contacto.email_sugerido ? " · ✉️ " + esc(e.contacto.email_sugerido) : ""}</div>
          <span class="ev-toggle" onclick="document.getElementById('${uid}').classList.toggle('open');this.textContent=this.textContent.includes('▶')? '▼ Ocultar evidencias (${e.total_evidencias})':'▶ Ver evidencias (${e.total_evidencias})'">▶ Ver evidencias (${e.total_evidencias})</span>
          <div id="${uid}" class="ev-list">${evItems}</div>
          <div class="exp-actions">
            <button class="sec" onclick="prefill(this.dataset.n)" data-n="${esc(e.nombre)}">➕ Guardar y auto-investigar</button>
            ${e.linkedin ? '<a class="sec" href="' + esc(safeUrl(e.linkedin)) + '" target="_blank" rel="noopener">LinkedIn ↗</a>' : ""}
            ${e.contacto && e.contacto.dominio ? '<button class="sec" onclick="verificarCorreo(this)" data-dom="' + esc(e.contacto.dominio) + '">✓ Verificar correo</button> <span class="vres"></span>' : ""}
          </div>
        </div>`; }).join("");
    } catch (e) { cont.innerHTML = '<div class="hint">Error al cargar expedientes: ' + esc(String(e)) + '</div>'; }
  }

  window.prefill = (nombre) => {
    $("nombre").value = nombre;
    $("nombre").scrollIntoView({ behavior: "smooth" });
    $("enrich_btn").click();
  };

  // ②·⁵ Informe profundo: análisis determinista de lo capturado.
  const SCLASE = { A: "msg ok", B: "msg", C: "msg" };
  // "Todas" y las casillas por categoría se excluyen entre sí.
  $("inf_todas").addEventListener("change", () => {
    if ($("inf_todas").checked) document.querySelectorAll(".inf-cat").forEach(c => c.checked = false);
  });
  document.querySelectorAll(".inf-cat").forEach(c => c.addEventListener("change", () => {
    if (document.querySelector(".inf-cat:checked")) $("inf_todas").checked = false;
    else $("inf_todas").checked = true;
  }));
  function catsSeleccionadas() {
    return [...document.querySelectorAll(".inf-cat:checked")].map(c => c.value);
  }
  function qsCats() {
    const cats = catsSeleccionadas();
    return cats.length ? "?categorias=" + encodeURIComponent(cats.join(",")) : "";
  }
  $("inf_btn").addEventListener("click", async () => {
    const cont = $("informe"), res = $("inf_resumen");
    res.textContent = "Analizando lo capturado…"; cont.innerHTML = "";
    const q = qsCats();
    $("inf_md").href = "/informe.md" + q; $("inf_md").style.display = "inline-block";
    $("inf_csv").href = "/informe.csv" + q; $("inf_csv").style.display = "inline-block";
    try {
      const r = await fetch("/informe" + q);
      const out = await leerJson(r);
      if (!out.ok) { res.className = "msg err"; res.textContent = "Error: " + (out.error || r.status); return; }
      const d = out.data, s = d.resumen_scoring || {};
      res.className = "hint";
      res.textContent = `${d.total} empresa(s) — A: ${s.A||0} · B: ${s.B||0} · C: ${s.C||0} (A = atacar primero).`;
      if (!d.prospectos.length) { cont.innerHTML = '<div class="hint">Aún no hay nada capturado para analizar. Haz una búsqueda por ecosistema arriba.</div>'; return; }
      cont.innerHTML = d.prospectos.map(t => {
        const c = t.contacto || {};
        const email = c.email_sugerido
          ? ` · ✉️ <b>${esc(c.email_sugerido)}</b> <span class="chip">sin verificar</span>`
          : "";
        return `
        <div class="card">
          <div><b>${esc(t.empresa || "(sin nombre)")}</b> <span class="chip">${esc(t.scoring)}</span> <span class="chip">ICP ${t.score_icp}</span> <span class="chip">intensidad ${esc(t.intensidad||"")}</span>${t.tipo_deuda ? ' <span class="chip">' + esc(t.tipo_deuda) + '</span>' : ''}</div>
          <div>${esc(t.titulo)}</div>
          <div class="meta">${t.tipo_deuda ? "🧩 " + esc(t.tipo_deuda) + " — " + esc(t.deuda_razon) + (t.deuda_secundaria ? " · secundaria: " + esc(t.deuda_secundaria) : "") + "<br>" : ""}
            ${t.angulo_conversacion ? "💬 Ángulo: " + esc(t.angulo_conversacion) + "<br>" : ""}
            🎯 Decisor sugerido: <b>${esc(t.decisor_sugerido)}</b>${email}<br>📌 ${esc(t.razon)}</div>
          <div class="meta">${esc(t.nombre_medio||"")}${t.vertical ? " · " + esc(t.vertical) : ""}${t.categoria ? " · " + esc(t.categoria) : ""}${t.fecha_publicacion ? " · " + esc(t.fecha_publicacion) : ""}
            ${t.url_fuente ? ' · <a href="' + esc(safeUrl(t.url_fuente)) + '" target="_blank" rel="noopener">fuente ↗</a>' : ""}
            ${t.linkedin ? ' · <a href="' + esc(safeUrl(t.linkedin)) + '" target="_blank" rel="noopener">LinkedIn ↗</a>' : ""}</div>
          <button class="sec" onclick="prefill(this.dataset.n)" data-n="${esc(t.empresa)}">➕ Guardar y auto-investigar</button>
          ${c.dominio ? '<button class="sec" onclick="verificarCorreo(this)" data-dom="' + esc(c.dominio) + '">✓ Verificar correo (Hunter)</button> <span class="vres"></span>' : ""}
        </div>`; }).join("");
    } catch (e) { res.className = "msg err"; res.textContent = "Error de red: " + e; }
  });

  // Guardar la investigación (snapshot) de las categorías elegidas.
  $("inf_guardar").addEventListener("click", async () => {
    const res = $("inf_resumen"), token = tok();
    if (!token) { res.className = "msg err"; res.style.display = "block"; res.textContent = "Falta el token para guardar."; return; }
    const cats = catsSeleccionadas();
    res.className = "hint"; res.textContent = "Guardando investigación…";
    try {
      const r = await fetch("/informe/guardar", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify({ categorias: cats.join(",") }) });
      const o = await leerJson(r);
      if (!o.ok) { res.className = "msg err"; res.textContent = "Error: " + (o.error || (o.data && o.data.detail) || r.status); return; }
      res.className = "msg ok"; res.textContent = `✓ Guardada: ${o.data.titulo}.`;
      verGuardadas();
    } catch (e) { res.className = "msg err"; res.textContent = "Error de red: " + e; }
  });

  async function verGuardadas() {
    const cont = $("inf_guardadas");
    try {
      const d = await (await fetch("/informes")).json();
      if (!d.items.length) { cont.innerHTML = '<div class="hint">Aún no hay investigaciones guardadas.</div>'; return; }
      cont.innerHTML = d.items.map(g => {
        const s = g.resumen_scoring || {};
        return `<div class="card">
          <div><b>${esc(g.titulo || "Investigación")}</b></div>
          <div class="meta">${esc(g.categorias || "Todas")} · ${g.total} empresas · A:${s.A||0} B:${s.B||0} C:${s.C||0} · ${(g.creado_en||"").slice(0,16).replace("T"," ")}</div>
          <a class="sec" href="/informes/${g.id}.md" target="_blank" rel="noopener">⬇︎ Descargar</a>
          <button class="sec" onclick="borrarInvestigacion(${g.id})">🗑 Borrar</button>
        </div>`; }).join("");
    } catch (e) { cont.innerHTML = '<div class="hint">No se pudieron cargar.</div>'; }
  }
  $("inf_ver_guardadas").addEventListener("click", verGuardadas);
  window.borrarInvestigacion = async (id) => {
    const res = $("inf_resumen"), token = tok();
    if (!token) { res.className = "msg err"; res.style.display = "block"; res.textContent = "Falta el token para borrar."; return; }
    if (!confirm("¿Borrar esta investigación guardada?")) return;
    try {
      const r = await fetch("/informes/" + id, { method: "DELETE",
        headers: { "X-Ingest-Token": token } });
      const o = await leerJson(r);
      if (!o.ok) { res.className = "msg err"; res.style.display = "block"; res.textContent = "Error al borrar: " + (o.error || (o.data && o.data.detail) || r.status); return; }
      res.className = "msg ok"; res.style.display = "block"; res.textContent = "🗑 Investigación borrada.";
      verGuardadas();
    } catch (e) { res.className = "msg err"; res.style.display = "block"; res.textContent = "Error de red: " + e; }
  };

  // Verificar correo del decisor con Hunter (bajo demanda, consume cuota).
  window.verificarCorreo = async (btn) => {
    const dom = btn.dataset.dom, out = btn.nextElementSibling, token = tok();
    if (!token) { out.textContent = " Falta el token."; return; }
    btn.disabled = true; out.textContent = " Verificando con Hunter…";
    try {
      const r = await fetch("/verificar-contacto", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify({ dominio: dom }) });
      const o = await leerJson(r);
      if (!o.ok) { out.textContent = " Error: " + (o.error || r.status); return; }
      const d = o.data;
      if (d.modo === "hipotesis" || d.verificado === false && d.status === "sin_clave") {
        const h = d.hipotesis || {};
        out.innerHTML = " ⚠️ Sin HUNTER_API_KEY. Candidato: <b>" + esc(h.email_sugerido||"—") + "</b> (sin verificar).";
      } else if (d.verificado) {
        out.innerHTML = " ✅ <b>" + esc(d.email_verificado) + "</b> — verificado por Hunter.";
      } else {
        const h = d.hipotesis || {};
        out.innerHTML = " 🟠 " + esc(d.nota || "no verificado") + (h.email_sugerido ? " · candidato: " + esc(h.email_sugerido) : "");
      }
    } catch (e) { out.textContent = " Error de red: " + e; }
    finally { btn.disabled = false; }
  };

  // ②·⁷ Capa 0: ingesta de noticias y análisis de texto EN LA APP.
  function pintarSenales(items) {
    const cont = $("cap_lista");
    if (!items || !items.length) { cont.innerHTML = '<div class="hint">Aún no hay señales de Capa 0.</div>'; return; }
    cont.innerHTML = items.map(s => `
      <div class="card">
        <div><b>${esc(s.org_nombre || "(sin org)")}</b> <span class="chip">${esc(s.tipo_senal||"")}</span> <span class="chip">${esc(s.nivel_alerta||"")}</span> <span class="chip">score ${s.score_deuda}</span></div>
        <div class="meta">${esc(s.motivo_match||"")}</div>
        <div class="meta">${esc((s.fragmento_literal||"").slice(0,160))}${s.url ? ' · <a href="'+esc(safeUrl(s.url))+'" target="_blank" rel="noopener">fuente ↗</a>' : ""}${s.timestamp_video ? " · ⏱ "+esc(s.timestamp_video) : ""}</div>
      </div>`).join("");
  }
  async function verSenales() {
    try { const d = await (await fetch("/senales-capa0?limite=50")).json(); pintarSenales(d.items); }
    catch (e) { $("cap_lista").innerHTML = '<div class="hint">No se pudieron cargar.</div>'; }
  }
  $("cap_ver").addEventListener("click", verSenales);
  $("cap_noticias").addEventListener("click", async () => {
    const m = $("cap_msg"), token = tok(), query = $("cap_query").value.trim();
    if (!token) { m.className = "msg err"; m.style.display = "block"; m.textContent = "Falta el token."; return; }
    if (!query) { m.className = "msg err"; m.style.display = "block"; m.textContent = "Escribe una búsqueda."; return; }
    document.querySelectorAll("button").forEach(b => b.disabled = true);
    m.className = "msg"; m.style.display = "block"; m.textContent = "Leyendo noticias y analizando…";
    try {
      const r = await fetch("/ingesta/noticias", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify({ query, limite: 25 }) });
      const o = await leerJson(r);
      if (!o.ok) { m.className = "msg err"; m.textContent = "Error: " + (o.error || (o.data && o.data.detail) || r.status); return; }
      const d = o.data;
      m.className = "msg ok";
      m.textContent = `✓ ${d.items} notas leídas · ${d.senales_detectadas} señal(es) de Capa 0 detectada(s).` + (d.senales_detectadas === 0 ? " (Las reglas buscan lenguaje de founder/operación; una noticia rara vez las dispara.)" : "");
      verSenales();
    } catch (e) { m.className = "msg err"; m.textContent = "Error de red: " + e; }
    finally { document.querySelectorAll("button").forEach(b => b.disabled = false); }
  });
  $("cap_analizar").addEventListener("click", async () => {
    const m = $("cap_msg"), token = tok(), texto = $("cap_texto").value.trim();
    if (!token) { m.className = "msg err"; m.style.display = "block"; m.textContent = "Falta el token."; return; }
    if (!texto) { m.className = "msg err"; m.style.display = "block"; m.textContent = "Pega un texto primero."; return; }
    $("cap_analizar").disabled = true; m.className = "msg"; m.style.display = "block"; m.textContent = "Analizando…";
    try {
      const r = await fetch("/webhook/ingesta", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify({ texto, org_name: $("cap_org").value.trim() || null }) });
      const o = await leerJson(r);
      if (!o.ok) { m.className = "msg err"; m.textContent = "Error: " + (o.error || (o.data && o.data.detail) || r.status); return; }
      const d = o.data;
      m.className = "msg ok";
      m.textContent = `✓ ${d.senales_detectadas} señal(es) · alerta ${d.nivel_alerta} · score ${d.score_total}.`;
      verSenales();
    } catch (e) { m.className = "msg err"; m.textContent = "Error de red: " + e; }
    finally { $("cap_analizar").disabled = false; }
  });

  // ③ Enriquecer: descubre web + tesis y precarga
  $("enrich_btn").addEventListener("click", async () => {
    const m = $("e_msg"), token = tok(), nombre = $("nombre").value.trim();
    if (!token) { m.className = "msg err"; m.textContent = "Falta el token."; return; }
    if (!nombre) { m.className = "msg err"; m.textContent = "Escribe el nombre primero."; return; }
    $("enrich_btn").disabled = true; m.className = "msg"; m.style.display = "block"; m.textContent = "Buscando web y discurso…";
    try {
      const r = await fetch("/enrich", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify({ nombre }) });
      const res = await leerJson(r);
      if (!res.ok) { m.className = "msg err"; m.textContent = "Error: " + (res.error || (res.data && res.data.detail) || r.status); return; }
      const d = res.data;
      if (d.sitio_web) { $("sitio_web").value = d.sitio_web; if (!$("url_perfil").value) $("url_perfil").value = d.sitio_web; }
      if (d.linkedin) $("linkedin").value = d.linkedin;
      if (d.vertical_sugerida && !$("vertical").value.trim()) $("vertical").value = d.vertical_sugerida;
      if (d.discurso && !$("discurso").value.trim()) { $("discurso").value = d.discurso; $("fuente_discurso").value = "sitio_oficial"; }
      const vsug = d.vertical_sugerida ? ` · vertical sugerida: ${d.vertical_sugerida} (confírmala)` : "";
      // Nivel de confianza del sitio resuelto (evita un único mensaje para todos).
      const TIER = { confirmada: "🟢 Web oficial confirmada", probable: "🟡 Dominio probable (confírmalo)", no_confirmada: "🟠 No se pudo confirmar; usa los enlaces" };
      const etiqueta = TIER[d.sitio_confianza] || TIER.no_confirmada;
      m.className = (d.sitio_confianza === "no_confirmada") ? "msg" : "msg ok"; m.style.display = "block";
      m.textContent = `${etiqueta}${d.sitio_web ? ": " + d.sitio_web : ""}${vsug}`;
      $("e_links").innerHTML =
        `<a href="${esc(safeUrl(d.linkedin))}" target="_blank" rel="noopener">LinkedIn ↗</a> · ` +
        `<a href="${esc(safeUrl(d.google))}" target="_blank" rel="noopener">Google ↗</a>` +
        (d.sitio_web ? ` · <a href="${esc(safeUrl(d.sitio_web))}" target="_blank" rel="noopener">Web ↗</a>` : "");
    } catch (e) { m.className = "msg err"; m.textContent = "Error de red: " + e; }
    finally { $("enrich_btn").disabled = false; }
  });

  // ③ Alta prospecto
  $("enviar").addEventListener("click", async () => {
    const m = $("msg"), token = tok();
    const body = { nombre: $("nombre").value.trim(), categoria: $("categoria").value,
      vertical: $("vertical").value.trim() || null,
      sitio_web: $("sitio_web").value.trim() || null,
      linkedin: $("linkedin").value.trim() || null,
      discurso_corporativo: $("discurso").value.trim() || null,
      tipo_discurso: $("tipo_discurso").value.trim() || null,
      url_perfil: $("url_perfil").value.trim() || null,
      fuente_discurso: $("fuente_discurso").value.trim() || null };
    if (!token) { m.className = "msg err"; m.textContent = "Falta el token."; return; }
    if (!body.nombre) { m.className = "msg err"; m.textContent = "Falta el nombre."; return; }
    $("enviar").disabled = true;
    try {
      const r = await fetch("/prospectos", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token }, body: JSON.stringify(body) });
      const d = await r.json();
      if (r.ok) { m.className = "msg ok"; m.textContent = `✓ ${body.nombre} [${body.categoria}] — ${d.accion}.`;
        ["discurso","tipo_discurso","url_perfil","fuente_discurso","vertical","sitio_web","linkedin"].forEach(id => $(id).value = "");
        $("e_links").innerHTML = "";
        refrescarConteo();
      } else { m.className = "msg err"; m.textContent = "Error: " + (d.detail || r.status); }
    } catch (e) { m.className = "msg err"; m.textContent = "Error de red: " + e; }
    finally { $("enviar").disabled = false; }
  });

  // PWA: registra el service worker para que sea instalable como app.
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
</script>
</body></html>"""


@app.get("/stats")
def stats() -> dict:
    db = get_db()
    return {
        "evidencias_consumibles": db.fetch_one(
            "SELECT COUNT(*) AS n FROM evidencias WHERE estado = ?", (ESTADO_OK,))["n"],
        "evidencias_no_fechadas": db.fetch_one(
            "SELECT COUNT(*) AS n FROM evidencias WHERE estado = 'no_fechado'")["n"],
        "prospectos": db.fetch_one("SELECT COUNT(*) AS n FROM prospectos")["n"],
        "prospectos_por_categoria": prospectos_por_categoria()["categorias"],
        "rechazos": db.fetch_one("SELECT COUNT(*) AS n FROM rechazos")["n"],
        # Desglose de descartes por motivo (dedup/contrato/relevancia): observabilidad
        # para la validación. Se calcula sobre la tabla `rechazos` ya existente.
        "rechazos_por_motivo": {
            r["motivo"]: r["n"]
            for r in db.fetch_all(
                "SELECT motivo, COUNT(*) AS n FROM rechazos GROUP BY motivo ORDER BY n DESC")
        },
        # Distribución de calidad_captura sobre evidencia consumible.
        "calidad_captura": {
            (r["calidad_captura"] or "sin_calidad"): r["n"]
            for r in db.fetch_all(
                "SELECT calidad_captura, COUNT(*) AS n FROM evidencias "
                "WHERE estado = ? GROUP BY calidad_captura", (ESTADO_OK,))
        },
        "fuentes_en_alerta": db.fetch_one(
            "SELECT COUNT(*) AS n FROM salud_fuentes WHERE alerta = 1")["n"],
    }
