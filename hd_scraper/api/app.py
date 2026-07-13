"""API interna de SOLO LECTURA (FastAPI).

Expone la evidencia ya validada para que Radar (u otros consumidores) la lean.
No hay endpoints de escritura: la extracción es responsabilidad del pipeline /
scheduler, no de la API.

Regla del contrato: la API SOLO sirve registros consumibles (estado = 'ok').
Los registros ``no_fechado`` existen en la base pero NO son consumibles y no se
devuelven por los endpoints de evidencia.
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..config import settings
from ..db.database import get_db
from ..db.models import CATEGORIAS, ESTADO_OK, TIPOS_EVENTO
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
        "ecosistemas": sorted(CATEGORIAS),
        "endpoints": {
            "GET /health": "estado del servicio",
            "GET /evidencias": "evidencia consumible (filtros: empresa, tipo_evento, desde, hasta)",
            "GET /evidencias/{id}": "una evidencia por id",
            "GET /prospectos": "prospectos por ecosistema (filtros: categoria, q, con_discurso)",
            "GET /prospectos/categorias": "conteo de prospectos por ecosistema",
            "GET /prospectos/{id}": "un prospecto por id (incluye Thick Data)",
            "POST /prospectos": "alta de prospecto (requiere X-Ingest-Token)",
            "GET /admin": "formulario web de alta de prospectos",
            "GET /salud-fuentes": "salud por fuente/conector",
            "GET /stats": "contadores agregados",
            "GET /docs": "documentación interactiva (OpenAPI)",
        },
        "nota": "API de solo lectura. La extracción corre en un host aparte (ver README).",
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


def _row_a_prospecto(row) -> dict:
    return {
        "id": row["id"],
        "nombre": row["nombre"],
        "categoria": row["categoria"],
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


@app.get("/admin", response_class=HTMLResponse)
def admin_form() -> str:
    """Pantalla de alta de prospectos (formulario web, sin dependencias externas)."""
    return _ADMIN_HTML


_ADMIN_HTML = """<!doctype html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>hd-prospector · Alta de prospectos</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0;
         padding: 1.2rem; max-width: 640px; margin-inline: auto; line-height: 1.4; }
  h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
  p.sub { margin: 0 0 1.2rem; opacity: .7; font-size: .9rem; }
  label { display: block; font-weight: 600; margin: .8rem 0 .25rem; font-size: .9rem; }
  input, select, textarea { width: 100%; padding: .6rem .7rem; border-radius: .5rem;
    border: 1px solid rgba(128,128,128,.4); background: transparent; color: inherit;
    font-size: 1rem; font-family: inherit; }
  textarea { min-height: 110px; resize: vertical; }
  .req::after { content: " *"; color: #e11; }
  button { margin-top: 1.1rem; width: 100%; padding: .8rem; border: 0; border-radius: .5rem;
    background: #2563eb; color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: .5; }
  #msg { margin-top: 1rem; padding: .8rem; border-radius: .5rem; display: none; font-size: .9rem; }
  #msg.ok { background: rgba(22,163,74,.15); border: 1px solid rgba(22,163,74,.5); display: block; }
  #msg.err { background: rgba(220,38,38,.15); border: 1px solid rgba(220,38,38,.5); display: block; }
  .counts { display: flex; gap: .5rem; flex-wrap: wrap; margin: 1rem 0; }
  .chip { padding: .3rem .6rem; border-radius: 1rem; border: 1px solid rgba(128,128,128,.4);
    font-size: .85rem; }
  .hint { font-size: .8rem; opacity: .65; margin-top: .2rem; }
</style></head><body>
<h1>hd-prospector · Alta de prospectos</h1>
<p class="sub">Registra entidades de los cuatro ecosistemas con su discurso corporativo (Thick Data). No interpreta: solo almacena.</p>

<div class="counts" id="counts"></div>

<label class="req">Token de ingesta</label>
<input id="token" type="password" placeholder="HD_INGEST_TOKEN" autocomplete="off">
<div class="hint">Se guarda solo en este dispositivo (localStorage). Es el valor que pusiste en Vercel.</div>

<label class="req">Nombre</label>
<input id="nombre" placeholder="p. ej. Kaszek">

<label class="req">Categoría (ecosistema)</label>
<select id="categoria">
  <option value="VC">VC — fondo / venture capital</option>
  <option value="Startup">Startup</option>
  <option value="Incubadora">Incubadora / Aceleradora</option>
  <option value="Corporativo">Corporativo</option>
</select>

<label>Discurso corporativo (Thick Data)</label>
<textarea id="discurso" placeholder="Tesis de inversión, promesa de valor, programa, comunicado…"></textarea>

<label>Tipo de discurso</label>
<input id="tipo_discurso" placeholder="tesis_inversion | promesa_valor | programa | comunicado…">

<label>URL / perfil de origen</label>
<input id="url_perfil" placeholder="https://…">

<label>Fuente del discurso</label>
<input id="fuente_discurso" placeholder="sitio_oficial, linkedin, prensa…">

<button id="enviar">Guardar prospecto</button>
<div id="msg"></div>

<script>
  const $ = id => document.getElementById(id);
  $("token").value = localStorage.getItem("hd_ingest_token") || "";

  async function refrescarConteo() {
    try {
      const r = await fetch("/prospectos/categorias");
      const d = await r.json();
      $("counts").innerHTML = Object.entries(d.categorias)
        .map(([k, v]) => `<span class="chip">${k}: <b>${v}</b></span>`).join("");
    } catch (e) {}
  }
  refrescarConteo();

  $("enviar").addEventListener("click", async () => {
    const msg = $("msg");
    const token = $("token").value.trim();
    localStorage.setItem("hd_ingest_token", token);
    const body = {
      nombre: $("nombre").value.trim(),
      categoria: $("categoria").value,
      discurso_corporativo: $("discurso").value.trim() || null,
      tipo_discurso: $("tipo_discurso").value.trim() || null,
      url_perfil: $("url_perfil").value.trim() || null,
      fuente_discurso: $("fuente_discurso").value.trim() || null,
    };
    if (!token) { msg.className = "err"; msg.textContent = "Falta el token de ingesta."; return; }
    if (!body.nombre) { msg.className = "err"; msg.textContent = "Falta el nombre."; return; }
    $("enviar").disabled = true;
    try {
      const r = await fetch("/prospectos", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Ingest-Token": token },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (r.ok) {
        msg.className = "ok";
        msg.textContent = `✓ ${body.nombre} [${body.categoria}] — ${d.accion}.`;
        $("nombre").value = ""; $("discurso").value = ""; $("tipo_discurso").value = "";
        $("url_perfil").value = ""; $("fuente_discurso").value = "";
        refrescarConteo();
      } else {
        msg.className = "err";
        msg.textContent = "Error: " + (d.detail || r.status);
      }
    } catch (e) {
      msg.className = "err"; msg.textContent = "Error de red: " + e;
    } finally {
      $("enviar").disabled = false;
    }
  });
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
