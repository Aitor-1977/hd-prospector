"""Retención del crudo (HTML/JSON/XML) comprimido en disco.

Cada registro conserva su crudo original comprimido con gzip, vinculado por
``hash_dedup``, durante ``raw_retention_days`` (90 por defecto). Esto permite
reproducir la extracción y auditar qué vio el scraper, sin inflar la base.
"""
from __future__ import annotations

import gzip
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import settings
from ..db.database import Database
from ..db.models import RawItem, ahora_iso


def _ruta_para(hash_dedup: str, formato: str) -> Path:
    # Sharding por prefijo para no saturar un solo directorio.
    sub = settings.raw_dir / hash_dedup[:2]
    sub.mkdir(parents=True, exist_ok=True)
    return sub / f"{hash_dedup}.{formato}.gz"


def guardar_crudo(db: Database, hash_dedup: str, raw: RawItem) -> str:
    """Comprime y persiste el crudo; registra su retención en `raw_store`.

    Devuelve la ruta del archivo. Idempotente por ``hash_dedup``.
    """
    destino = _ruta_para(hash_dedup, raw.formato)
    datos = raw.contenido.encode("utf-8")
    with gzip.open(destino, "wb") as fh:
        fh.write(datos)

    creado = datetime.now(timezone.utc)
    expira = creado + timedelta(days=settings.raw_retention_days)

    # Evita duplicar filas de retención para el mismo hash.
    ya = db.fetch_one("SELECT id FROM raw_store WHERE hash_dedup = ?", (hash_dedup,))
    if ya is None:
        db.execute(
            """INSERT INTO raw_store (hash_dedup, path, formato, tamano, creado_en, expira_en)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (hash_dedup, str(destino), raw.formato, destino.stat().st_size,
             creado.isoformat(), expira.isoformat()),
        )
    return str(destino)


def purgar_expirados(db: Database) -> int:
    """Borra crudos vencidos (> retención) del disco y de `raw_store`.

    Devuelve la cantidad de registros purgados. Pensado para correr en cada
    corrida programada.
    """
    ahora = ahora_iso()
    vencidos = db.fetch_all(
        "SELECT id, path FROM raw_store WHERE expira_en < ?", (ahora,)
    )
    borrados = 0
    for fila in vencidos:
        try:
            Path(fila["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        db.execute("DELETE FROM raw_store WHERE id = ?", (fila["id"],))
        borrados += 1
    return borrados
