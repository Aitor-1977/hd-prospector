-- Esquema de hd-scraper.
-- Escrito en SQL portable: usa tipos e idioms que existen tanto en SQLite
-- como en PostgreSQL. Notas de migración a Postgres:
--   * INTEGER PRIMARY KEY AUTOINCREMENT  -> GENERATED ALWAYS AS IDENTITY / BIGSERIAL
--   * las fechas se guardan como TEXT ISO 8601 (portable; en Postgres se puede
--     migrar la columna a TIMESTAMPTZ sin tocar el modelo).
--   * INSERT ... ON CONFLICT existe en ambos motores.

-- Evidencias: registros que CUMPLEN el contrato. Nunca entra aquí un registro
-- incompleto (eso va a `rechazos`). `estado` distingue consumibles de no_fechado.
CREATE TABLE IF NOT EXISTS evidencias (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Contrato obligatorio
    cita_textual        TEXT NOT NULL,
    fecha_extraccion    TEXT NOT NULL,          -- ISO 8601
    url_fuente          TEXT NOT NULL,
    nombre_medio        TEXT NOT NULL,
    empresa_mencionada  TEXT NOT NULL,
    tipo_evento         TEXT NOT NULL,          -- literal: ronda|contratacion|despido|lanzamiento|queja|cambio_sitio
    origen_declaracion  TEXT NOT NULL,          -- literal: operador|inversor|prensa|usuario
    hash_dedup          TEXT NOT NULL UNIQUE,   -- sha256(empresa + url normalizada)
    -- Contrato opcional
    fecha_publicacion   TEXT,                   -- ISO 8601; NULL => estado no_fechado
    persona_citada      TEXT,
    cargo               TEXT,
    -- Metadatos internos
    connector           TEXT NOT NULL,
    estado              TEXT NOT NULL DEFAULT 'ok',  -- ok | no_fechado
    raw_hash            TEXT,                   -- enlace al crudo retenido (raw_store)
    categoria           TEXT,                   -- ecosistema si viene de descubrimiento por categoría
    keywords            TEXT,                   -- JSON: etiquetas de señal Nivel 1 (objetivas)
    confianza           REAL NOT NULL DEFAULT 0, -- calidad objetiva de la extracción 0–1
    -- Captura Inteligente (dedup robusto + calidad informativa)
    clave_contenido     TEXT,                   -- identidad de contenido (url:/txt:) para dedup robusto
    hash_contenido      TEXT,                   -- sha256 del título normalizado (dedup entre URLs distintas)
    calidad_captura     TEXT,                   -- Alta | Media | Baja (informativa; no altera el scoring)
    creado_en           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidencias_empresa ON evidencias (empresa_mencionada);
CREATE INDEX IF NOT EXISTS idx_evidencias_tipo    ON evidencias (tipo_evento);
CREATE INDEX IF NOT EXISTS idx_evidencias_estado  ON evidencias (estado);
CREATE INDEX IF NOT EXISTS idx_evidencias_fpub    ON evidencias (fecha_publicacion);
CREATE INDEX IF NOT EXISTS idx_evidencias_categoria ON evidencias (categoria);
CREATE INDEX IF NOT EXISTS idx_evidencias_clave  ON evidencias (clave_contenido);
CREATE INDEX IF NOT EXISTS idx_evidencias_hashc  ON evidencias (hash_contenido);

-- Rechazos: todo registro que no pasa el validador, con su motivo. Auditable.
CREATE TABLE IF NOT EXISTS rechazos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    connector     TEXT NOT NULL,
    motivo        TEXT NOT NULL,
    payload_json  TEXT NOT NULL,   -- registro crudo/normalizado que se rechazó
    creado_en     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rechazos_motivo ON rechazos (motivo);

-- Prospectos: entidades objetivo de los CUATRO ecosistemas estratégicos.
-- `categoria` es OBLIGATORIA y acotada por CHECK (portable a SQLite y Postgres).
-- Los campos de "Thick Data" guardan el discurso corporativo extraído de URLs o
-- perfiles: el motor los ALMACENA tal cual, no los interpreta.
CREATE TABLE IF NOT EXISTS prospectos (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre                TEXT NOT NULL,
    categoria             TEXT NOT NULL,   -- VC | Startup | Incubadora | Corporativo
    -- Perfil de la entidad
    vertical              TEXT,            -- sector/vertical (declarado o del sitio)
    sitio_web             TEXT,            -- URL del sitio oficial
    linkedin              TEXT,            -- enlace a LinkedIn
    -- Thick Data (discurso corporativo)
    discurso_corporativo  TEXT,            -- cuerpo de texto extraído (tesis, promesa, programa, comunicado…)
    tipo_discurso         TEXT,            -- etiqueta estructural (tesis_inversion|promesa_valor|programa|portafolio|comunicado|reporte|perfil)
    url_perfil            TEXT,            -- URL/perfil de donde se extrajo el discurso
    fuente_discurso       TEXT,            -- nombre de la fuente/plataforma
    fecha_captura         TEXT,            -- ISO 8601 de la captura del texto
    -- Metadatos
    hash_dedup            TEXT NOT NULL UNIQUE,  -- sha256(nombre normalizado + categoria)
    creado_en             TEXT NOT NULL,
    actualizado_en        TEXT NOT NULL,
    CHECK (categoria IN ('VC', 'Startup', 'Incubadora', 'Corporativo'))
);

CREATE INDEX IF NOT EXISTS idx_prospectos_categoria ON prospectos (categoria);
CREATE INDEX IF NOT EXISTS idx_prospectos_nombre    ON prospectos (nombre);

-- Cola de trabajos: reemplaza a Redis con una tabla simple en SQLite.
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    connector     TEXT NOT NULL,
    query_json    TEXT NOT NULL,   -- QuerySpec serializado
    estado        TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|error
    intentos      INTEGER NOT NULL DEFAULT 0,
    resultado     TEXT,
    creado_en     TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_estado ON jobs (estado);

-- Salud por fuente: una fila por conector.
CREATE TABLE IF NOT EXISTS salud_fuentes (
    fuente               TEXT PRIMARY KEY,
    ultima_corrida       TEXT,
    ultimo_estado        TEXT,    -- ok | error
    fallos_consecutivos  INTEGER NOT NULL DEFAULT 0,
    alerta               INTEGER NOT NULL DEFAULT 0,  -- 0/1 (boolean portable)
    detalle              TEXT
);

-- Retención del crudo comprimido en disco, vinculado por hash_dedup.
CREATE TABLE IF NOT EXISTS raw_store (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash_dedup  TEXT NOT NULL,
    path        TEXT NOT NULL,   -- ruta del .gz en disco
    formato     TEXT NOT NULL,   -- json | xml | html
    tamano      INTEGER NOT NULL,
    creado_en   TEXT NOT NULL,   -- ISO 8601
    expira_en   TEXT NOT NULL    -- ISO 8601 (creado_en + retención)
);

CREATE INDEX IF NOT EXISTS idx_raw_hash   ON raw_store (hash_dedup);
CREATE INDEX IF NOT EXISTS idx_raw_expira ON raw_store (expira_en);

-- Caché de respuestas del directorio de empresas (Wikidata). Evita repetir la
-- misma consulta a la base pública; se sirve desde aquí si tiene < 7 días.
CREATE TABLE IF NOT EXISTS directorio_cache (
    clave       TEXT PRIMARY KEY,   -- qids|limite (la respuesta no depende de la vertical)
    data_json   TEXT NOT NULL,      -- respuesta cruda de Wikidata (JSON)
    creado_en   TEXT NOT NULL       -- ISO 8601
);
