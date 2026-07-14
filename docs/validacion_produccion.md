# Validación final en producción (datos reales)

Esta validación se ejecuta contra la app **desplegada en Vercel**, que consulta
**Google News real**. No usa simulaciones ni datos controlados. El script
`scripts/validar_produccion.py` dispara capturas reales, lee el corpus real y
mide/verifica todo lo pedido, dejando evidencia verificable en
`docs/evidencia_produccion.json` y `docs/evidencia_produccion.md`.

> **Por qué no lo corre el asistente:** el entorno sandbox del asistente tiene la
> salida de red bloqueada por política del proxy (todo `CONNECT` externo responde
> `403`, incluidos `hd-prospector.vercel.app` y `news.google.com`). Por eso la
> evidencia de producción la generas tú al correr el script desde una máquina con
> Internet. El asistente no inventa resultados.

## Requisitos

- Producción al día: confirma en Vercel que el último deploy (`main`) está
  **Ready** e incluye la Captura Inteligente y la observabilidad de validación.
- Python 3.9+ y el repo clonado.
- El **X-Ingest-Token** del despliegue (el mismo valor de la env var de ingesta
  en Vercel), necesario para `POST /scrape`.

## Ejecución

```bash
export MOTOR_A_URL="https://hd-prospector.vercel.app"
export HD_INGEST_TOKEN="<tu-token-de-ingesta>"

# Captura real + validación completa (recomendado):
python -m scripts.validar_produccion

# Solo validar el corpus ya existente (sin capturar):
python -m scripts.validar_produccion --solo-leer
```

## Qué valida y de dónde sale la evidencia

| Requisito | Cómo se obtiene (todo real) |
|---|---|
| Ejecutar captura real | `POST /scrape` (empresa y categoría) → Google News real |
| Artículos capturados / descartados / duplicados | Suma de `escritos` / `filtrados` / `duplicados` de las respuestas reales de `/scrape` |
| Empresas identificadas | `empresa_mencionada` distintas + detección objetiva sobre el título |
| Distribución `calidad_captura` | Conteo Alta/Media/Baja de `/evidencias` y `/stats.calidad_captura` |
| % artículos útiles | `útiles / (útiles + descartados)` |
| Muestra ≥50 con explicación | `random.sample` de `/evidencias`; cada registro explica su etiqueta recalculando los 3 criterios objetivos con el MISMO código de producción (`hd_scraper.relevance`/`signals`) |
| Deduplicación | Unicidad de `url` y `hash` en todo `/corpus` + `duplicados>0` en la 2ª consulta que trae el mismo artículo |
| Contrato `motor_a.corpus.v1` | Se verifica el tag `contrato` y que **cada** item de `/corpus` tenga exactamente las 10 claves del contrato |

## Observabilidad añadida para esta validación (no es funcionalidad nueva)

Solo se exponen contadores **ya calculados**, para que la evidencia salga de la
propia app:

- `POST /scrape` → cada resultado ahora incluye `filtrados` (descartes del filtro
  de relevancia), junto a `duplicados`, `rechazados`, `escritos`, `vistos`.
- `GET /stats` → añade `rechazos_por_motivo` (desglose de descartes: dedup /
  contrato / `relevancia:*`) y `calidad_captura` (distribución Alta/Media/Baja).

## Smoke manual (opcional, con curl)

```bash
curl -s "$MOTOR_A_URL/health"
curl -s -X POST "$MOTOR_A_URL/scrape" \
  -H "Content-Type: application/json" -H "X-Ingest-Token: $HD_INGEST_TOKEN" \
  -d '{"empresa":"Nubank","tipo_evento":"ronda","connectors":["google_news"],"region":"LATAM"}'
curl -s "$MOTOR_A_URL/corpus?limite=5"
curl -s "$MOTOR_A_URL/stats"
```
