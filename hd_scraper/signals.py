"""Extracción objetiva Nivel 1: keywords de señal + confianza.

Esto es Motor A (hechos), NO Motor B (HD). Detecta frases de señal con una
taxonomía GENÉRICA y pública (ronda, despidos, churn/retención, expansión,
cambio de liderazgo, lanzamiento, adquisición) — vocabulario estándar de
negocio, no la taxonomía propietaria de Deuda Cultural™. La conversión de estas
señales en Deuda Cultural (Moral/Temporal/Relacional…) es responsabilidad del
Motor B (RadarHD), fuera de este repo.

La ``confianza`` mide la CALIDAD OBJETIVA de la extracción (¿está fechada?,
¿fuente nombrada?, ¿trae señales?), no un juicio semántico del contenido.
"""
from __future__ import annotations

# Taxonomía genérica de señales (objetiva). tag -> frases que la disparan.
SENALES: dict[str, tuple[str, ...]] = {
    "ronda_inversion": ("ronda", "levanta capital", "serie a", "serie b", "serie c",
                        "financiamiento", "recauda", "inversión", "capital semilla"),
    "reduccion_personal": ("despido", "despidos", "reducción de personal", "recorte",
                           "layoff", "reestructura"),
    "friccion_retencion": ("churn", "baja retención", "abandono", "fricción", "quejas",
                           "cancelaciones", "deserción", "insatisfacción"),
    "expansion": ("expansión", "nuevo mercado", "nuevos mercados", "aterriza en", "apertura"),
    "cambio_liderazgo": ("nuevo ceo", "cambio de ceo", "nombra", "ficha a", "contratando",
                         "head of", "nuevo director"),
    "lanzamiento": ("lanzamiento", "nuevo producto", "lanza", "presenta", "estrena"),
    "adquisicion": ("adquisición", "adquiere", "compra de", "fusión", "fusiona"),
}

# Fuentes genéricas (no son un medio nombrado): bajan la confianza.
FUENTES_GENERICAS = {"google news", "gdelt", ""}


def detectar_keywords(texto: str) -> list[str]:
    """Devuelve las etiquetas de señal (objetivas) presentes en el texto."""
    t = (texto or "").lower()
    tags = [tag for tag, frases in SENALES.items() if any(f in t for f in frases)]
    return tags


def calcular_confianza(fecha_publicacion, nombre_medio: str, keywords: list[str]) -> float:
    """Confianza objetiva 0–1 según calidad de la extracción (no del contenido)."""
    score = 0.4
    if fecha_publicacion:
        score += 0.25
    if (nombre_medio or "").strip().lower() not in FUENTES_GENERICAS:
        score += 0.20
    if keywords:
        score += 0.15
    return round(min(score, 1.0), 2)
