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

-- Señales de la Capa 0: matches deterministas del motor de reglas sobre texto
-- (titulares, descripciones o transcripciones de video). id determinista => dedup.
CREATE TABLE IF NOT EXISTS senales_capa0 (
    id                TEXT PRIMARY KEY,   -- sha1(url|tipo|kw)
    url               TEXT,
    timestamp_video   TEXT,
    fragmento_literal TEXT,
    tipo_senal        TEXT,               -- Operativa | Discursiva | Rescate
    score_deuda       REAL,
    motivo_match      TEXT,
    org_id            TEXT,
    org_nombre        TEXT,
    score_total       REAL,
    nivel_alerta      TEXT,               -- Normal | Crítica
    creado_en         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capa0_org    ON senales_capa0 (org_nombre);
CREATE INDEX IF NOT EXISTS idx_capa0_alerta ON senales_capa0 (nivel_alerta);

-- Investigaciones (informes) guardadas: snapshot con su Markdown y resumen.
CREATE TABLE IF NOT EXISTS informes_guardados (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo        TEXT,
    categorias    TEXT,          -- ecosistemas incluidos (coma-separados)
    total         INTEGER,
    resumen_json  TEXT,          -- {A,B,C}
    markdown      TEXT,
    creado_en     TEXT NOT NULL
);

-- =========================================================================
-- Capa 6 — Motor de Drift Narrativo
-- Snapshots versionados del discurso público de cada organización.
-- Cada snapshot captura el texto limpio de una página pública en un momento
-- dado. La comparación entre snapshots consecutivos genera evidencias
-- narrativas (cambios observados, no interpretados).
-- =========================================================================

CREATE TABLE IF NOT EXISTS drift_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    org_nombre          TEXT NOT NULL,
    tipo_pagina         TEXT NOT NULL,       -- homepage|about|mision|propuesta_valor|manifiesto
    url                 TEXT NOT NULL,
    texto               TEXT NOT NULL DEFAULT '',
    hash_contenido      TEXT NOT NULL DEFAULT '',
    estado_observable   TEXT NOT NULL DEFAULT 'ok',  -- ok|no_observable|spa|error_http|timeout|contenido_vacio|bloqueado|robots
    capturado_en        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drift_snap_org   ON drift_snapshots (org_nombre);
CREATE INDEX IF NOT EXISTS idx_drift_snap_tipo  ON drift_snapshots (tipo_pagina);
CREATE INDEX IF NOT EXISTS idx_drift_snap_hash  ON drift_snapshots (hash_contenido);

-- Evidencias Narrativas: cambios detectados entre snapshots consecutivos.
-- Cada evidencia es un HECHO observado (no una interpretación). Los tipos
-- están cerrados: posicionamiento|audiencia|lenguaje|identidad|concepto_nuevo|
-- concepto_eliminado|contradiccion|cambio_ontologico.
CREATE TABLE IF NOT EXISTS drift_evidencias (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    org_nombre            TEXT NOT NULL,
    tipo_cambio           TEXT NOT NULL,
    tipo_pagina           TEXT NOT NULL,
    fragmento_antes       TEXT,
    fragmento_despues     TEXT,
    descripcion           TEXT NOT NULL,
    snapshot_anterior_id  INTEGER REFERENCES drift_snapshots(id),
    snapshot_actual_id    INTEGER REFERENCES drift_snapshots(id),
    hash_dedup            TEXT NOT NULL UNIQUE,
    detectado_en          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drift_ev_org  ON drift_evidencias (org_nombre);
CREATE INDEX IF NOT EXISTS idx_drift_ev_tipo ON drift_evidencias (tipo_cambio);

-- =========================================================================
-- Capa 7 — Motor Onlife
-- Señales conductuales observadas en espacios digitales donde la vida
-- organizacional realmente ocurre (repos, foros, changelogs, comunidades).
-- Cada señal es un HECHO verificable con URL fuente. No interpreta.
-- =========================================================================

CREATE TABLE IF NOT EXISTS onlife_signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    org_nombre          TEXT NOT NULL,
    fuente              TEXT NOT NULL,       -- github|hackernews|blog_changelog
    tipo_senal          TEXT NOT NULL,       -- actividad_tech|lanzamiento|comunidad|contratacion|presencia
    dato_json           TEXT NOT NULL,       -- observación estructurada (JSON)
    url                 TEXT,
    descripcion         TEXT NOT NULL,       -- descripción legible del hecho observado
    fecha_observacion   TEXT NOT NULL,       -- ISO 8601
    hash_dedup          TEXT NOT NULL UNIQUE,
    creado_en           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_onlife_org    ON onlife_signals (org_nombre);
CREATE INDEX IF NOT EXISTS idx_onlife_fuente ON onlife_signals (fuente);
CREATE INDEX IF NOT EXISTS idx_onlife_tipo   ON onlife_signals (tipo_senal);
