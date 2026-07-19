#!/usr/bin/env bash
# Ejecuta los conectores de ingesta Capa 0. Lee credenciales de .env.
# Uso:
#   ./run.sh apify   <DATASET_ID>
#   ./run.sh youtube <VIDEO_URL> ["Organización"] [lang]
set -euo pipefail
PY="${PYTHON:-python3}"

case "${1:-}" in
  apify)
    [ -n "${2:-}" ] || { echo "uso: ./run.sh apify <DATASET_ID>"; exit 2; }
    exec "$PY" -m hd_scraper.ingesta apify --dataset "$2"
    ;;
  youtube)
    [ -n "${2:-}" ] || { echo "uso: ./run.sh youtube <VIDEO_URL> [\"Org\"] [lang]"; exit 2; }
    exec "$PY" -m hd_scraper.ingesta youtube --url "$2" --org "${3:-}" --lang "${4:-es}"
    ;;
  *)
    echo "conectores de ingesta Capa 0"
    echo "  ./run.sh apify   <DATASET_ID>"
    echo "  ./run.sh youtube <VIDEO_URL> [\"Org\"] [lang]"
    exit 2
    ;;
esac
