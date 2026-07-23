"""Captura Inteligente: filtro de relevancia y calidad — 100% objetivo.

Este módulo reduce el ruido del corpus SIN convertir al Motor A en un
clasificador. Todas las decisiones son deterministas, documentadas y basadas en
ESTRUCTURA (mayúsculas, presencia de palabras clave de señal, marcadores léxicos
de opinión, fuente nombrada). No hay IA ni juicio semántico: eso sigue siendo
responsabilidad exclusiva del Motor B (RadarHD).

Dos responsabilidades:

1) ``evaluar_relevancia`` — decide si un titular de descubrimiento merece entrar
   al corpus. Descarta, con motivo auditable, lo que el operador pidió filtrar:
     - artículos de opinión / columnas / editoriales / listículos / tendencias,
     - análisis general de industria sin empresa,
     - noticias que no mencionan una empresa concreta,
     - noticias que no describen un evento de negocio verificable.

2) ``calcular_calidad`` — etiqueta INFORMATIVA (Alta | Media | Baja) calculada a
   partir de criterios objetivos. Es puramente descriptiva del acto de captura;
   NO modifica el scoring del Motor B ni el contrato ``motor_a.corpus.v1``.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ── Normalización auxiliar ───────────────────────────────────────────────────

def _sin_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm(texto: str) -> str:
    return _sin_acentos((texto or "").lower())


# Google News añade " - Medio" al final del titular; ese sufijo NO es contenido
# ni una empresa. Se recorta antes de detectar el nombre propio.
_RE_MEDIO = re.compile(r"\s+[-–|]\s+[^-–|]+$")


def _sin_medio(titulo: str) -> str:
    t = (titulo or "").strip()
    recortado = _RE_MEDIO.sub("", t).strip()
    return recortado or t


# ── Detección de empresa (objetiva, sin IA) ──────────────────────────────────
#
# Heurística estructural: una empresa aparece como NOMBRE PROPIO en el titular.
# Buscamos tokens que empiezan en mayúscula (o siglas en mayúsculas) y cuya forma
# en minúsculas NO es una palabra común española ni un término genérico de
# sector. Es una señal de "hay una entidad nombrada", no una identificación
# semántica. Documentada y determinista.

# Palabras que suelen ir capitalizadas al inicio de frase o son genéricas: NO son
# nombres de empresa por sí solas.
_STOP_CAP = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "este", "esta",
    "estos", "estas", "ese", "esa", "su", "sus", "al", "del", "y", "o", "e", "u",
    "en", "con", "por", "para", "como", "cuando", "donde", "que", "quien", "cual",
    "cuanto", "cuanta", "cuantos", "cuantas", "porque", "segun", "tras", "sobre",
    "ante", "hasta", "desde", "entre", "sin", "mientras", "aunque", "asi",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
    "mexico", "colombia", "chile", "peru", "argentina", "brasil", "brazil",
    "panama", "latam", "latinoamerica", "america", "espana",
    "nuevo", "nueva", "mas", "menos", "gran", "gobierno", "estado", "pais",
}

# Términos genéricos de sector: describen el rubro, no a la empresa.
_GENERICOS_SECTOR = {
    "fintech", "edtech", "healthtech", "agtech", "insurtech", "proptech",
    "startup", "startups", "scaleup", "scaleups", "unicornio", "unicornios",
    "empresa", "empresas", "compania", "companias", "firma", "banco", "bancos",
    "plataforma", "app", "aplicacion", "mercado", "sector", "industria",
    "tecnologia", "digital", "ronda", "serie",
}


def _es_sigla(token: str) -> bool:
    """Sigla tipo BBVA, IBM, SAP: 2+ letras todas mayúsculas."""
    return len(token) >= 2 and token.isupper() and token.isalpha()


def detectar_empresa(titulo: str) -> Optional[str]:
    """Devuelve un candidato a empresa nombrada en el titular, o ``None``.

    Objetivo (sin IA): recorre los tokens y devuelve el primero que parezca
    nombre propio (mayúscula inicial o sigla) y no sea palabra común ni término
    de sector. No garantiza que sea "la" empresa; garantiza que HAY una entidad
    nombrada, que es la condición objetiva pedida.
    """
    if not titulo:
        return None
    # Tokens conservando mayúsculas; separadores no alfanuméricos fuera.
    # Se ignora el sufijo " - Medio" (no es contenido ni una empresa).
    for bruto in re.findall(r"[\wÁÉÍÓÚÑÜáéíóúñü]+", _sin_medio(titulo)):
        limpio = bruto.strip()
        if len(limpio) < 3:
            # Siglas cortas de 2 (p. ej. "BQ") son raras; exigimos 3+ salvo sigla.
            if not _es_sigla(limpio):
                continue
        base = _sin_acentos(limpio).lower()
        if base in _STOP_CAP or base in _GENERICOS_SECTOR:
            continue
        primera = limpio[0]
        if primera.isupper() or _es_sigla(limpio):
            return limpio
    return None


# ── Marcadores de opinión / tendencia / listículo (léxicos, objetivos) ───────
#
# Presencia de estos patrones en el TITULAR marca contenido de opinión o
# tendencia genérica (no un evento verificable de una empresa). Son cadenas
# fijas comprobables, no criterios ambiguos.
MARCADORES_OPINION: tuple[str, ...] = (
    "opinion", "columna", "editorial", "punto de vista", "analisis",
    "reflexion", "ensayo", "tribuna", "mi opinion", "carta abierta",
    "por que", "porque deberias", "como lograr", "como hacer", "como elegir",
    "guia para", "guia definitiva", "tutorial", "paso a paso",
    "el futuro de", "el fin de", "la era de", "tendencias", "predicciones",
    "lo que viene", "lo que aprendi", "claves para", "consejos para",
    "razones para", "razones por las que", "formas de", "maneras de",
    "ranking de", "los mejores", "las mejores", "top ",
)

# Listículos "5 claves", "10 razones", "3 formas": número + palabra de lista.
_RE_LISTICULO = re.compile(
    r"\b\d{1,3}\s+(claves|razones|formas|maneras|consejos|tips|pasos|"
    r"tendencias|predicciones|errores|habitos|secretos|mitos|preguntas)\b"
)


def es_opinion(titulo: str) -> bool:
    """True si el titular presenta marcadores objetivos de opinión/tendencia."""
    t = _norm(titulo)
    if any(m in t for m in MARCADORES_OPINION):
        return True
    return bool(_RE_LISTICULO.search(t))


# ── Geografía y "no es empresa" (deterministas) ──────────────────────────────
#
# Términos NO-LATAM (país, región, ciudad, gentilicio). El laboratorio opera
# México/LATAM; una nota de España/EE.UU./etc. se descarta.
NO_LATAM: tuple[str, ...] = (
    "espana", "espanola", "espanol", "madrid", "barcelona", "girona", "castilla",
    "cataluna", "andalucia", "sevilla", "galicia", "gallego", "vasco", "catalan",
    "estados unidos", "ee.uu", "eeuu", "reino unido", "inglaterra", "francia",
    "frances", "alemania", "aleman", "italia", "portugal", "china", "india",
    "japon", "canada",
    # Europa y otras regiones fuera de LATAM (se colaban por eventos regulatorios).
    "suiza", "suizo", "europa", "europea", "europeo", "union europea", "bruselas",
    "suecia", "noruega", "dinamarca", "finlandia", "holanda", "paises bajos",
    "belgica", "irlanda", "austria", "grecia", "polonia", "rusia", "ucrania",
    "australia", "corea", "singapur", "hong kong", "dubai", "emiratos", "israel",
)

# Marcas GIGANTES (tecnología global, comida rápida, consumo masivo). No son
# prospectos de HD (empresas en fase de escala con deuda cultural); su aparición
# en un titular casi siempre es ruido internacional, no un candidato LATAM.
GIGANTES: tuple[str, ...] = (
    "google", "alphabet", "amazon", "meta", "facebook", "instagram", "whatsapp",
    "apple", "microsoft", "netflix", "tesla", "samsung", "huawei", "tiktok",
    "nvidia", "intel", "spotify", "sony", "disney", "nike", "adidas",
    "wendy", "mcdonald", "burger king", "starbucks", "walmart", "coca-cola",
    "coca cola", "pepsi", "nestle", "unilever",
)

# Términos que indican que NO es una empresa prospecto: gobierno, premios,
# academia, gremios, y REPORTES/análisis de mercado (no una compañía concreta).
NO_EMPRESA: tuple[str, ...] = (
    "gobierno", "ministerio", "ministro", "ayuntamiento", "diputacion",
    "generalitat", "xunta", "alcaldia", "senado", "congreso",
    "premios", "premio", "galardon",
    "universidad", "facultad", "camara de", "colegio de",
    "asociacion", "federacion", "fundacion", "sindicato",
    "panorama de", "panorama del", "outlook", "perspectivas de",
    "perspectivas para", "estado de la", "el estado de", "balance de",
    "resumen del ano", "reporte anual", "informe anual", "state of",
    "de cada 10", "de cada diez", "siete de cada", "record en", "ranking",
    "radiografia",
    # Sucesos / nota roja / interés humano: no son una empresa prospecto.
    "muerte de", "muere ", "murio", "fallece", "fallecio", "asesinat",
    "asesinan", "homicidio", "feminicidio", "femicidio", "violencia de genero",
    "violencia de genero", "accidente", "detienen a", "detenido", "detenida",
    "narco", "secuestro", "balacera", "sismo", "terremoto", "huracan",
    "elecciones", "candidato", "candidata", "partido politico",
)

# Ruido mediático que NO es inteligencia de organización: deportes, espectáculos,
# clima, promociones y aperturas rutinarias. Estas notas nunca son un prospecto
# ni una señal estratégica; se descartan de la evidencia antes de mostrarse.
RUIDO_MEDIATICO: tuple[str, ...] = (
    # Deportes
    "futbol", "liga mx", "mundial", "champions", "nba", "nfl", "beisbol",
    "boxeo", "seleccion nacional", "gol de", "goles de", "partido de",
    # Espectáculos / entretenimiento / farándula
    "farandula", "espectaculos", "celebridad", "telenovela", "reality show",
    "alfombra roja", "concierto de", "estreno de la pelicula", "cantante",
    # Clima / desastres naturales
    "ola de calor", "frente frio", "granizada", "deslave", "inundacion",
    "tormenta tropical", "lluvias",
    # Promociones / ofertas / patrocinado
    "promocion", "descuento", "cupon", "oferta especial", "2x1", "rebajas",
    "contenido patrocinado", "nota patrocinada", "publirreportaje",
    # Aperturas rutinarias / obituarios
    "abre sucursal", "abre su sucursal", "nueva sucursal", "inaugura",
    "inauguracion", "franquicia", "obituario", "esquela",
)

# Eventos que involucran a una empresa pero NO tienen profundidad estructural
# para Thick Data. Pasan los filtros existentes (no opinión, no gigante, tienen
# empresa) pero NO son evidencia de fricción cultural. El laboratorio descarta
# esto ANTES de persistir: el sistema es un curador, no un recolector.
EVENTOS_SUPERFICIALES: tuple[str, ...] = (
    # Imagen corporativa / PR / marca — sin fricción
    "patrocinio", "patrocina", "sponsor", "naming rights",
    "responsabilidad social", "accion social", "voluntariado",
    "donacion a", "dona a", "dona millones",
    "aniversario de", "cumple anos", "celebra su aniversario",
    # Operaciones rutinarias — sin señal estructural
    "nueva version de", "actualizacion de", "actualiza su app",
    "cambio de imagen", "cambio de logo", "rebranding",
    # Eventos de industria sin impacto estructural
    "participa en", "participara en", "asiste a", "asistira a",
    "presenta en", "presento en", "conferencia de", "foro de",
    "summit", "webinar", "hackathon", "meetup",
    # Reconocimientos — positivos pero sin Thick Data
    "mejor empresa para trabajar", "great place to work",
    "reconocida como", "obtiene certificacion", "certificada como",
    # Comentario de mercado sin evento estructural
    "analistas esperan", "los expertos opinan", "valoracion de mercado",
    "cotiza a", "sube en bolsa", "baja en bolsa", "accion de",
    "acciones de", "precio objetivo",
)

# Título de reporte: un tema seguido de "AÑO:" (p. ej. "Venture Capital LATAM
# 2025:"). Señal fuerte de informe, casi nunca una empresa.
_RE_REPORTE = re.compile(r"\b20\d\d\s*:")


def _contiene(texto: str, terminos: tuple[str, ...]) -> bool:
    return any(t in texto for t in terminos)


# ── Filtro de relevancia mínimo ──────────────────────────────────────────────
#
# Motivos de descarte (se persisten en `rechazos`, auditables):
MOTIVO_OPINION = "relevancia:opinion"          # opinión / tendencia / listículo
MOTIVO_SIN_EMPRESA = "relevancia:sin_empresa"  # no menciona empresa concreta
MOTIVO_SIN_EVENTO = "relevancia:sin_evento"    # no describe evento verificable
MOTIVO_NO_LATAM = "relevancia:no_latam"        # geografía fuera de LATAM
MOTIVO_NO_EMPRESA = "relevancia:no_empresa"    # gobierno/premios/academia/reporte/suceso
MOTIVO_GIGANTE = "relevancia:gigante"          # marca gigante (no es ICP de HD)
MOTIVO_RUIDO = "relevancia:ruido_mediatico"    # deportes/espectáculos/clima/promos/aperturas
MOTIVO_SUPERFICIAL = "relevancia:sin_profundidad_estructural"  # evento sin Thick Data


def evaluar_relevancia(
    titulo: str, keywords: list, empresa_identificada: bool,
    exigir_evento: bool = True,
) -> tuple[bool, str]:
    """Decide si un titular de descubrimiento es relevante. Determinista.

    Filosofía: el sistema es un curador de Thick Data, no un recolector de
    noticias. Filtra agresivamente buscando indicios de fricción estructural,
    Deuda Cultural, Situacional o Simbólica.

    Reglas (todas deben cumplirse para CONSERVAR):
      R1  No es opinión/tendencia/listículo (marcadores léxicos).
      R2  No es geografía fuera de LATAM (España, EE.UU., …).
      R3  No es "no-empresa" (gobierno, premios, academia, reporte de mercado).
      R4  No es ruido mediático (deportes, espectáculos, clima, promos).
      R5  No es marca gigante global (no es perfil HD).
      R6  No es evento superficial sin profundidad estructural (PR, premios,
          conferencias, movimientos bursátiles rutinarios).
      R7  Hay una empresa identificable (nombre propio o consulta dirigida).
      R8  (solo si ``exigir_evento``) Hay un evento verificable en ``keywords``.

    Devuelve ``(relevante, motivo)``. ``motivo`` vacío si es relevante.
    """
    t = _norm(titulo)
    if es_opinion(titulo):
        return False, MOTIVO_OPINION
    if _contiene(t, NO_LATAM):
        return False, MOTIVO_NO_LATAM
    if _contiene(t, NO_EMPRESA) or _RE_REPORTE.search(t):
        return False, MOTIVO_NO_EMPRESA
    if _contiene(t, RUIDO_MEDIATICO):
        return False, MOTIVO_RUIDO
    if _contiene(t, GIGANTES):
        return False, MOTIVO_GIGANTE
    if _contiene(t, EVENTOS_SUPERFICIALES):
        return False, MOTIVO_SUPERFICIAL
    if not empresa_identificada:
        return False, MOTIVO_SIN_EMPRESA
    if exigir_evento and not keywords:
        return False, MOTIVO_SIN_EVENTO
    return True, ""


# ── Calidad de captura (informativa) ─────────────────────────────────────────
#
# Etiqueta objetiva del acto de captura. NO es una puntuación de valor comercial
# (eso es del Motor B). Se calcula con cuatro criterios objetivos:
#   c1  empresa claramente identificada,
#   c2  evento claramente identificado (señal genérica presente),
#   c3  fuente confiable (medio nombrado, no fuente genérica),
#   c4  ausencia de duplicados (garantizada: solo se etiquetan registros que
#       pasaron la deduplicación robusta).
CALIDAD_ALTA = "Alta"
CALIDAD_MEDIA = "Media"
CALIDAD_BAJA = "Baja"


def calcular_calidad(
    empresa_ok: bool, evento_ok: bool, fuente_ok: bool, sin_duplicado: bool = True
) -> str:
    """Etiqueta de calidad de captura (Alta|Media|Baja) por criterios objetivos.

    ``sin_duplicado`` es True para todo registro almacenado (la dedup ya corrió),
    por lo que actúa como criterio siempre satisfecho; la etiqueta la determinan
    los tres criterios variables (empresa, evento, fuente):
        3 de 3  -> Alta      2 de 3 -> Media      <=1 de 3 -> Baja
    """
    n = int(bool(empresa_ok)) + int(bool(evento_ok)) + int(bool(fuente_ok))
    if not sin_duplicado:
        return CALIDAD_BAJA
    if n >= 3:
        return CALIDAD_ALTA
    if n == 2:
        return CALIDAD_MEDIA
    return CALIDAD_BAJA
