"""Dictamen Antropológico — narrativa determinista de inteligencia.

Transforma expedientes crudos en un informe ejecutivo con:
  - Resumen ejecutivo (cifras, cobertura)
  - Hallazgos ecosistémicos (patrones agregados)
  - Hipótesis de Deuda Cultural dominante
  - Ranking TOP 10 con explicación
  - Alertas (cambios recientes detectados)

100% determinista: mismos datos → mismo dictamen. Sin IA ni red.
"""
from __future__ import annotations

from collections import Counter

from .analisis import (
    ANGULO_POR_DEUDA,
    DEUDA_POR_SENAL,
    SENALES_CAMBIO,
    SENALES_DOLOR,
)


def _plural(n: int, singular: str, plural: str = "") -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def generar_dictamen(
    expedientes: list[dict],
    query: str = "",
    region: str = "",
    vertical: str = "",
    tiempo_s: float = 0,
    total_escritos: int = 0,
    total_vistos: int = 0,
    noticias_senales: int = 0,
) -> dict:
    """Genera un Dictamen Antropológico completo a partir de expedientes."""
    n_orgs = len(expedientes)
    n_evidencias = sum(e.get("total_evidencias", 0) for e in expedientes)
    n_a = sum(1 for e in expedientes if e.get("scoring") == "A")
    n_b = sum(1 for e in expedientes if e.get("scoring") == "B")
    n_c = sum(1 for e in expedientes if e.get("scoring") == "C")

    resumen_ejecutivo = {
        "organizaciones_analizadas": n_orgs,
        "evidencias_procesadas": n_evidencias,
        "titulares_revisados": total_vistos,
        "senales_rss": noticias_senales,
        "organizaciones_prioritarias": n_a,
        "scoring": {"A": n_a, "B": n_b, "C": n_c},
        "query": query,
        "region": region,
        "vertical": vertical,
        "tiempo_s": tiempo_s,
    }

    hallazgos = _generar_hallazgos(expedientes)
    hipotesis = _generar_hipotesis(expedientes)
    ranking = generar_ranking(expedientes)
    alertas = _generar_alertas(expedientes)

    return {
        "resumen_ejecutivo": resumen_ejecutivo,
        "hallazgos": hallazgos,
        "hipotesis": hipotesis,
        "ranking": ranking,
        "alertas": alertas,
    }


def _generar_hallazgos(expedientes: list[dict]) -> list[dict]:
    """Detecta patrones ecosistémicos a partir del agregado de señales."""
    if not expedientes:
        return [{"hallazgo": "Sin evidencia suficiente para generar hallazgos.",
                 "tipo": "sin_datos"}]

    all_kws: list[str] = []
    all_deudas: list[str] = []
    all_verticales: list[str] = []
    all_categorias: list[str] = []
    dolor_count = 0
    cambio_count = 0

    for e in expedientes:
        kws = e.get("keywords", [])
        all_kws.extend(kws)
        if e.get("tipo_deuda"):
            all_deudas.append(e["tipo_deuda"])
        if e.get("vertical"):
            all_verticales.append(e["vertical"])
        if e.get("categoria"):
            all_categorias.append(e["categoria"])
        kw_set = set(kws)
        if kw_set & SENALES_DOLOR:
            dolor_count += 1
        if kw_set & SENALES_CAMBIO:
            cambio_count += 1

    kw_counter = Counter(all_kws)
    deuda_counter = Counter(all_deudas)
    vert_counter = Counter(all_verticales)

    hallazgos: list[dict] = []

    top_kws = kw_counter.most_common(5)
    if top_kws:
        nombres = [f"{k} ({v})" for k, v in top_kws]
        hallazgos.append({
            "hallazgo": f"Señales más frecuentes: {', '.join(nombres)}.",
            "tipo": "senales_frecuentes",
            "detalle": dict(top_kws),
        })

    n_orgs = len(expedientes)
    if dolor_count > 0:
        pct = round(dolor_count / n_orgs * 100)
        hallazgos.append({
            "hallazgo": f"{_plural(dolor_count, 'organización', 'organizaciones')} "
                        f"({pct}%) muestra señales de dolor explícito "
                        f"(fricción, recortes, cierre o regulación).",
            "tipo": "dolor_ecosistema",
            "detalle": {"total": dolor_count, "pct": pct},
        })

    if cambio_count > 0:
        pct = round(cambio_count / n_orgs * 100)
        hallazgos.append({
            "hallazgo": f"{_plural(cambio_count, 'organización', 'organizaciones')} "
                        f"({pct}%) presenta señales de crecimiento o cambio "
                        f"(rondas, expansión, contratación).",
            "tipo": "cambio_ecosistema",
            "detalle": {"total": cambio_count, "pct": pct},
        })

    if deuda_counter:
        top_deuda = deuda_counter.most_common(3)
        for deuda, count in top_deuda:
            pct = round(count / n_orgs * 100)
            hallazgos.append({
                "hallazgo": f"{deuda}: {_plural(count, 'organización', 'organizaciones')} ({pct}%).",
                "tipo": "deuda_dominante",
                "detalle": {"deuda": deuda, "total": count, "pct": pct},
            })

    if vert_counter:
        top_vert = vert_counter.most_common(3)
        if top_vert[0][1] > 1:
            nombres_v = [f"{v} ({c})" for v, c in top_vert if c > 1]
            if nombres_v:
                hallazgos.append({
                    "hallazgo": f"Verticales con mayor concentración: {', '.join(nombres_v)}.",
                    "tipo": "concentracion_vertical",
                    "detalle": dict(top_vert),
                })

    return hallazgos


def _generar_hipotesis(expedientes: list[dict]) -> dict:
    """Genera la hipótesis central del dictamen basada en la deuda dominante."""
    if not expedientes:
        return {
            "texto": "Sin evidencia suficiente para formular una hipótesis.",
            "confianza": "baja",
            "deuda_dominante": "",
            "angulo": "",
        }

    deudas = [e["tipo_deuda"] for e in expedientes if e.get("tipo_deuda")]
    if not deudas:
        return {
            "texto": "Las organizaciones observadas no muestran patrones claros de "
                     "Deuda Cultural. Se recomienda ampliar la captura de evidencia.",
            "confianza": "baja",
            "deuda_dominante": "",
            "angulo": "",
        }

    deuda_counter = Counter(deudas)
    dominante, count = deuda_counter.most_common(1)[0]
    n_orgs = len(expedientes)
    pct = round(count / n_orgs * 100)

    all_kws: list[str] = []
    for e in expedientes:
        all_kws.extend(e.get("keywords", []))
    kw_counter = Counter(all_kws)
    top_kw = kw_counter.most_common(2)
    contexto_kw = " y ".join(k for k, _ in top_kw) if top_kw else "señales mixtas"

    if pct >= 40:
        confianza = "alta"
        texto = (
            f"La mayor parte del dolor observado apunta a una {dominante} "
            f"relacionada con {contexto_kw}. "
            f"{count} de {n_orgs} organizaciones ({pct}%) presentan este patrón, "
            f"lo que sugiere una tendencia ecosistémica, no casos aislados."
        )
    elif pct >= 20:
        confianza = "media"
        texto = (
            f"Se observa un patrón emergente de {dominante} "
            f"asociado a {contexto_kw}. "
            f"{count} organizaciones ({pct}%) lo presentan. "
            f"Se recomienda vigilancia activa de este grupo."
        )
    else:
        confianza = "baja"
        texto = (
            f"La deuda más frecuente es {dominante} ({count} org., {pct}%), "
            f"pero el patrón es disperso. "
            f"No hay evidencia suficiente para una hipótesis ecosistémica fuerte."
        )

    angulo = ANGULO_POR_DEUDA.get(dominante, "")

    return {
        "texto": texto,
        "confianza": confianza,
        "deuda_dominante": dominante,
        "angulo": angulo,
        "distribucion": dict(deuda_counter.most_common(5)),
    }


def generar_ranking(expedientes: list[dict], limite: int = 10) -> list[dict]:
    """TOP N organizaciones con explicación de por qué están rankeadas."""
    scored: list[tuple[float, dict]] = []
    for e in expedientes:
        score = _score_compuesto(e)
        scored.append((score, e))

    scored.sort(key=lambda x: -x[0])

    ranking = []
    for rank, (score, e) in enumerate(scored[:limite], 1):
        motivos = _explicar_ranking(e)
        ranking.append({
            "posicion": rank,
            "nombre": e["nombre"],
            "scoring": e["scoring"],
            "score_icp": e["score_icp"],
            "score_compuesto": round(score, 1),
            "tipo_deuda": e.get("tipo_deuda", ""),
            "intensidad": e.get("intensidad", ""),
            "total_evidencias": e.get("total_evidencias", 0),
            "decisor_sugerido": e.get("decisor_sugerido", ""),
            "angulo_conversacion": e.get("angulo_conversacion", ""),
            "motivos": motivos,
            "vertical": e.get("vertical", ""),
            "categoria": e.get("categoria", ""),
        })

    return ranking


def _score_compuesto(e: dict) -> float:
    """Calcula un score compuesto para ranking (mayor = más prioritario)."""
    score = 0.0

    scoring_map = {"A": 40, "B": 20, "C": 5}
    score += scoring_map.get(e.get("scoring", "C"), 0)

    score += min(e.get("score_icp", 0), 100) * 0.3

    n_ev = e.get("total_evidencias", 0)
    score += min(n_ev, 20) * 1.5

    n_patrones = len(e.get("patrones", []))
    score += n_patrones * 5

    if e.get("tipo_deuda"):
        score += 10
    if e.get("deuda_secundaria"):
        score += 3

    int_map = {"Alta": 10, "Media": 5, "Baja": 0}
    score += int_map.get(e.get("intensidad", ""), 0)

    return score


def _explicar_ranking(e: dict) -> list[str]:
    """Genera lista de motivos por los que la organización está rankeada."""
    motivos = []
    s = e.get("scoring", "C")
    if s == "A":
        motivos.append("Scoring A: dolor explícito, prioridad comercial máxima")
    elif s == "B":
        motivos.append("Scoring B: señal de crecimiento/cambio, oportunidad activa")

    n_ev = e.get("total_evidencias", 0)
    if n_ev >= 5:
        motivos.append(f"{n_ev} evidencias capturadas: volumen alto de señales")
    elif n_ev >= 2:
        motivos.append(f"{n_ev} evidencias capturadas")

    if e.get("tipo_deuda"):
        motivos.append(f"Dolor Cultural: {e['tipo_deuda']}")

    if e.get("intensidad") == "Alta":
        motivos.append("Intensidad Alta: múltiples señales de dolor convergentes")

    n_patrones = len(e.get("patrones", []))
    if n_patrones:
        motivos.append(f"{n_patrones} patrón(es) detectado(s)")

    icp = e.get("score_icp", 0)
    if icp >= 70:
        motivos.append(f"Score ICP {icp}: ajuste alto al perfil ideal HD")
    elif icp >= 50:
        motivos.append(f"Score ICP {icp}: ajuste medio-alto")

    return motivos


def _generar_alertas(expedientes: list[dict]) -> list[dict]:
    """Genera alertas basadas en expedientes con señales fuertes."""
    alertas = []
    for e in expedientes:
        razones = []
        if e.get("scoring") == "A" and e.get("intensidad") == "Alta":
            razones.append("dolor intenso detectado")
        if e.get("tipo_deuda") and e.get("deuda_secundaria"):
            razones.append("dos tipos de deuda simultáneos")
        n_ev = e.get("total_evidencias", 0)
        if n_ev >= 5:
            razones.append(f"{n_ev} evidencias acumuladas")
        n_pat = len(e.get("patrones", []))
        if n_pat >= 2:
            razones.append(f"{n_pat} patrones convergentes")

        if razones:
            alertas.append({
                "nombre": e["nombre"],
                "scoring": e["scoring"],
                "tipo_deuda": e.get("tipo_deuda", ""),
                "intensidad": e.get("intensidad", ""),
                "razones": razones,
                "accion_sugerida": _accion_sugerida(e),
            })

    return alertas[:20]


def _accion_sugerida(e: dict) -> str:
    """Sugiere la siguiente acción comercial para la organización."""
    s = e.get("scoring", "C")
    icp = e.get("score_icp", 0)
    if s == "A" and icp >= 50:
        return "Candidata a DolorMap Sprint: programar contacto con decisor"
    if s == "A":
        return "Candidata a Peritaje Cualitativo: profundizar observación"
    if s == "B" and icp >= 40:
        return "Mover a Vigilancia activa: capturar drift y onlife"
    return "Mantener en observación: acumular más evidencia"
