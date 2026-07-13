-- Esquema de hd-prospector — versión PostgreSQL.
-- Espejo de schema.sql (SQLite) con el único cambio de dialecto necesario:
--   * INTEGER PRIMARY KEY AUTOINCREMENT  ->  BIGINT GENERATED ALWAYS AS IDENTITY
-- El resto (TEXT, UNIQUE, CHECK, índices, ON CONFLICT) es idéntico y válido en
-- ambos motores. Las fechas se guardan como TEXT ISO 8601 (igual que en SQLite);
-- migrar una columna a TIMESTAMPTZ más adelante no afecta al modelo.

CREATE TABLE IF NOT EXISTS evidencias (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cita_textual        TEXT NOT NULL,
    fecha_extraccion    TEXT NOT NULL,
    url_fuente          TEXT NOT NULL,
    nombre_medio        TEXT NOT NULL,
    empresa_mencionada  TEXT NOT NULL,
    tipo_evento         TEXT NOT NULL,
    origen_declaracion  TEXT NOT NULL,
    hash_dedup          TEXT NOT NULL UNIQUE,
    fecha_publicacion   TEXT,
    persona_citada      TEXT,
    cargo               TEXT,
    connector           TEXT NOT NULL,
    estado              TEXT NOT NULL DEFAULT 'ok',
    raw_hash            TEXT,
    categoria           TEXT,
    keywords            TEXT,
    confianza           DOUBLE PRECISION NOT NULL DEFAULT 0,
    creado_en           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidencias_empresa ON evidencias (empresa_mencionada);
CREATE INDEX IF NOT EXISTS idx_evidencias_tipo    ON evidencias (tipo_evento);
CREATE INDEX IF NOT EXISTS idx_evidencias_estado  ON evidencias (estado);
CREATE INDEX IF NOT EXISTS idx_evidencias_fpub    ON evidencias (fecha_publicacion);
-- Migración idempotente: añade columnas nuevas a una `evidencias` ya existente
-- (Postgres soporta ADD COLUMN IF NOT EXISTS; en una base nueva es no-op).
ALTER TABLE evidencias ADD COLUMN IF NOT EXISTS categoria TEXT;
ALTER TABLE evidencias ADD COLUMN IF NOT EXISTS keywords  TEXT;
ALTER TABLE evidencias ADD COLUMN IF NOT EXISTS confianza DOUBLE PRECISION NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_evidencias_categoria ON evidencias (categoria);

CREATE TABLE IF NOT EXISTS rechazos (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    connector     TEXT NOT NULL,
    motivo        TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    creado_en     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rechazos_motivo ON rechazos (motivo);

-- Prospectos: entidades objetivo de los CUATRO ecosistemas estratégicos.
-- `categoria` obligatoria y acotada por CHECK. Thick Data en columnas de texto.
CREATE TABLE IF NOT EXISTS prospectos (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre                TEXT NOT NULL,
    categoria             TEXT NOT NULL,
    vertical              TEXT,
    sitio_web             TEXT,
    linkedin              TEXT,
    discurso_corporativo  TEXT,
    tipo_discurso         TEXT,
    url_perfil            TEXT,
    fuente_discurso       TEXT,
    fecha_captura         TEXT,
    hash_dedup            TEXT NOT NULL UNIQUE,
    creado_en             TEXT NOT NULL,
    actualizado_en        TEXT NOT NULL,
    CHECK (categoria IN ('VC', 'Startup', 'Incubadora', 'Corporativo'))
);

-- Migración idempotente: columnas de perfil en una `prospectos` ya existente.
ALTER TABLE prospectos ADD COLUMN IF NOT EXISTS vertical  TEXT;
ALTER TABLE prospectos ADD COLUMN IF NOT EXISTS sitio_web TEXT;
ALTER TABLE prospectos ADD COLUMN IF NOT EXISTS linkedin  TEXT;

CREATE INDEX IF NOT EXISTS idx_prospectos_categoria ON prospectos (categoria);
CREATE INDEX IF NOT EXISTS idx_prospectos_nombre    ON prospectos (nombre);

CREATE TABLE IF NOT EXISTS jobs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    connector     TEXT NOT NULL,
    query_json    TEXT NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'pending',
    intentos      INTEGER NOT NULL DEFAULT 0,
    resultado     TEXT,
    creado_en     TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_estado ON jobs (estado);

CREATE TABLE IF NOT EXISTS salud_fuentes (
    fuente               TEXT PRIMARY KEY,
    ultima_corrida       TEXT,
    ultimo_estado        TEXT,
    fallos_consecutivos  INTEGER NOT NULL DEFAULT 0,
    alerta               INTEGER NOT NULL DEFAULT 0,
    detalle              TEXT
);

CREATE TABLE IF NOT EXISTS raw_store (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hash_dedup  TEXT NOT NULL,
    path        TEXT NOT NULL,
    formato     TEXT NOT NULL,
    tamano      INTEGER NOT NULL,
    creado_en   TEXT NOT NULL,
    expira_en   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_hash   ON raw_store (hash_dedup);
CREATE INDEX IF NOT EXISTS idx_raw_expira ON raw_store (expira_en);
