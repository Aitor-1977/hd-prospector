# hd-prospector · Motor A

Motor de **extracción de evidencia**: aplicación Python independiente que
extrae, normaliza y almacena señales públicas sobre empresas. **No puntúa, no
clasifica culturalmente, no interpreta.**

> Proyecto autónomo. No depende de ningún otro repositorio.

## Frontera Motor A / Motor B (arquitectura)

Este repo es el **Motor A (objetivo)**: scraping, limpieza, extracción,
deduplicación y señales Nivel 1 (genéricas). Produce un **corpus de evidencia
verificable** — nada más:

```
Fuentes (RSS, Google News, GDELT, job boards, sitios)
      │  todo el fetching ocurre en el backend (Vercel)
      ▼
Motor A  (este repo, hd-prospector)  →  Neon PostgreSQL
      │  GET /corpus  (empresa · fuente · fecha · texto · keywords · confianza)
      ▼
Motor B  (RadarHD, repo aparte)      →  Deuda Cultural™, ICP, hipótesis, relato
```

**Regla dura:** la Deuda Cultural™, el ICP y las hipótesis condicionales son
propiedad intelectual de HD y viven en el **Motor B (RadarHD)**, NUNCA en este
repo. Aquí las señales son una taxonomía **genérica y pública** (ronda, despidos,
churn/retención, expansión, cambio de liderazgo, lanzamiento, adquisición). Si
cambia la metodología HD, se toca solo el Motor B; este scraper no se modifica.
El contrato de consumo es el endpoint estable `GET /corpus` (`motor_a.corpus.v1`).

## Estado (Fase 1 — COMPLETA)

- ✅ Clase base `Connector` (search / fetch / normalize / validate)
- ✅ Modelo de datos + contrato de evidencia
- ✅ Validador (guardián único del contrato; incompletos → `rechazos`)
- ✅ **Google News RSS** de punta a punta (extracción → normalización →
  validación → escritura en SQLite, con dedup y retención de crudo)
- ✅ **GDELT DOC 2.0 API** de punta a punta (mismo pipeline, JSON `ArtList`)
- ✅ **Feeds RSS fijos** (8 medios) con filtro por mención literal y salud por feed
- ✅ **Job boards JSON** (Greenhouse, Lever, Ashby por slug); `tipo_evento` y
  `origen_declaracion` estructurales (`contratacion` / `operador`)
- ✅ Gobernanza: rate limiting + backoff, salud por fuente, retención 90 días
- ✅ Cola `jobs` en SQLite (sin Redis) + scheduler cada 12 h (APScheduler)
- ✅ API FastAPI de solo lectura

Los 4 conectores de Fase 1 están completos. Se implementaron **uno a uno**: cada
uno debía funcionar de punta a punta antes de escribir el siguiente (ver `CLAUDE.md`).

## Arquitectura

```
search(query) → fetch(url) → normalize(raw) → validate(record)
                                                   │
                         ┌─────────────────────────┴───────────────┐
                     válido                                     inválido
        guardar crudo (gz, 90d) + INSERT dedup                 rechazos(motivo)
                  → tabla evidencias                        (nunca a evidencias)
```

- `hd_scraper/connectors/` — conectores intercambiables (`base.py`, `google_news.py`, `gdelt.py`, `rss_fijos.py`, `job_boards.py`)
- `hd_scraper/db/` — modelo (`models.py`), esquema portable (`schema.sql`), acceso (`database.py`)
- `hd_scraper/validation/` — validador del contrato
- `hd_scraper/governance/` — rate limiting/backoff, salud por fuente
- `hd_scraper/storage/` — retención del crudo comprimido
- `hd_scraper/pipeline.py` — orquestación de punta a punta
- `hd_scraper/jobs.py` — cola en SQLite
- `hd_scraper/scheduler.py` — corridas cada 12 h
- `hd_scraper/api/app.py` — API de solo lectura

## Uso

```bash
pip install -r requirements.txt

# Corrida única (verificación de punta a punta)
python -m scripts.run_once "Nubank" --tipo ronda                         # google_news
python -m scripts.run_once "Nubank" --tipo ronda --connector gdelt       # gdelt
python -m scripts.run_once "Nubank" --tipo lanzamiento --connector rss_fijos
python -m scripts.run_once "Acme" --connector job_boards --slug acme      # requiere --slug

# API de solo lectura (+ scheduler cada 12h)
python -m scripts.serve_api
#   GET /evidencias?empresa=Nubank&tipo_evento=ronda
#   GET /evidencias/{id}
#   GET /salud-fuentes
#   GET /stats

pytest -q
```

## Contrato de datos

Todo registro en `evidencias` debe traer: `cita_textual`, `fecha_extraccion`,
`url_fuente`, `nombre_medio`, `empresa_mencionada`, `tipo_evento`
(`ronda|contratacion|despido|lanzamiento|queja|cambio_sitio`),
`origen_declaracion` (`operador|inversor|prensa|usuario`) y `hash_dedup`
(sha256 de empresa + URL normalizada, único). `persona_citada` y `cargo` son
opcionales. Si falta `fecha_publicacion`, el registro queda `no_fechado` y **no
es consumible por la API**. Los incompletos van a `rechazos`, nunca a
`evidencias`.

## Prospectos — los cuatro ecosistemas

El radar se enfoca en cuatro ecosistemas estratégicos. La tabla `prospectos`
guarda cada entidad objetivo con una **categoría obligatoria** (`CHECK` en la
BD) y su discurso corporativo ("Thick Data") extraído de URLs o perfiles:

| Categoría | Discurso típico (Thick Data) |
|---|---|
| `VC` | tesis de inversión |
| `Startup` | promesa de valor, perfil de founders/C-level |
| `Incubadora` | programas de aceleración, portafolio |
| `Corporativo` | comunicados institucionales, reportes de innovación |

Campos de `prospectos`: `nombre`, `categoria` (obligatoria y literal),
`discurso_corporativo` (texto largo), `tipo_discurso`, `url_perfil`,
`fuente_discurso`, `fecha_captura`, `hash_dedup` (sha256 de nombre + categoría,
único). El motor **almacena** el discurso tal cual — no lo interpreta ni lo
puntúa. La categoría la declara el operador al alta (estructural), no se infiere
del texto. La escritura es UPSERT por `hash_dedup` (enriquece sin duplicar);
los inválidos van a `rechazos`.

Endpoints de lectura (públicos): `GET /prospectos` (filtros `categoria`, `q`,
`con_discurso`), `GET /prospectos/categorias`, `GET /prospectos/{id}`.

### Alta de prospectos (intake del operador)

La evidencia nunca se escribe por la API (solo el pipeline). Los **prospectos**
sí tienen una intake autenticada del operador:

- `POST /prospectos` y `POST /prospectos/bulk` — requieren la cabecera
  `X-Ingest-Token` igual a `HD_INGEST_TOKEN`. Sin ese token configurado, la
  escritura está **deshabilitada** (503). Reusan `upsert_prospecto`.
- `POST /scrape` — rastreo **bajo demanda** que escribe evidencia al momento.
  Requiere `X-Ingest-Token`. Dos modos:
  - **Por ecosistema** (`{"categoria": "VC"}`): corre las consultas temáticas
    declaradas del ecosistema (ver `hd_scraper/discovery.py`) y **etiqueta** la
    evidencia con esa categoría. Para descubrir sin nombrar empresas.
  - **Por nombre** (`{"empresa": "Nubank"}`): rastrea una empresa concreta.
  Corre en función serverless (no necesita host always-on); el scheduler cada
  12 h sí. Con disco efímero, la retención de crudo se apaga con `HD_RAW_ENABLED=0`.
- `GET /admin` — panel web autocontenido (móvil) con el flujo completo:
  ① **buscar señales** (scraping) → ② **revisar** lo encontrado → ③ dar de alta
  el prospecto con su discurso. Pega el token una vez y opera sin terminal.

## Base de datos

SQLite en Fase 1, con esquema escrito para migrar a PostgreSQL sin tocar el
modelo (ver notas en `hd_scraper/db/schema.sql`). El `CHECK` de `categoria` y el
UPSERT por `hash_dedup` funcionan igual en SQLite y en Postgres.

## Despliegue

El repo incluye `vercel.json` y `api/index.py` para enlazarlo a Vercel como una
app nueva. **Importante — qué corre y qué no en Vercel:**

- ✅ **La API de solo lectura** (`/health`, `/evidencias`, `/salud-fuentes`,
  `/stats`) se despliega como función serverless Python y responde de inmediato.
- ⚠️ **El scraper NO corre en Vercel.** El scheduler cada 12 h y la escritura en
  la base necesitan un proceso *always-on* y disco persistente; el serverless
  de Vercel es efímero y sin procesos de larga vida. En Vercel la base vive en
  `/tmp` (se reinicia en cada invocación), así que la API responderá **vacía**
  hasta conectar una base persistente.

Arquitectura recomendada para producción:

1. **API (lectura)** → Vercel (este repo, tal cual).
2. **Extracción (scraper + scheduler)** → un host always-on (una VM pequeña, un
   worker de Railway/Render/Fly, o un cron externo que invoque
   `python -m scripts.run_once ...`).
3. **Base** → PostgreSQL gestionado (Neon, Supabase, RDS). El esquema ya es
   portable; se apunta `HD_DATABASE_URL` a Postgres y ambos lados leen/escriben
   la misma base.

Para un despliegue de demo (solo ver la API viva, con datos vacíos), basta con
enlazar este repo a Vercel sin configurar nada más.
