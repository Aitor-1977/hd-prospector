# CLAUDE.md — hd-scraper

Guía para agentes (y humanos) que trabajen en este proyecto.

## Qué es (y qué NO es)

hd-scraper es la **capa de extracción de evidencia** de Hamaca Digital.
Extrae, normaliza y almacena señales públicas sobre empresas.

**NUNCA puntúa, clasifica culturalmente ni interpreta.** Eso lo hace **Radar**,
otro sistema que consume esta base como fuente de verdad.

## Frontera Motor A / Motor B (INVIOLABLE)

Este repo es **Motor A (objetivo)**: scraping, limpieza, extracción, dedup y
señales Nivel 1 con taxonomía **genérica/pública** (ronda, despidos, churn,
expansión, liderazgo, lanzamiento, adquisición). Emite el corpus por `GET
/corpus` (contrato `motor_a.corpus.v1`: empresa·fuente·fecha·texto·keywords·
confianza).

La **Deuda Cultural™ (Moral/Temporal/Relacional/Ontológica/Epistémica)**, el
**score ICP** y las **hipótesis condicionales** son IP de HD y viven en el
**Motor B (repo RadarHD)** — JAMÁS en este repo. Si alguien pide "clasificar la
Deuda Cultural" aquí, está fuera de alcance: pertenece a RadarHD. La `confianza`
mide calidad de extracción, no juicio del contenido. Si una tarea te pide "decidir si
esto es bueno/malo", "puntuar", "resumir con criterio" o "inferir el tipo de
evento leyendo el texto", está fuera de alcance: no pertenece a hd-scraper.

> **Nota (2026-07-22):** el alcance exacto de "interpretación" admisible en este
> repo fue precisado por decisión del operador en la sección
> **«Frontera de Interpretación (hd-scraper vs RadarHD)»** (más abajo). En lo que
> respecta a scoring A/B/C, criterios ICP y clasificación preliminar de señales
> de Deuda Cultural™ sobre datos ya extraídos, esa sección es la referencia
> vigente; el resto de las prohibiciones de este apartado sigue intacto.

### Cómo se respeta "no interpreta" con campos como `tipo_evento`

`tipo_evento` y `origen_declaracion` son obligatorios y literales, pero **no se
infieren leyendo el contenido**:

- `tipo_evento` viaja en la `QuerySpec`: lo **declara el operador** al lanzar la
  corrida (estructura de la consulta), no el conector.
- `origen_declaracion` se deriva de la **estructura de la fuente** (un feed de
  prensa ⇒ `prensa`; un job board ⇒ el que corresponda por estructura).

## Frontera de Interpretación (hd-scraper vs RadarHD)

> Sección añadida el 2026-07-22 por autorización explícita del operador
> (registrada en `MEMORIA.md`), para resolver la contradicción normativa
> detectada en `AUDITORIA_MOTORES.md` (hallazgo 3). Es una **adición** que
> precisa la frontera; no deroga el resto de este documento.

**Justificación en una frase:** hd-scraper puede **clasificar la señal que
extrae**, pero **no puede decidir ni ejecutar acción comercial sobre ella**;
esa decisión es exclusiva de Mario vía RadarHD.

**Admisible en este repo** (interpretación determinista y auditable, aplicada
SOLO sobre datos ya extraídos por este mismo motor; sin IA, sin juicio libre):

- **Scoring A/B/C** de señales capturadas.
- **Aplicación de criterios ICP** (score 0–100 por reglas declaradas).
- **Clasificación preliminar de señales de Deuda Cultural™**: hipótesis
  etiquetadas y reproducibles (mismo insumo ⇒ mismo resultado), marcadas siempre
  como preliminares.

**Exclusivo de RadarHD (JAMÁS aquí):**

- Todo análisis con **Gemini** u otro LLM.
- El **dashboard**.
- El **pipeline comercial** (seguimiento, contacto ejecutado, envíos).
- Cualquier decisión sobre **Expediente Activado**.

**Implementación actual de esta frontera:** `hd_scraper/analisis.py` (scoring,
ICP, Deuda preliminar) y `hd_scraper/engine/rule_engine.py` (reglas y pesos de
señal). No reproducir esa lógica en otros módulos.

**Regla de ampliación:** cualquier ampliación futura de interpretación en este
repo exige actualizar **esta misma sección ANTES de escribir código**. Si una
tarea propone interpretación no listada aquí, está fuera de alcance hasta que
esta sección cambie.

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

## Prospectos (cuatro ecosistemas)

Tabla `prospectos`: entidades objetivo del radar. `categoria` es OBLIGATORIA y
literal — `VC | Startup | Incubadora | Corporativo` (`CHECK` en la BD). La
declara el operador al alta (estructural), NO se infiere del discurso.

Campos "Thick Data" (`discurso_corporativo`, `tipo_discurso`, `url_perfil`,
`fuente_discurso`, `fecha_captura`) guardan el discurso corporativo extraído de
URLs/perfiles: el motor lo ALMACENA, no lo interpreta. Escritura vía
`hd_scraper/prospectos.py:upsert_prospecto` (UPSERT por `hash_dedup`, enriquece
sin duplicar; inválidos → `rechazos`). Validación: `validate_prospecto`.

Intake del operador (única escritura vía API; la evidencia NUNCA se escribe por
API): `POST /prospectos` y `/prospectos/bulk` con cabecera `X-Ingest-Token` ==
`HD_INGEST_TOKEN` (sin token → 503). `GET /admin` sirve un formulario web.

Base: SQLite (dev/tests) o PostgreSQL (producción), según la URL. El driver
(`db/database.py`) traduce marcadores y elige el esquema; `scripts/migrate.py`
crea el esquema. En Vercel se auto-detecta `DATABASE_URL`/`POSTGRES_URL`.

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

1. **Feeds parseados desde `resp.text` en vez de bytes (2 ocurrencias).**
   `connectors/google_news.py:72-73` y `connectors/rss_fijos.py:76-77` pasan a
   `feedparser.parse()` el texto ya decodificado por httpx (charset adivinado por
   cabecera HTTP), lo que anula la detección de encoding del prólogo XML del
   feed. Riesgo: mojibake en `cita_textual` cuando el charset real difiere de la
   cabecera. **Causa:** `_get()` devuelve `resp.text` por comodidad. **Solución
   (cuando se toque ese código):** pasar `resp.content` (bytes) a feedparser y
   dejar que él resuelva el encoding. Detectado en auditoría 2026-07-22 (ver
   `AUDITORIA_MOTORES.md`, hallazgo 4). No corregido aún: la auditoría fue de
   solo lectura.
