"""Configuración central de hd-scraper.

Todo se resuelve por variables de entorno con defaults razonables para
desarrollo local con SQLite. El esquema es compatible con PostgreSQL para
una migración posterior sin cambiar el modelo de datos.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("HD_DATA_DIR", BASE_DIR / "data"))
RAW_DIR = Path(os.getenv("HD_RAW_DIR", DATA_DIR / "raw"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _parse_slugs(valor: str) -> dict[str, str]:
    """Parsea 'Empresa=slug,Empresa2=slug2' -> {empresa: slug} para job boards."""
    out: dict[str, str] = {}
    for par in valor.split(","):
        par = par.strip()
        if "=" in par:
            empresa, slug = par.split("=", 1)
            empresa, slug = empresa.strip(), slug.strip()
            if empresa and slug:
                out[empresa] = slug
    return out


def _resolve_database_url() -> str:
    """Resuelve la URL de la base según prioridad.

    1. HD_DATABASE_URL (override explícito).
    2. DATABASE_URL / POSTGRES_URL / POSTGRES_PRISMA_URL (los que inyecta Vercel
       al conectar Postgres). Producción usa Postgres: sin memoria temporal.
    3. Fallback SQLite (solo desarrollo local; en Vercel apunta a /tmp para no
       romper la lectura mientras no haya Postgres conectado).
    """
    for var in ("HD_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        valor = os.getenv(var)
        if valor:
            return valor
    if os.getenv("VERCEL"):
        return "sqlite:////tmp/hd_scraper.db"
    return f"sqlite:///{DATA_DIR / 'hd_scraper.db'}"


@dataclass(frozen=True)
class Settings:
    # Base de datos. postgres://... para Postgres (producción); sqlite:///ruta
    # para desarrollo/tests. El esquema tiene versión para cada motor.
    database_url: str = field(default_factory=_resolve_database_url)

    # Retención del crudo (HTML/JSON comprimido) en disco.
    raw_dir: Path = RAW_DIR
    raw_retention_days: int = _int("HD_RAW_RETENTION_DAYS", 90)

    # Scheduler: corridas programadas cada N horas.
    schedule_hours: int = _int("HD_SCHEDULE_HOURS", 12)

    # Gobernanza de rate limiting / backoff por fuente.
    request_timeout_s: float = float(os.getenv("HD_REQUEST_TIMEOUT_S", "20"))
    max_retries: int = _int("HD_MAX_RETRIES", 4)
    backoff_base_s: float = float(os.getenv("HD_BACKOFF_BASE_S", "2"))
    min_interval_s: float = float(os.getenv("HD_MIN_INTERVAL_S", "1.5"))

    # Umbral de salud: fallos consecutivos que disparan alerta.
    health_alert_threshold: int = _int("HD_HEALTH_ALERT_THRESHOLD", 2)

    # Token para la intake de prospectos (POST /prospectos y /admin). Si está
    # vacío, la escritura queda DESHABILITADA (la API sigue siendo solo lectura).
    ingest_token: str = os.getenv("HD_INGEST_TOKEN", "")

    # User-Agent identificable para las fuentes.
    user_agent: str = os.getenv(
        "HD_USER_AGENT",
        "hd-scraper/0.1 (+https://hamaca.digital; evidence-extraction)",
    )

    # Empresas seguidas por el scheduler (coma-separadas).
    tracked_empresas: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            e.strip() for e in os.getenv("HD_TRACKED_EMPRESAS", "").split(",") if e.strip()
        )
    )

    # Slugs de job boards por empresa: "Empresa=slug,Empresa2=slug2".
    tracked_slugs: dict = field(
        default_factory=lambda: _parse_slugs(os.getenv("HD_TRACKED_SLUGS", ""))
    )

    @property
    def sqlite_path(self) -> Path | None:
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url[len("sqlite:///"):])
        return None


settings = Settings()
