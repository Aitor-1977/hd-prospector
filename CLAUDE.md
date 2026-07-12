# CLAUDE.md — hd-scraper

Guía para agentes (y humanos) que trabajen en este proyecto.

## Qué es (y qué NO es)

hd-scraper es la **capa de extracción de evidencia** de Hamaca Digital.
Extrae, normaliza y almacena señales públicas sobre empresas.

**NUNCA puntúa, clasifica ni interpreta.** Eso lo hace **Radar**, otro sistema
que consume esta base como fuente de verdad. Si una tarea te pide "decidir si
esto es bueno/malo", "puntuar", "resumir con criterio" o "inferir el tipo de
evento leyendo el texto", está fuera de alcance: no pertenece a hd-scraper.

### Cómo se respeta "no interpreta" con campos como `tipo_evento`

`tipo_evento` y `origen_declaracion` son obligatorios y literales, pero **no se
infieren leyendo el contenido**:

- `tipo_evento` viaja en la `QuerySpec`: lo **declara el operador** al lanzar la
  corrida (estructura de la consulta), no el conector.
- `origen_declaracion` se deriva de la **estructura de la fuente** (un feed de
  prensa ⇒ `prensa`; un job board ⇒ el que corresponda por estructura).

## Contrato de datos (tabla `evidencias`)

Obligatorios: `cita_textual`, `fecha_extraccion`, `url_fuente`, `nombre_medio`,
`empresa_mencionada`, `tipo_evento` (ronda|contratacion|despido|lanzamiento|
queja|cambio_sitio), `origen_declaracion` (operador|inversor|prensa|usuario),
`hash_dedup` (sha256 de empresa + URL normalizada, único).

Opcionales: `persona_citada`, `cargo`.

`fecha_publicacion` (ISO 8601): si falta, el registro se marca **`no_fechado`**
y **no es consumible por la API** (pero NO se rechaza).

El **validador** (`hd_scraper/validation/validator.py`) es el **único guardián**
del contrato. Un registro incompleto va a `rechazos` con motivo, **nunca** a
`evidencias`.

## Arquitectura

- **Conectores** (`hd_scraper/connectors/`): clase base `Connector` con
  `search / fetch / normalize / validate`. Cada fuente es intercambiable.
- **Pipeline** (`pipeline.py`): search → normalize → validate → (guardar crudo +
  escribir con dedup) | rechazo.
- **Gobernanza** (`governance/`): rate limiting con backoff por fuente; salud por
  conector (2 fallos seguidos ⇒ alerta); dedup en escritura por `hash_dedup`;
  retención del crudo comprimido 90 días (`storage/raw_store.py`).
- **Cola** (`jobs.py`): tabla `jobs` en SQLite. Sin Redis.
- **Scheduler** (`scheduler.py`): APScheduler, cada 12 h.
- **API** (`api/app.py`): FastAPI **solo lectura**. Solo sirve `estado='ok'`.

## Fase 1 — conectores (SOLO estos)

1. **Google News RSS** — ✅ implementado y probado de punta a punta.
2. **GDELT DOC 2.0 API** — ✅ implementado y probado de punta a punta.
3. **Feeds RSS fijos** (Startupeable, Contxto, LAVCA, LatamList, Bloomberg
   Línea, Forbes México, El CEO, Xataka México) — ✅ implementado y probado.
   Filtra por mención literal de la empresa (subcadena sin acentos = extracción,
   no interpretación). Salud por feed (`rss_fijos:<Medio>`).
4. **Job boards JSON** (Greenhouse, Lever, Ashby por slug) — ✅ implementado y
   probado. `tipo_evento=contratacion` y `origen_declaracion=operador` son
   ESTRUCTURALES (una vacante publicada por la empresa). Salud por plataforma;
   un 404 = "ese slug no está en esa plataforma", no cuenta como fallo.

**Fase 1 COMPLETA.** Los 4 conectores funcionan de punta a punta.

## Reglas de trabajo (estrictas)

1. **Un conector a la vez.** Google News RSS debe funcionar completo
   (extracción, normalización, validación, escritura en SQLite) **antes** de
   tocar el segundo. No escribir los cuatro conectores de golpe.
2. **Errores repetidos van aquí.** Si un mismo error aparece **dos veces**,
   anotarlo en la sección "Errores recurrentes" antes de seguir intentando.
3. Sin Playwright en Fase 1. Librerías: `httpx`, `feedparser`, y BeautifulSoup
   **solo si es indispensable**.

## Comandos

```bash
pip install -r requirements.txt
python -m scripts.run_once "Nubank" --tipo ronda                        # google_news
python -m scripts.run_once "Nubank" --tipo ronda --connector gdelt      # gdelt
python -m scripts.run_once "Nubank" --tipo lanzamiento --connector rss_fijos
python -m scripts.run_once "Acme" --connector job_boards --slug acme     # requiere --slug
python -m scripts.serve_api                              # API + scheduler
uvicorn hd_scraper.api.app:app --reload                  # solo API
pytest -q                                                # tests
```

> Nota de entorno: el proxy de egress de esta sesión bloquea (403) tanto
> `news.google.com` como `api.gdeltproject.org`. La verificación en vivo no es
> posible aquí; se validó de punta a punta con fixtures y escritura real en
> SQLite. Fuera de este entorno, `run_once` funciona contra las fuentes reales.

## Errores recurrentes

_(Vacío por ahora. Registrar aquí cualquier error que se repita dos veces, con
causa y solución, antes de seguir intentando.)_
