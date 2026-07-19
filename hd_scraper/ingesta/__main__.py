"""CLI de los conectores de ingesta Capa 0.

Ejemplos:
    python -m hd_scraper.ingesta noticias --query "fintech México ronda"
    python -m hd_scraper.ingesta noticias --feed https://un-medio.com/rss
    python -m hd_scraper.ingesta youtube --url <VIDEO_URL> --org "Acme" --lang es
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import noticias, youtube

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="hd_scraper.ingesta",
        description="Conectores de ingesta (noticias RSS gratis / yt-dlp) → /webhook/ingesta",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("noticias", help="Noticias por RSS gratis (Google News u otro feed)")
    n.add_argument("--query", default=None, help="Búsqueda en Google News RSS")
    n.add_argument("--feed", default=None, help="URL de un feed RSS/Atom directo")
    n.add_argument("--limite", type=int, default=25, help="máximo de notas a enviar")

    y = sub.add_parser("youtube", help="Transcripción de un video de YouTube")
    y.add_argument("--url", required=True, help="URL del video")
    y.add_argument("--org", default=None, help="Nombre de la organización")
    y.add_argument("--lang", default="es", help="Idioma de subtítulos (default: es)")

    args = p.parse_args(argv)
    try:
        if args.cmd == "noticias":
            res = noticias.correr(query=args.query, feed_url=args.feed, limite=args.limite)
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
