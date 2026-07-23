"""Análisis profundo de prospecto — capa de INTERPRETACIÓN determinista.

IMPORTANTE (cambio de alcance pedido por el operador): históricamente el Motor A
(hd-prospector) solo CAPTURABA hechos y dejaba la interpretación (scoring, Deuda
Cultural™, decisor) al Motor B (RadarHD). El operador pidió explícitamente que
hd-prospector TAMBIÉN entregue análisis profundo. Este módulo lo hace de forma
100% DETERMINISTA (sin IA ni red): traduce las señales objetivas ya capturadas
(``keywords`` de ``signals.SENALES``, vertical, confianza, calidad) a:

  - ``scoring``      A | B | C  (prioridad comercial)
  - ``tipo_deuda``   hipótesis de Deuda Cultural™ (etiqueta legible)
  - ``score_icp``    0–100 (ajuste al perfil de prospecto ideal de HD)
  - ``decisor``      rol de decisor probable a buscar (no un dato verificado)
  - ``razon``        explicación auditable de la clasificación

Es una HIPÓTESIS reproducible a partir de hechos, no un juicio de IA. Al ser
determinista, el mismo insumo da siempre el mismo resultado y es testeable
offline. Si en el futuro se quiere calidad tipo-LLM, este módulo es el respaldo.
"""
from __future__ import annotations

from typing import Optional

# Verticales dependientes de contexto que le interesan a HD (perfil ideal).
VERTICALES_HD_SET = {
    "fintech", "edtech", "healthtech", "salud mental", "logística agrícola",
    "identidad", "hrtech", "saas_b2b", "climatetech",
}

# Señales que implican DOLOR organizacional explícito (máxima prioridad: hay
# necesidad). Determinan el scoring A.
SENALES_DOLOR = {
    "friccion_retencion", "reduccion_personal", "cierre_operaciones", "regulacion",
}

# Señales de crecimiento / cambio (oportunidad, prioridad media -> B).
SENALES_CAMBIO = {
    "ronda_inversion", "expansion", "crecimiento", "contratacion_masiva",
    "cambio_liderazgo", "adquisicion", "alianza",
}

# Hipótesis de Deuda Cultural™ por señal (la más fuerte gana, en este orden).
# Etiqueta legible + una nota corta de por qué. Es interpretación DECLARADA.
DEUDA_POR_SENAL: dict[str, tuple[str, str]] = {
    "friccion_retencion": ("Deuda Relacional",
                           "fricción/churn: la relación con el cliente se desgasta más rápido de lo que el dato explica"),
    "reduccion_personal": ("Deuda Moral",
                           "recortes/reestructura: tensión interna y pérdida de confianza del equipo"),
    "cierre_operaciones": ("Deuda Estructural",
                           "cierre/quiebra parcial: el modelo operativo no sostiene la promesa"),
    "regulacion": ("Deuda de Gobernanza",
                   "presión regulatoria: la cultura de cumplimiento va detrás del negocio"),
    "cambio_liderazgo": ("Deuda de Liderazgo",
                         "rotación en la cúpula: la narrativa y la dirección quedan en transición"),
    "contratacion_masiva": ("Deuda de Onboarding",
                            "crecimiento de plantilla: la cultura no escala al ritmo de la contratación"),
    "ronda_inversion": ("Deuda de Escalamiento",
                        "capital nuevo: presión por crecer más rápido de lo que la organización asimila"),
    "expansion": ("Deuda de Escalamiento",
                  "nuevos mercados: la operación se estira antes de estar lista"),
    "crecimiento": ("Deuda de Escalamiento",
                    "crecimiento acelerado: procesos y cultura corren detrás del negocio"),
    "adquisicion": ("Deuda de Integración",
                    "fusión/adquisición: dos culturas que deben integrarse"),
    "alianza": ("Deuda de Integración",
                "alianza estratégica: coordinación entre organizaciones distintas"),
    "lanzamiento": ("Deuda de Adopción",
                    "lanzamiento: el reto pasa a que el mercado adopte y retenga"),
}

# Hipótesis de COMBINACIÓN: dos señales juntas dicen algo más preciso que cada
# una por separado. Se evalúan EN ORDEN; la primera cuyos tags estén todos
# presentes gana como deuda PRINCIPAL (antes que la de señal única). Esto afina
# el diagnóstico: no es lo mismo "recorte" que "recorte justo después de levantar
# capital". Sigue siendo determinista y declarado.
COMBINACIONES: tuple[tuple[frozenset, str, str], ...] = (
    (frozenset({"reduccion_personal", "ronda_inversion"}),
     "Deuda de Escalamiento mal gestionado",
     "levantó capital y aun así recorta: la expectativa de crecer choca con una operación que no la sostiene"),
    (frozenset({"friccion_retencion", "crecimiento"}),
     "Deuda de Experiencia en escala",
     "crece en números pero pierde clientes: el crecimiento tapa una experiencia que no retiene"),
    (frozenset({"friccion_retencion", "expansion"}),
     "Deuda de Experiencia en escala",
     "se expande mientras sube la fricción: la operación crece más rápido que la cultura de cliente"),
    (frozenset({"cambio_liderazgo", "reduccion_personal"}),
     "Deuda de Liderazgo en crisis",
     "rotación en la cúpula junto a recortes: la organización queda sin brújula y con miedo"),
    (frozenset({"regulacion", "lanzamiento"}),
     "Deuda de Gobernanza en producto",
     "lanza producto bajo presión regulatoria: el cumplimiento va detrás de la ambición de producto"),
    (frozenset({"adquisicion", "reduccion_personal"}),
     "Deuda de Integración con recortes",
     "adquisición con recortes: dos culturas que además deben integrarse perdiendo gente"),
    (frozenset({"contratacion_masiva", "friccion_retencion"}),
     "Deuda de Onboarding y experiencia",
     "contrata rápido mientras pierde clientes: crece el equipo pero no la calidad de la relación"),
    (frozenset({"cierre_operaciones", "expansion"}),
     "Deuda Estructural de expansión",
     "cierra en un frente mientras abre en otro: la expansión no se sostiene en la base"),
    (frozenset({"regulacion", "friccion_retencion"}),
     "Deuda de Confianza",
     "fricción de clientes con ruido regulatorio: la confianza se erosiona por fuera y por dentro"),
)

# Ángulo de conversación sugerido por tipo de deuda: cómo abrir la charla
# comercial con HD. Accionable, no es un juicio del contenido.
ANGULO_POR_DEUDA: dict[str, str] = {
    "Deuda Relacional": "abrir por la experiencia del cliente: ¿qué se rompió en la relación antes del churn?",
    "Deuda Moral": "abrir por el clima interno: ¿qué narrativa quedó en el equipo tras los recortes?",
    "Deuda Estructural": "abrir por el modelo operativo: ¿qué promesa dejó de sostener la operación?",
    "Deuda de Gobernanza": "abrir por el cumplimiento: ¿cómo alinear cultura y regulación sin frenar el negocio?",
    "Deuda de Liderazgo": "abrir por la transición: ¿qué relato necesita la nueva dirección para alinear?",
    "Deuda de Onboarding": "abrir por la incorporación: ¿cómo transmitir cultura al ritmo de la contratación?",
    "Deuda de Escalamiento": "abrir por la presión de crecer: ¿qué se está estirando más allá de su límite sano?",
    "Deuda de Integración": "abrir por la fusión de culturas: ¿qué identidad común hace falta construir?",
    "Deuda de Adopción": "abrir por la adopción: ¿qué historia hace que el mercado use y retenga el producto?",
    "Deuda de Escalamiento mal gestionado": "abrir por el desajuste crecer/recortar: ¿qué expectativa quedó sin respaldo operativo?",
    "Deuda de Experiencia en escala": "abrir por el crecimiento hueco: ¿por qué crece el número y no la retención?",
    "Deuda de Liderazgo en crisis": "abrir por la brújula: ¿qué dirección y contención necesita el equipo ahora?",
    "Deuda de Gobernanza en producto": "abrir por producto vs. cumplimiento: ¿cómo lanzar sin deuda regulatoria?",
    "Deuda de Integración con recortes": "abrir por la integración dolorosa: ¿qué cultura sobrevive a la fusión con recortes?",
    "Deuda de Onboarding y experiencia": "abrir por el doble frente: crecer el equipo sin perder la relación con el cliente",
    "Deuda Estructural de expansión": "abrir por los cimientos: ¿qué base falta antes de seguir expandiendo?",
    "Deuda de Confianza": "abrir por la confianza: ¿cómo se reconstruye por dentro y por fuera a la vez?",
}

# Matiz por vertical sensible: añade contexto a la razón de la deuda.
MATIZ_VERTICAL: dict[str, str] = {
    "fintech": "en fintech, la confianza y el cumplimiento pesan doble",
    "salud mental": "en salud mental, la experiencia es especialmente sensible y personal",
    "healthtech": "en healthtech, el error erosiona la confianza clínica",
    "edtech": "en edtech, la retención depende de resultados percibidos",
    "identidad": "en identidad, un fallo golpea directo la confianza y el cumplimiento",
    "hrtech": "en hrtech, la fricción interna escala más rápido porque el producto toca la cultura directamente",
    "saas_b2b": "en SaaS B2B, el churn es lento pero caro: cada cliente perdido es un contrato largo",
    "climatetech": "en climatetech, la misión amplifica la tensión entre impacto y rentabilidad",
}

# Rol de decisor probable a buscar, según la señal dominante. Es una PISTA de a
# quién contactar, no un contacto verificado.
DECISOR_POR_SENAL: dict[str, str] = {
    "friccion_retencion": "Head of Customer Success / CX",
    "reduccion_personal": "Director/a de RRHH / People",
    "cierre_operaciones": "COO / Director/a de Operaciones",
    "regulacion": "Compliance / Dirección Legal",
    "cambio_liderazgo": "CEO / Fundador/a",
    "contratacion_masiva": "Director/a de RRHH / People",
    "ronda_inversion": "CEO / Fundador/a",
    "expansion": "Director/a General / Country Manager",
    "crecimiento": "CEO / Fundador/a",
    "adquisicion": "CEO / Corporate Development",
    "alianza": "Director/a de Alianzas / BD",
    "lanzamiento": "CPO / Head of Product",
}

# Orden de prioridad para elegir la señal DOMINANTE cuando hay varias.
_PRIORIDAD = (
    "friccion_retencion", "reduccion_personal", "cierre_operaciones", "regulacion",
    "cambio_liderazgo", "ronda_inversion", "adquisicion", "expansion",
    "crecimiento", "contratacion_masiva", "alianza", "lanzamiento",
)

CALIDAD_PESO = {"Alta": 10, "Media": 5, "Baja": 0}

INTENSIDAD_ALTA = "Alta"
INTENSIDAD_MEDIA = "Media"
INTENSIDAD_BAJA = "Baja"

# Profundidad de señal: cuánto dice la señal sobre fricción ESTRUCTURAL.
# 0–100 donde 100 = máxima profundidad operativa. El Interés Analítico se
# calcula a partir de esta profundidad, NO de lo "llamativo" del titular.
PROFUNDIDAD_SENAL: dict[str, int] = {
    "cierre_operaciones": 95,
    "friccion_retencion": 90,
    "reduccion_personal": 85,
    "regulacion": 70,
    "adquisicion": 65,
    "cambio_liderazgo": 60,
    "contratacion_masiva": 40,
    "ronda_inversion": 35,
    "expansion": 30,
    "crecimiento": 25,
    "alianza": 20,
    "lanzamiento": 15,
}

# Amplificación por vertical: ciertas verticales hacen ciertas señales MÁS
# relevantes para el trabajo de laboratorio de HD. Multiplicador sobre la
# profundidad de la señal dominante.
AMPLIFICADOR_VERTICAL: dict[str, dict[str, float]] = {
    "fintech": {
        "friccion_retencion": 1.3, "regulacion": 1.4,
        "cierre_operaciones": 1.2, "ronda_inversion": 1.1,
    },
    "healthtech": {
        "friccion_retencion": 1.3, "regulacion": 1.3,
        "reduccion_personal": 1.2,
    },
    "salud mental": {
        "friccion_retencion": 1.4, "reduccion_personal": 1.3,
    },
    "edtech": {
        "friccion_retencion": 1.3, "reduccion_personal": 1.2,
    },
    "hrtech": {
        "reduccion_personal": 1.4, "friccion_retencion": 1.3,
        "contratacion_masiva": 1.3,
    },
    "saas_b2b": {
        "friccion_retencion": 1.3, "reduccion_personal": 1.2,
    },
    "climatetech": {
        "regulacion": 1.3, "ronda_inversion": 1.2,
    },
    "identidad": {
        "regulacion": 1.4, "friccion_retencion": 1.3,
    },
    "logística agrícola": {
        "expansion": 1.3, "cierre_operaciones": 1.2,
    },
}


def _senales_ordenadas(keywords: list[str]) -> list[str]:
    """Señales presentes, en orden de prioridad (la primera es la dominante)."""
    ks = set(keywords or [])
    return [tag for tag in _PRIORIDAD if tag in ks]


def _senal_dominante(keywords: list[str]) -> Optional[str]:
    orden = _senales_ordenadas(keywords)
    return orden[0] if orden else None


def _intensidad(keywords: list[str], confianza: float) -> str:
    """Qué tan fuerte es la señal (Alta|Media|Baja), por cantidad de dolor + confianza."""
    ks = set(keywords or [])
    n_dolor = len(ks & SENALES_DOLOR)
    if n_dolor >= 2 or (n_dolor >= 1 and confianza >= 0.8):
        return INTENSIDAD_ALTA
    if n_dolor >= 1 or (ks & SENALES_CAMBIO and confianza >= 0.7):
        return INTENSIDAD_MEDIA
    return INTENSIDAD_BAJA


def _deuda_principal(keywords: list[str]) -> tuple[str, str]:
    """Deuda principal: primero por COMBINACIÓN de señales, luego por señal única."""
    ks = set(keywords or [])
    for tags, label, razon in COMBINACIONES:
        if tags <= ks:
            return label, razon
    dom = _senal_dominante(keywords)
    if dom and dom in DEUDA_POR_SENAL:
        return DEUDA_POR_SENAL[dom]
    return "", "sin señal de negocio clara para inferir deuda"


def _deuda_secundaria(keywords: list[str], deuda_principal: str) -> str:
    """Segunda hipótesis de deuda (si otra señal apunta a una deuda distinta)."""
    for tag in _senales_ordenadas(keywords):
        etiqueta = DEUDA_POR_SENAL.get(tag, ("", ""))[0]
        if etiqueta and etiqueta != deuda_principal:
            return etiqueta
    return ""


def _norm_vertical(vertical: str) -> str:
    return (vertical or "").strip().lower()


def _calcular_profundidad(dominante: str | None, vert: str) -> int:
    """Profundidad del dolor cruzada con el perfil de la vertical."""
    base = PROFUNDIDAD_SENAL.get(dominante or "", 0)
    amplificador = 1.0
    if vert in AMPLIFICADOR_VERTICAL and dominante:
        amplificador = AMPLIFICADOR_VERTICAL[vert].get(dominante, 1.0)
    return min(int(base * amplificador), 100)


def _calcular_viabilidad(
    scoring: str, profundidad: int, hay_dolor: bool, vert_hd: bool,
) -> str:
    """Viabilidad del candidato para el laboratorio HD."""
    if scoring == "A" and profundidad >= 70 and vert_hd:
        return "alta"
    if scoring == "A" and profundidad >= 50:
        return "alta"
    if scoring == "A":
        return "media"
    if scoring == "B" and profundidad >= 30 and vert_hd:
        return "media"
    if scoring == "B":
        return "baja"
    if profundidad > 0:
        return "baja"
    return "descartable"


def analizar(
    keywords: list[str],
    vertical: str = "",
    confianza: float = 0.0,
    calidad: str = "Baja",
    categoria: str = "",
) -> dict:
    """Convierte señales capturadas en análisis profundo (determinista).

    El Interés Analítico (score_icp) se calcula cruzando la PROFUNDIDAD del
    dolor detectado con el perfil de la vertical. No se basa en lo llamativo
    del titular, sino en la profundidad de la fricción estructural.

    Devuelve dict con scoring, tipo_deuda, deuda_razon, score_icp, decisor,
    viabilidad, profundidad_dolor y razon.
    """
    ks = set(keywords or [])
    hay_dolor = bool(ks & SENALES_DOLOR)
    hay_cambio = bool(ks & SENALES_CAMBIO)
    dominante = _senal_dominante(keywords)

    # Scoring A/B/C: dolor = A (hay necesidad), cambio/crecimiento = B, resto C.
    if hay_dolor:
        scoring = "A"
    elif hay_cambio or dominante == "lanzamiento":
        scoring = "B"
    else:
        scoring = "C"

    tipo_deuda, deuda_razon = _deuda_principal(keywords)
    vert = _norm_vertical(vertical)
    if tipo_deuda and vert in MATIZ_VERTICAL:
        deuda_razon = f"{deuda_razon}; {MATIZ_VERTICAL[vert]}"
    decisor = DECISOR_POR_SENAL.get(dominante or "", "CEO / Fundador/a")
    angulo = ANGULO_POR_DEUDA.get(tipo_deuda, "")

    # Profundidad del dolor: cruza señal con vertical.
    profundidad = _calcular_profundidad(dominante, vert)

    # Interés Analítico 0–100: basado en PROFUNDIDAD del dolor × vertical,
    # no en lo llamativo del titular.
    icp = 10
    icp += int(profundidad * 0.4)
    if vert in VERTICALES_HD_SET:
        icp += 15
    n_dolor = len(ks & SENALES_DOLOR)
    if n_dolor >= 2:
        icp += 20
    elif n_dolor == 1:
        icp += 15
    elif hay_cambio:
        icp += 5
    icp += int(round(max(0.0, min(confianza, 1.0)) * 10))
    icp += CALIDAD_PESO.get((calidad or "").strip(), 0)
    score_icp = max(0, min(icp, 100))

    vert_hd = vert in VERTICALES_HD_SET
    viabilidad = _calcular_viabilidad(scoring, profundidad, hay_dolor, vert_hd)

    # Razón auditable.
    partes = []
    if hay_dolor:
        partes.append("señal de dolor (fricción/recorte/cierre/regulación)")
    elif hay_cambio:
        partes.append("señal de crecimiento/cambio")
    else:
        partes.append("sin señal disparadora fuerte")
    if vert in VERTICALES_HD_SET:
        partes.append(f"vertical HD «{vert}»")
    partes.append(f"profundidad {profundidad}")
    partes.append(f"confianza captura {confianza:.2f}")
    razon = "; ".join(partes) + "."

    return {
        "scoring": scoring,
        "tipo_deuda": tipo_deuda,
        "deuda_razon": deuda_razon,
        "deuda_secundaria": _deuda_secundaria(keywords, tipo_deuda),
        "intensidad": _intensidad(keywords, confianza),
        "score_icp": score_icp,
        "decisor_sugerido": decisor,
        "angulo_conversacion": angulo,
        "senal_dominante": dominante or "",
        "profundidad_dolor": profundidad,
        "viabilidad": viabilidad,
        "razon": razon,
    }
