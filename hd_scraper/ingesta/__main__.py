"""CLI de los conectores de ingesta Capa 0.

Ejemplos:
    python -m hd_scraper.ingesta apify --dataset <DATASET_ID>
    python -m hd_scraper.ingesta youtube --url <VIDEO_URL> --org "Acme" --lang es
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import apify, youtube

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="hd_scraper.ingesta",
        description="Conectores de ingesta (Apify / yt-dlp) → /webhook/ingesta",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("apify", help="Dataset de Apify (LinkedIn/Jobs/News)")
    a.add_argument("--dataset", required=True, help="ID del dataset de Apify")

    y = sub.add_parser("youtube", help="Transcripción de un video de YouTube")
    y.add_argument("--url", required=True, help="URL del video")
    y.add_argument("--org", default=None, help="Nombre de la organización")
    y.add_argument("--lang", default="es", help="Idioma de subtítulos (default: es)")

    args = p.parse_args(argv)
    try:
        if args.cmd == "apify":
            res = apify.correr(args.dataset)
        else:
            res = youtube.correr(args.url, args.org, lang=args.lang)
    except Exception as exc:
        logging.getLogger("hd_scraper.ingesta").error("conector falló: %s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
