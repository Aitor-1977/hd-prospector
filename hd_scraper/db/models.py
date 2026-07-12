"""Modelo de datos y contrato de evidencia.

El contrato de datos es la pieza más importante de hd-scraper: define qué
puede entrar a la tabla `evidencias`. El validador (ver
``hd_scraper.validation.validator``) es el único guardián de este contrato.

Recordatorio de invariante: hd-scraper NO interpreta. Los campos que podrían
parecer "clasificación" (``tipo_evento``, ``origen_declaracion``) NO se
infieren leyendo el contenido: se derivan de la ESTRUCTURA de la consulta o
de la fuente (ver docstring de cada conector). Radar es quien interpreta.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

# --- Vocabularios literales del contrato ---------------------------------

# tipo_evento: valores literales permitidos. Cualquier otro valor es rechazado.
TIPOS_EVENTO: frozenset[str] = frozenset(
    {"ronda", "contratacion", "despido", "lanzamiento", "queja", "cambio_sitio"}
)

# origen_declaracion: quién emite la señal.
ORIGENES_DECLARACION: frozenset[str] = frozenset(
    {"operador", "inversor", "prensa", "usuario"}
)

# Estados de un registro dentro de evidencias.
ESTADO_OK = "ok"              # completo y fechado: consumible por la API.
ESTADO_NO_FECHADO = "no_fechado"  # completo pero sin fecha_publicacion: NO consumible.


def ahora_iso() -> str:
    """Timestamp actual en ISO 8601 con zona UTC."""
    return datetime.now(timezone.utc).isoformat()


def normalizar_url(url: str) -> str:
    """URL normalizada para deduplicación.

    Baja el host a minúsculas, descarta query string, fragmento y el slash
    final del path. Es determinista: misma nota → misma URL normalizada.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, "", ""))


def normalizar_empresa(empresa: str) -> str:
    """Nombre de empresa normalizado para deduplicación (minúsculas, sin bordes)."""
    return " ".join((empresa or "").lower().split())


def calcular_hash_dedup(empresa: str, url_fuente: str) -> str:
    """hash_dedup = sha256(empresa_normalizada + url_normalizada). Único por registro."""
    base = f"{normalizar_empresa(empresa)}|{normalizar_url(url_fuente)}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


@dataclass
class QuerySpec:
    """Especificación estructural de una consulta.

    Encapsula la INTENCIÓN declarada por el operador. El ``tipo_evento`` viaja
    aquí porque lo decide quien lanza la corrida (estructura), no el conector
    leyendo el artículo (interpretación).
    """
    empresa: str
    tipo_evento: str            # uno de TIPOS_EVENTO
    terminos: Optional[str] = None   # términos extra opcionales para la búsqueda
    slug: Optional[str] = None       # slug de empresa (para job boards)

    def to_dict(self) -> dict:
        return {
            "empresa": self.empresa,
            "tipo_evento": self.tipo_evento,
            "terminos": self.terminos,
            "slug": self.slug,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QuerySpec":
        return cls(
            empresa=d["empresa"],
            tipo_evento=d["tipo_evento"],
            terminos=d.get("terminos"),
            slug=d.get("slug"),
        )


@dataclass
class RawItem:
    """Payload crudo devuelto por ``search``/``fetch`` de un conector.

    ``contenido`` es el crudo textual (RSS entry serializado, JSON, HTML) que
    se retiene comprimido en disco. ``meta`` lleva campos ya extraídos de forma
    estructural para que ``normalize`` no tenga que re-parsear.
    """
    url: str
    contenido: str
    formato: str = "json"            # json | xml | html
    meta: dict = field(default_factory=dict)


@dataclass
class EvidenceRecord:
    """Un registro candidato a la tabla `evidencias`.

    Los campos obligatorios del contrato deben venir poblados para pasar el
    validador. ``persona_citada`` y ``cargo`` son opcionales. ``fecha_publicacion``
    puede faltar: en ese caso el registro se marca ``no_fechado`` y no es
    consumible por la API (pero NO se rechaza).
    """
    # --- Contrato obligatorio ---
    cita_textual: str
    fecha_extraccion: str
    url_fuente: str
    nombre_medio: str
    empresa_mencionada: str
    tipo_evento: str
    origen_declaracion: str
    hash_dedup: str

    # --- Opcionales del contrato ---
    fecha_publicacion: Optional[str] = None
    persona_citada: Optional[str] = None
    cargo: Optional[str] = None

    # --- Metadatos internos (no forman parte del contrato público) ---
    connector: str = ""
    estado: str = ESTADO_OK
    raw_hash: Optional[str] = None   # enlace al crudo retenido en disco
    creado_en: str = field(default_factory=ahora_iso)

    def campos_contrato(self) -> dict:
        """Vista de solo los campos del contrato (para la API de lectura)."""
        return {
            "cita_textual": self.cita_textual,
            "fecha_publicacion": self.fecha_publicacion,
            "fecha_extraccion": self.fecha_extraccion,
            "url_fuente": self.url_fuente,
            "nombre_medio": self.nombre_medio,
            "empresa_mencionada": self.empresa_mencionada,
            "persona_citada": self.persona_citada,
            "cargo": self.cargo,
            "tipo_evento": self.tipo_evento,
            "origen_declaracion": self.origen_declaracion,
            "hash_dedup": self.hash_dedup,
        }
