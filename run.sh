#!/usr/bin/env bash
# Ejecuta los conectores de ingesta Capa 0 (100% gratuitos). Lee .env.
# Uso:
#   ./run.sh noticias --query "fintech México ronda"
#   ./run.sh noticias --feed https://un-medio.com/rss
#   ./run.sh youtube <VIDEO_URL> ["Organización"] [lang]
set -euo pipefail
PY="${PYTHON:-python3}"

case "${1:-}" in
  noticias)
    shift
    exec "$PY" -m hd_scraper.ingesta noticias "$@"
    ;;
  youtube)
    [ -n "${2:-}" ] || { echo "uso: ./run.sh youtube <VIDEO_URL> [\"Org\"] [lang]"; exit 2; }
    exec "$PY" -m hd_scraper.ingesta youtube --url "$2" --org "${3:-}" --lang "${4:-es}"
    ;;
  *)
    echo "conectores de ingesta Capa 0 (gratuitos)"
    echo "  ./run.sh noticias --query \"fintech México ronda\""
    echo "  ./run.sh noticias --feed <URL_RSS>"
    echo "  ./run.sh youtube <VIDEO_URL> [\"Org\"] [lang]"
    exit 2
    ;;
esac
