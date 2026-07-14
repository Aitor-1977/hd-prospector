"""Validación FINAL en PRODUCCIÓN del Motor A (datos reales de Internet).

Este script NO simula nada: ejecuta capturas reales contra la app desplegada en
Vercel (que a su vez consulta Google News real) y luego lee el corpus real para
medir y verificar. Se corre desde CUALQUIER máquina con Internet — NO desde el
entorno sandbox de Claude, cuyo proxy bloquea toda salida a la red (por eso el
asistente no puede ejecutarlo por ti; la evidencia la produces tú al correrlo).

Requisitos:
    - Python 3.9+ (solo stdlib para la red; importa hd_scraper.relevance/​signals
      del propio repo para EXPLICAR cada clasificación con el mismo código que usó
      producción).
    - Variables de entorno:
        MOTOR_A_URL     (por defecto https://hd-prospector.vercel.app)
        HD_INGEST_TOKEN (el X-Ingest-Token del despliegue; requerido para /scrape)

Uso:
    export MOTOR_A_URL="https://hd-prospector.vercel.app"
    export HD_INGEST_TOKEN="<tu-token>"
    python -m scripts.validar_produccion            # captura + valida
    python -m scripts.validar_produccion --solo-leer  # NO captura; valida lo existente

Salida:
    - Imprime el informe completo en pantalla.
    - Escribe evidencia verificable en docs/evidencia_produccion.json y .md
      (incluye la muestra de ≥50 registros con su explicación de calidad).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Mismo código objetivo que usó producción, para EXPLICAR cada clasificación.
from hd_scraper.relevance import detectar_empresa  # noqa: E402
from hd_scraper.signals import detectar_keywords, fuente_confiable  # noqa: E402

BASE = os.getenv("MOTOR_A_URL", "https://hd-prospector.vercel.app").rstrip("/")
TOKEN = os.getenv("HD_INGEST_TOKEN", "")

# Contrato esperado, exacto.
CONTRATO = "motor_a.corpus.v1"
CLAVES_CORPUS = {"empresa", "fuente", "fecha", "texto", "url",
                 "keywords", "confianza", "categoria", "tipo_evento", "hash"}

# Plan de captura real. Mezcla:
#   - Modo empresa: la MISMA empresa bajo DOS tipos distintos -> las mismas notas
#     surgen por consultas distintas => prueba de deduplicación.
#   - Modo categoría: descubrimiento amplio => ejercita el filtro de relevancia y
#     genera variedad de calidad_captura (Alta/Media/Baja).
PLAN_EMPRESAS = [
    ("Nubank", ["ronda", "lanzamiento"]),
    ("Mercado Libre", ["lanzamiento", "queja"]),
    ("Rappi", ["despido", "queja"]),
    ("Kavak", ["ronda", "despido"]),
    ("Bitso", ["ronda", "lanzamiento"]),
]
PLAN_CATEGORIAS = [
    {"categoria": "Startup", "tipo_evento": "queja", "vertical": "fintech", "region": "México"},
    {"categoria": "VC", "tipo_evento": "ronda", "vertical": "todas", "region": "México"},
    {"categoria": "Startup", "tipo_evento": "despido", "vertical": "todas", "region": "Colombia"},
]


# ── HTTP (stdlib) ─────────────────────────────────────────────────────────────

def _req(metodo: str, ruta: str, cuerpo: dict | None = None, token: bool = False,
         intentos: int = 3) -> tuple[int, dict | list | str]:
    url = f"{BASE}{ruta}"
    data = json.dumps(cuerpo).encode() if cuerpo is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-Ingest-Token"] = TOKEN
    ultimo = None
    for i in range(intentos):
        try:
            r = urllib.request.Request(url, data=data, headers=headers, method=metodo)
            with urllib.request.urlopen(r, timeout=120) as resp:
                raw = resp.read().decode()
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
            return e.code, body
        except (urllib.error.URLError, TimeoutError) as e:
            ultimo = e
            time.sleep(2 ** i)
    raise SystemExit(f"ERROR de red irrecuperable contra {url}: {ultimo}")


def _get_paginado(ruta: str, clave_items: str = "items", limite: int = 200) -> list[dict]:
    """Trae TODAS las páginas de un endpoint de lista."""
    salida: list[dict] = []
    offset = 0
    while True:
        sep = "&" if "?" in ruta else "?"
        st, d = _req("GET", f"{ruta}{sep}limite={limite}&offset={offset}")
        if st != 200 or not isinstance(d, dict):
            raise SystemExit(f"GET {ruta} devolvió {st}: {d}")
        items = d.get(clave_items, [])
        salida.extend(items)
        total = d.get("total", len(salida))
        offset += len(items)
        if not items or offset >= total:
            break
    return salida


# ── Explicación objetiva de la calidad (mismo criterio que producción) ────────

def explicar_calidad(ev: dict) -> tuple[str, str]:
    """Recalcula los criterios objetivos y explica la etiqueta calidad_captura."""
    titulo = ev.get("cita_textual") or ev.get("texto") or ""
    empresa_ok = bool(detectar_empresa(titulo))
    evento_ok = bool(detectar_keywords(titulo))
    fuente_ok = fuente_confiable(ev.get("nombre_medio") or ev.get("fuente") or "")
    n = int(empresa_ok) + int(evento_ok) + int(fuente_ok)
    esperada = "Alta" if n == 3 else "Media" if n == 2 else "Baja"
    partes = [
        f"empresa={'sí' if empresa_ok else 'no'}",
        f"evento={'sí' if evento_ok else 'no'}",
        f"fuente_confiable={'sí' if fuente_ok else 'no'}",
    ]
    razon = f"{n}/3 criterios ({', '.join(partes)}) -> {esperada}"
    return esperada, razon


# ── Validación ────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-leer", action="store_true",
                    help="No dispara capturas; valida el corpus ya existente.")
    ap.add_argument("--muestra", type=int, default=50)
    args = ap.parse_args()

    print("=" * 70)
    print("VALIDACIÓN FINAL EN PRODUCCIÓN — Motor A (datos reales)")
    print(f"Base: {BASE}")
    print("=" * 70)

    # 0) Salud + dialecto de base (debe ser postgres en producción).
    st, salud = _req("GET", "/health")
    print(f"[health] status={st} -> {salud}")
    if st != 200:
        raise SystemExit("La app no responde /health; abortando.")

    capturas: list[dict] = []

    # 1) Capturas reales.
    if not args.solo_leer:
        if not TOKEN:
            raise SystemExit("Falta HD_INGEST_TOKEN para ejecutar /scrape.")
        print("\n[captura] Ejecutando scrapes reales contra Google News…")
        for empresa, tipos in PLAN_EMPRESAS:
            for tipo in tipos:
                st, d = _req("POST", "/scrape", token=True,
                             cuerpo={"empresa": empresa, "tipo_evento": tipo,
                                     "connectors": ["google_news"], "region": "LATAM"})
                print(f"  empresa={empresa!r:16} tipo={tipo:12} -> {st} "
                      f"{_resumen_scrape(d)}")
                if st == 200:
                    capturas.append({"consulta": f"{empresa}/{tipo}", "resp": d})
        for pl in PLAN_CATEGORIAS:
            st, d = _req("POST", "/scrape", token=True, cuerpo=pl)
            print(f"  categoria={pl['categoria']:10} tipo={pl['tipo_evento']:8} "
                  f"vert={pl['vertical']:8} -> {st} {_resumen_scrape(d)}")
            if st == 200:
                capturas.append({"consulta": json.dumps(pl), "resp": d})
    else:
        print("\n[captura] --solo-leer: se valida el corpus existente.")

    # 2) Leer corpus y evidencias reales (todas las páginas).
    print("\n[lectura] Descargando corpus y evidencias reales…")
    st, corpus_head = _req("GET", "/corpus?limite=1")
    contrato = corpus_head.get("contrato") if isinstance(corpus_head, dict) else None
    corpus = _get_paginado("/corpus")
    evidencias = _get_paginado("/evidencias")
    st, stats = _req("GET", "/stats")
    print(f"  corpus={len(corpus)}  evidencias(consumibles)={len(evidencias)}  "
          f"contrato={contrato!r}")

    # 3) Verificación de contrato (exacto).
    print("\n[contrato] Verificando motor_a.corpus.v1 en TODOS los items…")
    fallas_contrato = []
    for it in corpus:
        if set(it.keys()) != CLAVES_CORPUS:
            fallas_contrato.append({"hash": it.get("hash"),
                                    "claves": sorted(it.keys())})
    contrato_ok = (contrato == CONTRATO) and not fallas_contrato
    print(f"  contrato_tag={contrato!r}  items_ok={len(corpus) - len(fallas_contrato)}/"
          f"{len(corpus)}  -> {'OK' if contrato_ok else 'FALLA'}")
    if fallas_contrato[:3]:
        print(f"  primeras fallas: {fallas_contrato[:3]}")

    # 4) Deduplicación: no debe haber URL ni hash de contenido repetidos.
    urls = [it["url"] for it in corpus]
    hashes = [it["hash"] for it in corpus]
    dup_urls = [u for u, c in Counter(urls).items() if c > 1]
    dup_hash = [h for h, c in Counter(hashes).items() if c > 1]
    # Duplicados colapsados en la escritura (evidencia por consulta):
    dups_por_consulta = sum(
        r.get("duplicados", 0)
        for cap in capturas for r in cap["resp"].get("resultados", []))
    print("\n[dedup] Unicidad en el corpus:")
    print(f"  urls únicas={len(set(urls))}/{len(urls)}  (repetidas={len(dup_urls)})")
    print(f"  hash únicos={len(set(hashes))}/{len(hashes)}  (repetidos={len(dup_hash)})")
    print(f"  duplicados colapsados al escribir (suma de consultas)={dups_por_consulta}")

    # 5) Métricas.
    empresas = {(ev.get("empresa_mencionada") or "").strip()
                for ev in evidencias if (ev.get("empresa_mencionada") or "").strip()}
    empresas_detectadas = sum(
        1 for ev in evidencias if detectar_empresa(ev.get("cita_textual") or ""))
    dist = Counter((ev.get("calidad_captura") or "sin_calidad") for ev in evidencias)
    total_rechazos = stats.get("rechazos", 0) if isinstance(stats, dict) else 0
    escritos = sum(r.get("escritos", 0)
                   for cap in capturas for r in cap["resp"].get("resultados", []))
    filtrados = sum(r.get("filtrados", 0)
                    for cap in capturas for r in cap["resp"].get("resultados", []))
    utiles = len(evidencias)  # /evidencias solo devuelve estado=ok (consumibles)
    descartados = dups_por_consulta + filtrados
    base_util = utiles + descartados
    pct_utiles = (utiles / base_util * 100) if base_util else 0.0

    print("\n[métricas] (de esta corrida y del corpus real)")
    print(f"  artículos capturados (escritos en esta corrida) : {escritos}")
    print(f"  evidencia consumible total en corpus            : {utiles}")
    print(f"  duplicados eliminados (esta corrida)            : {dups_por_consulta}")
    print(f"  descartados por relevancia (esta corrida)       : {filtrados}")
    print(f"  rechazos acumulados (stats)                     : {total_rechazos}")
    print(f"  empresas distintas en corpus                    : {len(empresas)}")
    print(f"  evidencias con empresa detectable               : {empresas_detectadas}/{utiles}")
    print(f"  distribución calidad_captura                    : {dict(dist)}")
    print(f"  % artículos útiles (útiles/(útiles+descartados)): {pct_utiles:.1f}%")
    if isinstance(stats, dict):
        print(f"  stats.rechazos_por_motivo                       : "
              f"{stats.get('rechazos_por_motivo')}")
        print(f"  stats.calidad_captura                           : "
              f"{stats.get('calidad_captura')}")

    # 6) Muestra aleatoria de ≥N registros con explicación de calidad.
    n = min(args.muestra, len(evidencias))
    muestra = random.sample(evidencias, n) if n else []
    print(f"\n[muestra] {n} registros reales con explicación objetiva de calidad:")
    filas_muestra = []
    coincidencias = 0
    for i, ev in enumerate(muestra, 1):
        etiqueta_prod = ev.get("calidad_captura") or "sin_calidad"
        esperada, razon = explicar_calidad(ev)
        coincide = (etiqueta_prod == esperada)
        coincidencias += int(coincide)
        titulo = (ev.get("cita_textual") or "")[:70]
        filas_muestra.append({
            "empresa": ev.get("empresa_mencionada"),
            "medio": ev.get("nombre_medio"),
            "titulo": ev.get("cita_textual"),
            "url": ev.get("url_fuente"),
            "calidad_produccion": etiqueta_prod,
            "calidad_recalculada": esperada,
            "coincide": coincide,
            "razon": razon,
        })
        if i <= 15:  # imprime las primeras 15; el resto va al JSON/MD
            print(f"  {i:2}. [{etiqueta_prod:5}] {titulo!r}")
            print(f"      {razon} | fuente={ev.get('nombre_medio')!r}")
    if muestra:
        print(f"  coincidencia etiqueta producción vs recálculo: "
              f"{coincidencias}/{n}")

    # 7) Volcado de evidencia verificable.
    ts = datetime.now(timezone.utc).isoformat()
    evidencia = {
        "generado_en": ts, "base": BASE, "contrato": contrato,
        "contrato_ok": contrato_ok, "salud": salud, "stats": stats,
        "capturas": capturas,
        "metricas": {
            "capturados_esta_corrida": escritos,
            "consumibles_en_corpus": utiles,
            "duplicados_eliminados": dups_por_consulta,
            "filtrados_relevancia": filtrados,
            "rechazos_acumulados": total_rechazos,
            "empresas_distintas": len(empresas),
            "evidencias_con_empresa": empresas_detectadas,
            "distribucion_calidad": dict(dist),
            "pct_utiles": round(pct_utiles, 1),
            "corpus_urls_unicas": [len(set(urls)), len(urls)],
            "corpus_hash_unicos": [len(set(hashes)), len(hashes)],
        },
        "muestra_calidad": filas_muestra,
    }
    out_json = ROOT / "docs" / "evidencia_produccion.json"
    out_json.write_text(json.dumps(evidencia, ensure_ascii=False, indent=2))
    _escribir_md(ROOT / "docs" / "evidencia_produccion.md", evidencia)
    print(f"\n[evidencia] escrita en:\n  {out_json}\n  {out_json.with_suffix('.md')}")

    # 8) Veredicto.
    ok = contrato_ok and not dup_urls and not dup_hash and st == 200
    print("\n" + "=" * 70)
    print(f"VEREDICTO: {'✅ VALIDACIÓN OK' if ok else '❌ REVISAR'} "
          f"(contrato={'ok' if contrato_ok else 'falla'}, "
          f"dedup={'ok' if not dup_urls and not dup_hash else 'falla'})")
    print("=" * 70)
    sys.exit(0 if ok else 1)


def _resumen_scrape(d) -> str:
    if not isinstance(d, dict):
        return str(d)[:120]
    rs = d.get("resultados", [])
    esc = sum(r.get("escritos", 0) for r in rs)
    dup = sum(r.get("duplicados", 0) for r in rs)
    fil = sum(r.get("filtrados", 0) for r in rs)
    vis = sum(r.get("vistos", 0) for r in rs)
    return f"vistos={vis} escritos={esc} duplicados={dup} filtrados={fil}"


def _escribir_md(path: Path, ev: dict) -> None:
    m = ev["metricas"]
    lineas = [
        "# Evidencia de validación en producción — Motor A",
        "",
        f"- Generado: `{ev['generado_en']}`",
        f"- Base (producción): `{ev['base']}`",
        f"- Contrato: `{ev['contrato']}` — {'✅ OK' if ev['contrato_ok'] else '❌ FALLA'}",
        f"- Salud: `{ev['salud']}`",
        "",
        "## Métricas (datos reales)",
        "",
        "| Métrica | Valor |",
        "|---|---:|",
        f"| Artículos capturados (esta corrida) | {m['capturados_esta_corrida']} |",
        f"| Evidencia consumible en corpus | {m['consumibles_en_corpus']} |",
        f"| Duplicados eliminados | {m['duplicados_eliminados']} |",
        f"| Descartados por relevancia | {m['filtrados_relevancia']} |",
        f"| Rechazos acumulados | {m['rechazos_acumulados']} |",
        f"| Empresas distintas | {m['empresas_distintas']} |",
        f"| Evidencias con empresa detectable | {m['evidencias_con_empresa']} |",
        f"| % artículos útiles | {m['pct_utiles']}% |",
        f"| URLs únicas / total (corpus) | {m['corpus_urls_unicas'][0]}/{m['corpus_urls_unicas'][1]} |",
        f"| Hash únicos / total (corpus) | {m['corpus_hash_unicos'][0]}/{m['corpus_hash_unicos'][1]} |",
        f"| Distribución calidad | {m['distribucion_calidad']} |",
        "",
        "## Muestra de calidad (≥50 registros reales)",
        "",
        "| # | Calidad | Empresa | Medio | Título | Razón objetiva |",
        "|--:|:--|:--|:--|:--|:--|",
    ]
    for i, f in enumerate(ev["muestra_calidad"], 1):
        titulo = (f["titulo"] or "").replace("|", "\\|")[:80]
        lineas.append(
            f"| {i} | {f['calidad_produccion']} | {f['empresa']} | {f['medio']} "
            f"| {titulo} | {f['razon']} |")
    path.write_text("\n".join(lineas) + "\n")


if __name__ == "__main__":
    main()
