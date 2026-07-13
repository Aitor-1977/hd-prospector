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

from ..discovery import REGIONES, VERTICALES_HD, queries_para, region_clause
from ..enrich import enriquecer
from ..pipeline import run_connector
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


def _row_a_corpus(row) -> dict:
    """Contrato estable del corpus (Motor A → Motor B / RadarHD). Solo hechos."""
    return {
        "empresa": row["empresa_mencionada"],
        "fuente": row["nombre_medio"],
        "fecha": row["fecha_publicacion"],
        "texto": row["cita_textual"],
        "url": row["url_fuente"],
        "keywords": _keywords(row),
        "confianza": row["confianza"],
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
        for termino, tipo in queries_para(payload.categoria, payload.tipo_evento, payload.vertical):
            query = QuerySpec(empresa=termino, tipo_evento=tipo, terminos=zona,
                              categoria=payload.categoria, exact=False)
            resultados += _correr_query(db, query, ["google_news"])
        modo = {"modo": "categoria", "categoria": payload.categoria,
                "tipo_evento": payload.tipo_evento, "vertical": payload.vertical,
                "region": payload.region}
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
  .dl { display: block; text-align: center; text-decoration: none; color: inherit;
    padding: .7rem; border: 1px solid rgba(128,128,128,.5); border-radius: .5rem; font-weight: 600; }
</style></head><body>
<h1>hd-prospector · Radar</h1>
<p class="sub">Radar de prospección para Hamaca Digital. ① Rastrea candidatos por ecosistema, vertical y señal de fricción → ② revísalos → ③ el expediente se <b>auto-investiga</b> (web, tesis, vertical) y tú lo confirmas. Extrae y almacena hechos públicos; la interpretación (Deuda Cultural™) es de HD, no del motor.</p>

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
  <h2>② Candidatos encontrados</h2>
  <div class="hint">Titulares del ecosistema/vertical. Toca “abrir” para leer la fuente, o “➕ prospecto”: se investiga el perfil automáticamente.</div>
  <div id="evidencias"></div>
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
      const d = await r.json();
      if (!r.ok) { m.className = "msg err"; m.textContent = "Error: " + (d.detail || r.status); return; }
      const vistas = (d.resultados||[]).reduce((a,x)=>a+(x.vistos||0),0);
      const dup = (d.resultados||[]).reduce((a,x)=>a+(x.duplicados||0),0);
      const err = (d.resultados||[]).reduce((a,x)=>a+(x.errores||0)+(x.error?1:0),0);
      m.className = "msg ok";
      m.textContent = `✓ ${d.total_escritos} nuevas · ${vistas} vistas · ${dup} repetidas · ${err} errores — ${etiqueta}.`;
      cargar();
    } catch (e) { m.className = "msg err"; m.textContent = "Error de red: " + e; }
    finally { document.querySelectorAll("button").forEach(b => b.disabled = false); }
  }

  const region = () => $("region").value;

  document.querySelectorAll(".cat").forEach(btn => btn.addEventListener("click", () => {
    const cat = btn.dataset.cat, tipo = $("c_tipo").value, vert = $("c_vertical").value;
    if ($("categoria")) $("categoria").value = cat;   // precarga categoría en ③
    scrapear({ categoria: cat, tipo_evento: tipo, vertical: vert, region: region() },
             `${cat} · ${tipo} · ${vert} · ${region()}`, () => cargarEvidencias({ categoria: cat }));
  }));

  $("s_btn").addEventListener("click", () => {
    const empresa = $("s_empresa").value.trim();
    if (!empresa) { const m = $("s_msg"); m.className = "msg err"; m.style.display = "block"; m.textContent = "Escribe una empresa."; return; }
    $("nombre").value = empresa;                       // precarga para ③
    scrapear({ empresa, tipo_evento: $("s_tipo").value, region: region(), connectors: $("s_fuentes").value.split(",") },
             empresa, () => cargarEvidencias({ empresa }));
  });

  // ② Listar evidencias (por empresa o por categoría)
  async function cargarEvidencias(filtro) {
    const cont = $("evidencias");
    const qs = new URLSearchParams({ limite: "25", ...filtro });
    try {
      const d = await (await fetch("/evidencias?" + qs)).json();
      if (!d.items.length) { cont.innerHTML = '<div class="hint">Sin señales fechadas todavía. Prueba otra categoría o revisa /stats.</div>'; return; }
      cont.innerHTML = d.items.map(e => `
        <div class="card">
          <div>${esc(e.cita_textual)}</div>
          <div class="meta">${esc(e.nombre_medio)} · ${esc(e.tipo_evento)}${e.categoria ? " · " + esc(e.categoria) : ""} · ${(e.fecha_publicacion||"").slice(0,10)}
            · <a href="${esc(safeUrl(e.url_fuente))}" target="_blank" rel="noopener">abrir ↗</a></div>
          <button class="sec" onclick="prefill(this.dataset.n)" data-n="${esc(e.empresa_mencionada)}">➕ Guardar como prospecto</button>
        </div>`).join("");
    } catch (e) { cont.innerHTML = '<div class="hint">No se pudieron cargar.</div>'; }
  }
  window.prefill = (nombre) => {
    $("nombre").value = nombre;
    $("nombre").scrollIntoView({ behavior: "smooth" });
    $("enrich_btn").click();   // investiga automáticamente al promover (research-first)
  };

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
      const d = await r.json();
      if (!r.ok) { m.className = "msg err"; m.textContent = "Error: " + (d.detail || r.status); return; }
      if (d.sitio_web) { $("sitio_web").value = d.sitio_web; if (!$("url_perfil").value) $("url_perfil").value = d.sitio_web; }
      if (d.linkedin) $("linkedin").value = d.linkedin;
      if (d.vertical_sugerida && !$("vertical").value.trim()) $("vertical").value = d.vertical_sugerida;
      if (d.discurso && !$("discurso").value.trim()) { $("discurso").value = d.discurso; $("fuente_discurso").value = "sitio_oficial"; }
      const vsug = d.vertical_sugerida ? ` · vertical sugerida: ${d.vertical_sugerida} (confírmala)` : "";
      m.className = "msg ok";
      m.textContent = (d.sitio_web ? `✓ Web y discurso cargados${(d.notas||[]).length? " ("+d.notas.join("; ")+")":""}.` : `Sin web clara. Usa los enlaces. ${(d.notas||[]).join("; ")}`) + vsug;
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
        "fuentes_en_alerta": db.fetch_one(
            "SELECT COUNT(*) AS n FROM salud_fuentes WHERE alerta = 1")["n"],
    }
