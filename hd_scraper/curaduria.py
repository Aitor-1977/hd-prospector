"""Motor de Curaduría Antropológica — Capa 10.

Transforma el conjunto de expedientes, hallazgos y análisis en una
LECTURA ANTROPOLÓGICA curada. No responde "¿qué pasó?" sino
"¿qué significa todo esto junto?".

El usuario lee conclusiones primero. Las noticias son evidencia secundaria.

100% determinista: mismos datos → misma curaduría. Sin IA ni red.

Flujo completo:
  Investigación → Captura → Normalización → Expedientes → Drift →
  Onlife → Dolor Cultural → CURADURÍA ANTROPOLÓGICA
"""
from __future__ import annotations

from collections import Counter

from .analisis import (
    ANGULO_POR_DEUDA,
    COMBINACIONES,
    DEUDA_POR_SENAL,
    MATIZ_VERTICAL,
    SENALES_CAMBIO,
    SENALES_DOLOR,
    VERTICALES_HD_SET,
)


# Tensiones ecosistémicas: cuando el ecosistema entero muestra un patrón,
# la lectura sube de nivel — ya no es "esta empresa tiene problemas", es
# "este sector está en transición".
_TENSIONES: tuple[tuple[str, str, str], ...] = (
    ("dolor_y_crecimiento",
     "El ecosistema crece y sufre al mismo tiempo",
     "Las organizaciones que crecen conviven con las que recortan. "
     "Esto sugiere un sector en transición donde el crecimiento no es "
     "uniforme: algunos jugadores escalan mientras otros no logran sostener "
     "su operación. La pregunta no es quién crece, sino quién crece sano."),
    ("dolor_dominante",
     "El dolor supera al crecimiento en el ecosistema",
     "La mayoría de las señales apuntan a fricción, recortes o cierre. "
     "Esto indica un ecosistema bajo presión: las organizaciones están "
     "reaccionando más que construyendo. Es un momento donde la cultura "
     "organizacional se pone a prueba."),
    ("crecimiento_dominante",
     "El ecosistema está en fase de expansión",
     "Predominan señales de rondas, contratación y expansión. "
     "Es un momento fértil pero riesgoso: las organizaciones que crecen "
     "rápido acumulan deuda cultural sin darse cuenta. La pregunta es "
     "cuántas están creciendo con conciencia de lo que se estira."),
    ("estancamiento",
     "El ecosistema muestra poca actividad",
     "Las señales son escasas o de baja intensidad. No hay dolor evidente "
     "pero tampoco crecimiento claro. Puede ser un ecosistema maduro, "
     "estancado, o simplemente fuera del radar de la prensa. Se necesita "
     "observación más profunda antes de concluir."),
)


def curar(
    expedientes: list[dict],
    query: str = "",
    region: str = "",
    vertical: str = "",
) -> dict:
    """Genera la curaduría antropológica completa.

    Devuelve un dict con la lectura de ecosistema, no una lista de hechos.
    """
    if not expedientes:
        return _curaduria_sin_datos(query, region, vertical)

    n = len(expedientes)
    kw_all: list[str] = []
    deudas: list[str] = []
    verticales: list[str] = []
    dolor_orgs: list[dict] = []
    cambio_orgs: list[dict] = []
    convergencias: list[dict] = []

    for e in expedientes:
        kws = set(e.get("keywords", []))
        kw_all.extend(e.get("keywords", []))
        if e.get("tipo_deuda"):
            deudas.append(e["tipo_deuda"])
        if e.get("vertical"):
            verticales.append(e["vertical"])
        if kws & SENALES_DOLOR:
            dolor_orgs.append(e)
        if kws & SENALES_CAMBIO:
            cambio_orgs.append(e)

        if len(kws) >= 2 and (kws & SENALES_DOLOR) and (kws & SENALES_CAMBIO):
            convergencias.append(e)

    n_dolor = len(dolor_orgs)
    n_cambio = len(cambio_orgs)
    deuda_counter = Counter(deudas)
    vert_counter = Counter(verticales)
    kw_counter = Counter(kw_all)

    tension = _identificar_tension(n, n_dolor, n_cambio)
    narrativa = _construir_narrativa(
        expedientes, n, n_dolor, n_cambio, deuda_counter,
        vert_counter, kw_counter, query, region, vertical, tension,
    )
    convergencias_curadas = _curar_convergencias(convergencias)
    lectura = _lectura_antropologica(
        expedientes, deuda_counter, vert_counter, tension, vertical,
    )
    preguntas = _preguntas_abiertas(
        deuda_counter, n_dolor, n_cambio, n, vertical,
    )
    siguiente = _siguiente_paso(expedientes, deuda_counter, n_dolor, tension)
    orgs_curadas = _organizaciones_curadas(expedientes)

    return {
        "tension_central": {
            "tipo": tension[0],
            "titulo": tension[1],
            "descripcion": tension[2],
        },
        "narrativa": narrativa,
        "lectura_antropologica": lectura,
        "convergencias": convergencias_curadas,
        "organizaciones_curadas": orgs_curadas,
        "preguntas_abiertas": preguntas,
        "siguiente_paso": siguiente,
        "meta": {
            "total_organizaciones": n,
            "con_dolor": n_dolor,
            "con_cambio": n_cambio,
            "con_convergencia": len(convergencias),
            "deudas_detectadas": len(deuda_counter),
            "query": query,
            "region": region,
            "vertical": vertical,
        },
    }


def _curaduria_sin_datos(query: str, region: str, vertical: str) -> dict:
    return {
        "tension_central": {
            "tipo": "sin_datos",
            "titulo": "Sin evidencia suficiente",
            "descripcion": "No hay expedientes para construir una lectura "
                           "antropológica. Se necesita ampliar la captura.",
        },
        "narrativa": (
            f"La búsqueda «{query}» no arrojó expedientes con señales "
            "suficientes para una lectura ecosistémica. Esto no significa "
            "que el ecosistema esté inactivo — puede significar que las "
            "fuentes disponibles no capturan lo que ocurre. Se recomienda "
            "ampliar la observación con drift narrativo y señales onlife."
        ),
        "lectura_antropologica": "",
        "convergencias": [],
        "organizaciones_curadas": [],
        "preguntas_abiertas": [
            "¿Las fuentes actuales capturan lo que realmente ocurre en este ecosistema?",
            "¿Hay actividad relevante que no pasa por prensa?",
        ],
        "siguiente_paso": "Ampliar la captura: observar drift narrativo "
                          "de actores conocidos y buscar señales onlife.",
        "meta": {
            "total_organizaciones": 0,
            "con_dolor": 0, "con_cambio": 0, "con_convergencia": 0,
            "deudas_detectadas": 0,
            "query": query, "region": region, "vertical": vertical,
        },
    }


def _identificar_tension(
    n: int, n_dolor: int, n_cambio: int,
) -> tuple[str, str, str]:
    if n == 0:
        return _TENSIONES[3]

    pct_dolor = n_dolor / n * 100
    pct_cambio = n_cambio / n * 100

    if pct_dolor >= 20 and pct_cambio >= 20:
        return _TENSIONES[0]  # dolor_y_crecimiento
    if pct_dolor >= 20:
        return _TENSIONES[1]  # dolor_dominante
    if pct_cambio >= 20:
        return _TENSIONES[2]  # crecimiento_dominante
    return _TENSIONES[3]      # estancamiento


def _construir_narrativa(
    expedientes: list[dict],
    n: int, n_dolor: int, n_cambio: int,
    deuda_counter: Counter, vert_counter: Counter, kw_counter: Counter,
    query: str, region: str, vertical: str,
    tension: tuple[str, str, str],
) -> str:
    """Narrativa ecosistémica en prosa: la conclusión principal."""
    partes: list[str] = []

    # Apertura: qué se observó
    contexto = f"«{query}»" if query else "el ecosistema observado"
    partes.append(
        f"Al analizar {contexto}"
        f"{' en ' + region if region else ''}"
        f"{' (vertical: ' + vertical + ')' if vertical else ''}, "
        f"se observaron {n} organizaciones con señales relevantes."
    )

    # Tensión central
    partes.append(tension[2])

    # Hipótesis de deuda dominante
    if deuda_counter:
        top_deuda, top_count = deuda_counter.most_common(1)[0]
        pct = round(top_count / n * 100)
        if pct >= 30:
            partes.append(
                f"La hipótesis de deuda más frecuente es {top_deuda} "
                f"({top_count} de {n} organizaciones, {pct}%). "
                f"Esto no confirma la deuda — señala dónde la evidencia "
                f"narrativa apunta con más fuerza y merece investigación "
                f"cualitativa directa."
            )
        elif len(deuda_counter) >= 2:
            tops = deuda_counter.most_common(2)
            partes.append(
                f"Las hipótesis de deuda se distribuyen entre "
                f"{tops[0][0]} ({tops[0][1]}) y {tops[1][0]} ({tops[1][1]}). "
                f"No hay un patrón dominante claro; el ecosistema muestra "
                f"tensiones diversas que requieren observación diferenciada."
            )

    # Señales clave
    top_kw = kw_counter.most_common(3)
    if top_kw:
        nombres = [f"{k} ({v})" for k, v in top_kw]
        partes.append(
            f"Las señales más recurrentes son {', '.join(nombres)}, "
            f"lo que delimita el territorio de observación."
        )

    return " ".join(partes)


def _curar_convergencias(convergencias: list[dict]) -> list[dict]:
    """Organizaciones donde dolor y cambio convergen — las más interesantes."""
    curadas = []
    for e in sorted(convergencias,
                    key=lambda x: x.get("score_icp", 0), reverse=True)[:5]:
        kws = set(e.get("keywords", []))
        dolor_kws = kws & SENALES_DOLOR
        cambio_kws = kws & SENALES_CAMBIO

        lectura = _lectura_convergencia(dolor_kws, cambio_kws, e)

        curadas.append({
            "nombre": e["nombre"],
            "scoring": e.get("scoring", "C"),
            "hipotesis_deuda": e.get("tipo_deuda", ""),
            "senales_dolor": sorted(dolor_kws),
            "senales_cambio": sorted(cambio_kws),
            "lectura": lectura,
            "intensidad": e.get("intensidad", ""),
            "angulo": e.get("angulo_conversacion", ""),
        })
    return curadas


def _lectura_convergencia(
    dolor: set, cambio: set, e: dict,
) -> str:
    """Genera lectura interpretativa de una convergencia dolor+cambio."""
    nombre = e.get("nombre", "la organización")
    deuda = e.get("tipo_deuda", "")

    if "friccion_retencion" in dolor and "crecimiento" in cambio:
        return (f"{nombre} crece en números pero pierde clientes. "
                "La pregunta es si el crecimiento compensa la erosión "
                "o la está enmascarando.")
    if "reduccion_personal" in dolor and "ronda_inversion" in cambio:
        return (f"{nombre} levantó capital pero recorta personal. "
                "Hay una tensión entre la expectativa del inversor "
                "y la realidad operativa.")
    if "friccion_retencion" in dolor and "expansion" in cambio:
        return (f"{nombre} se expande mientras sube la fricción. "
                "La operación crece más rápido que la capacidad de "
                "sostener la experiencia.")
    if "cierre_operaciones" in dolor and cambio:
        return (f"{nombre} cierra en un frente mientras mueve en otro. "
                "Puede ser reestructuración estratégica o señal de "
                "que la base no sostiene la expansión.")
    if "regulacion" in dolor and cambio:
        return (f"{nombre} enfrenta presión regulatoria en medio de "
                "cambios. La tensión entre cumplimiento y agilidad "
                "puede estar definiendo sus decisiones.")

    if deuda:
        return (f"{nombre} muestra señales simultáneas de dolor y cambio. "
                f"La hipótesis de {deuda} necesita corroboración con "
                "observación directa.")

    return (f"{nombre} presenta señales cruzadas de dolor y cambio. "
            "Merece observación cercana para entender si está en "
            "crisis, en transición, o en ambas.")


def _lectura_antropologica(
    expedientes: list[dict],
    deuda_counter: Counter,
    vert_counter: Counter,
    tension: tuple[str, str, str],
    vertical: str,
) -> str:
    """La lectura de fondo: qué le pasa al ecosistema como sistema."""
    n = len(expedientes)
    if n == 0:
        return ""

    partes: list[str] = []

    n_a = sum(1 for e in expedientes if e.get("scoring") == "A")
    n_patrones = sum(len(e.get("patrones", [])) for e in expedientes)

    tipo_tension = tension[0]
    if tipo_tension == "dolor_y_crecimiento":
        partes.append(
            "El ecosistema está en un punto de bifurcación: "
            "algunas organizaciones están construyendo futuro mientras "
            "otras luchan por sostener el presente. Esta coexistencia "
            "de dolor y crecimiento es característica de sectores en "
            "transformación, donde las reglas del juego están cambiando."
        )
    elif tipo_tension == "dolor_dominante":
        partes.append(
            "El ecosistema muestra signos de estrés colectivo. "
            "Cuando múltiples organizaciones exhiben señales de dolor "
            "simultáneamente, rara vez es coincidencia — suele reflejar "
            "una presión sistémica (regulatoria, de mercado, o de modelo) "
            "que afecta al sector completo."
        )
    elif tipo_tension == "crecimiento_dominante":
        partes.append(
            "El ecosistema está en fase expansiva. Pero el crecimiento "
            "es donde más deuda cultural se acumula sin ser vista: las "
            "organizaciones que crecen rápido suelen descubrir sus "
            "grietas culturales meses después, cuando la velocidad "
            "ya no alcanza para tapar los vacíos."
        )
    else:
        partes.append(
            "El ecosistema no muestra señales fuertes en ninguna dirección. "
            "Esto puede indicar madurez, estancamiento, o que la observación "
            "necesita fuentes más cercanas al terreno (drift, onlife)."
        )

    # Matiz por vertical
    vert = (vertical or "").strip().lower()
    if vert in MATIZ_VERTICAL:
        partes.append(f"Contexto vertical: {MATIZ_VERTICAL[vert]}.")

    # Patrones cruzados
    if n_patrones > 0:
        partes.append(
            f"Se detectaron {n_patrones} patrones cruzados entre señales, "
            "lo que sugiere que las tensiones no son aisladas sino que "
            "se retroalimentan."
        )

    # Deuda como fenómeno ecosistémico
    if len(deuda_counter) >= 3:
        tipos = [d for d, _ in deuda_counter.most_common(3)]
        partes.append(
            f"La diversidad de hipótesis de deuda ({', '.join(tipos)}) "
            "indica un ecosistema con múltiples frentes abiertos, no "
            "un problema único. Cada tipo de deuda necesita un abordaje "
            "distinto."
        )
    elif len(deuda_counter) == 1:
        unica = list(deuda_counter.keys())[0]
        partes.append(
            f"La concentración en una sola hipótesis ({unica}) sugiere "
            "una presión sistémica específica. Merece investigación "
            "cualitativa enfocada."
        )

    return " ".join(partes)


def _preguntas_abiertas(
    deuda_counter: Counter,
    n_dolor: int, n_cambio: int, n: int,
    vertical: str,
) -> list[str]:
    """Preguntas que la curaduría deja abiertas para investigación."""
    preguntas: list[str] = []

    if n_dolor > 0 and n_cambio > 0:
        preguntas.append(
            "¿Las organizaciones que crecen y las que sufren comparten "
            "el mismo mercado, o están en segmentos distintos?"
        )

    if deuda_counter:
        top = deuda_counter.most_common(1)[0][0]
        preguntas.append(
            f"¿La {top} es un fenómeno local de estas organizaciones o "
            "refleja una condición del sector?"
        )

    if n > 5:
        preguntas.append(
            "¿Hay organizaciones que no aparecen en prensa pero son "
            "relevantes para este ecosistema? La ausencia de señal "
            "no es ausencia de tensión."
        )

    vert = (vertical or "").strip().lower()
    if vert in VERTICALES_HD_SET:
        preguntas.append(
            f"¿Cómo influye la naturaleza de {vert} en la forma en que "
            "estas organizaciones viven sus tensiones? No es lo mismo "
            "un recorte en fintech que en edtech."
        )

    preguntas.append(
        "¿Qué dirían las personas dentro de estas organizaciones "
        "si pudieran hablar sin la versión oficial? La distancia entre "
        "el discurso público y la experiencia interna es donde vive "
        "la deuda cultural."
    )

    return preguntas[:5]


def _siguiente_paso(
    expedientes: list[dict],
    deuda_counter: Counter,
    n_dolor: int,
    tension: tuple[str, str, str],
) -> str:
    """Qué hacer después de esta lectura."""
    n_a = sum(1 for e in expedientes if e.get("scoring") == "A")
    n_convergentes = sum(
        1 for e in expedientes
        if (set(e.get("keywords", [])) & SENALES_DOLOR)
        and (set(e.get("keywords", [])) & SENALES_CAMBIO)
    )

    if n_convergentes >= 2:
        return (
            "Priorizar observación directa de las organizaciones con "
            "convergencia (dolor + cambio simultáneo). Son las que más "
            "probablemente estén en un punto de inflexión donde HD puede "
            "aportar valor. Iniciar drift narrativo y captura onlife."
        )
    if n_a >= 3:
        return (
            "Hay suficiente evidencia de dolor para justificar un "
            "peritaje cualitativo enfocado en las organizaciones de "
            "mayor interés analítico. Antes de contacto, corroborar "
            "las hipótesis con observación directa (drift + onlife)."
        )
    if n_dolor > 0:
        return (
            "Ampliar la observación con drift narrativo de las "
            "organizaciones que muestran señales de dolor. Una sola "
            "noticia no confirma la hipótesis — se necesitan al menos "
            "dos fuentes independientes antes de escalar."
        )
    if len(expedientes) > 0:
        return (
            "Las señales actuales son de cambio, no de dolor. "
            "Mantener vigilancia activa: capturar drift narrativo "
            "cada 2 semanas para detectar si el crecimiento genera "
            "tensiones que aún no son visibles."
        )
    return "Ampliar la captura de evidencia antes de formular hipótesis."


def _viabilidad_candidato(e: dict) -> dict:
    """Evalúa la viabilidad del candidato para el laboratorio HD."""
    viab = e.get("viabilidad", "")
    profundidad = e.get("profundidad_dolor", 0)
    scoring = e.get("scoring", "C")
    deuda = e.get("tipo_deuda", "")

    if not viab:
        if scoring == "A" and deuda:
            viab = "alta" if profundidad >= 50 else "media"
        elif scoring == "B":
            viab = "media" if deuda else "baja"
        else:
            viab = "baja"

    if viab == "alta":
        razon = ("Candidato viable: señales de dolor estructural con "
                 "profundidad suficiente para justificar investigación onlife.")
    elif viab == "media":
        razon = ("Candidato en observación: hay indicios pero se necesita "
                 "convergencia con más fuentes antes de escalar.")
    elif viab == "baja":
        razon = ("Candidato preliminar: la señal existe pero es insuficiente "
                 "para formular una hipótesis sólida de fricción estructural.")
    else:
        razon = ("No viable: la evidencia no muestra indicios de fricción "
                 "estructural relevante para el laboratorio.")

    return {"nivel": viab, "razon": razon, "profundidad": profundidad}


def _evidencia_curada(e: dict) -> dict:
    """Separa la evidencia narrativa del ruido: qué es hecho y qué es señal."""
    kws = set(e.get("keywords", []))
    dolor_kws = sorted(kws & SENALES_DOLOR)
    cambio_kws = sorted(kws & SENALES_CAMBIO)
    deuda = e.get("tipo_deuda", "")
    razon = e.get("deuda_razon", "")

    hechos: list[str] = []
    if dolor_kws:
        hechos.append(f"Señales de dolor: {', '.join(dolor_kws)}")
    if cambio_kws:
        hechos.append(f"Señales de cambio: {', '.join(cambio_kws)}")

    hipotesis = ""
    if deuda:
        hipotesis = (f"Hipótesis: {deuda}. {razon}. "
                     "Señal narrativa — requiere convergencia operativa "
                     "para confirmación.")

    return {
        "hechos_estructurales": hechos,
        "hipotesis_deuda": hipotesis,
        "total_evidencias": e.get("total_evidencias", 0),
        "intensidad": e.get("intensidad", ""),
    }


def _organizaciones_curadas(
    expedientes: list[dict], limite: int = 5,
) -> list[dict]:
    """TOP organizaciones con lectura interpretativa (no ranking de score).

    Cada organización incluye:
      1) Identificación como sujeto central
      2) Evidencia narrativa curada (separando ruido de hecho)
      3) Hipótesis de viabilidad del candidato para el laboratorio
    """
    candidatas = [e for e in expedientes if e.get("scoring") in ("A", "B")]
    if not candidatas:
        candidatas = expedientes[:limite]

    candidatas.sort(key=lambda e: (
        0 if e.get("scoring") == "A" else 1,
        -e.get("score_icp", 0),
        -e.get("total_evidencias", 0),
    ))

    curadas = []
    for e in candidatas[:limite]:
        curadas.append({
            "nombre": e["nombre"],
            "scoring": e.get("scoring", "C"),
            "hipotesis_deuda": e.get("tipo_deuda", ""),
            "intensidad": e.get("intensidad", ""),
            "interes": e.get("score_icp", 0),
            "total_evidencias": e.get("total_evidencias", 0),
            "lectura": _lectura_organizacion(e),
            "evidencia_curada": _evidencia_curada(e),
            "viabilidad_hd": _viabilidad_candidato(e),
            "angulo": e.get("angulo_conversacion", ""),
            "decisor": e.get("decisor_sugerido", ""),
            "vertical": e.get("vertical", ""),
        })
    return curadas


def _lectura_organizacion(e: dict) -> str:
    """Genera una lectura interpretativa de una organización."""
    nombre = e.get("nombre", "la organización")
    deuda = e.get("tipo_deuda", "")
    razon = e.get("deuda_razon", "")
    scoring = e.get("scoring", "C")
    n_ev = e.get("total_evidencias", 0)
    n_pat = len(e.get("patrones", []))
    secundaria = e.get("deuda_secundaria", "")

    partes: list[str] = []

    if scoring == "A" and deuda:
        partes.append(
            f"{nombre} presenta señales de dolor explícito que "
            f"apuntan a una hipótesis de {deuda}."
        )
    elif scoring == "B" and deuda:
        partes.append(
            f"{nombre} muestra señales de cambio activo con "
            f"una hipótesis emergente de {deuda}."
        )
    elif scoring == "A":
        partes.append(f"{nombre} tiene señales de dolor sin hipótesis clara de deuda aún.")
    else:
        partes.append(f"{nombre} está en observación con señales de baja intensidad.")

    if razon:
        partes.append(f"Contexto: {razon}.")

    if secundaria:
        partes.append(
            f"Además, se observa una tensión secundaria ({secundaria}), "
            "lo que sugiere que la situación es más compleja que una "
            "sola deuda."
        )

    if n_pat >= 2:
        partes.append(
            f"Con {n_pat} patrones cruzados, las señales convergen — "
            "esto eleva el interés analítico."
        )

    if n_ev >= 5:
        partes.append(
            f"El volumen de evidencia ({n_ev}) da solidez a la "
            "hipótesis, aunque no la confirma."
        )
    elif n_ev <= 1:
        partes.append(
            "Con solo una evidencia, la hipótesis es preliminar — "
            "se necesita más observación."
        )

    return " ".join(partes)
