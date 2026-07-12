#!/usr/bin/env python3
"""Corre el pipeline una vez para una empresa (verificación de punta a punta).

Uso:
    python -m scripts.run_once "Nombre Empresa" --tipo ronda --connector google_news

Sirve para verificar el flujo completo del conector Google News RSS:
extracción -> normalización -> validación -> escritura en SQLite.
"""
from __future__ import annotations

import argparse
import logging

from hd_scraper.connectors import REGISTRY
from hd_scraper.db.database import Database
from hd_scraper.db.models import TIPOS_EVENTO, QuerySpec
from hd_scraper.pipeline import run_connector


def main() -> None:
    parser = argparse.ArgumentParser(description="Corrida única de un conector.")
    parser.add_argument("empresa", help="Nombre de la empresa a buscar")
    parser.add_argument("--tipo", default="ronda", choices=sorted(TIPOS_EVENTO),
                        help="tipo_evento declarado para la consulta")
    parser.add_argument("--connector", default="google_news", choices=sorted(REGISTRY),
                        help="conector a usar")
    parser.add_argument("--terminos", default=None, help="términos extra de búsqueda")
    parser.add_argument("--slug", default=None,
                        help="slug de empresa (requerido por job_boards)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    db = Database()
    db.init_schema()

    connector_cls = REGISTRY[args.connector]
    if connector_cls.requires_slug and not args.slug:
        parser.error(f"el conector {args.connector} requiere --slug")
    query = QuerySpec(empresa=args.empresa, tipo_evento=args.tipo,
                      terminos=args.terminos, slug=args.slug)
    with connector_cls() as connector:
        res = run_connector(db, connector, query)

    print(res.resumen())
    db.close()


if __name__ == "__main__":
    main()
