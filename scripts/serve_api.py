#!/usr/bin/env python3
"""Levanta la API de solo lectura y el scheduler cada 12h.

Uso:
    python -m scripts.serve_api        # API + scheduler
    uvicorn hd_scraper.api.app:app     # solo API
"""
from __future__ import annotations

import logging

import uvicorn

from hd_scraper.config import settings
from hd_scraper.db.database import get_db
from hd_scraper.scheduler import build_scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    db = get_db()  # crea esquema si falta
    scheduler = build_scheduler(db)
    scheduler.start()
    logging.getLogger("hd_scraper").info(
        "scheduler activo: corrida cada %d h", settings.schedule_hours
    )
    try:
        uvicorn.run("hd_scraper.api.app:app", host="0.0.0.0", port=8000, log_level="info")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
