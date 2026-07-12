"""Conector de feeds RSS fijos (Fase 1, tercer conector).

A diferencia de Google News / GDELT (que buscan por empresa en una API), aquí
se traen feeds RSS de sitios completos y se filtran las entradas que MENCIONAN
la empresa por coincidencia literal de subcadena. Ese filtro es extracción
determinista (¿contiene el texto el nombre de la empresa?), NO interpretación:
no se lee ni se juzga el contenido.

Fuentes fijas de Fase 1: Startupeable, Contxto, LAVCA, LatamList,
Bloomberg Línea, Forbes México, El CEO, Xataka México.

Sobre la invariante "no interpreta":
  - ``tipo_evento`` viaja en la ``QuerySpec`` (lo declara el operador).
  - ``origen_declaracion`` es ``prensa`` por estructura (son medios).
  - ``nombre_medio`` es el nombre fijo de la fuente (autoritativo), no lo que
    diga el feed.

Salud: cada feed es una sub-fuente independiente. El conector emite un evento
de salud por feed (``rss_fijos:<Medio>``); el pipeline lo persiste. Así, si un
feed puntual cae 2 corridas seguidas, se marca su alerta sin afectar a los otros.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable

import feedparser

from ..db.models import (
    EvidenceRecord,
    QuerySpec,
    RawItem,
    ahora_iso,
    calcular_hash_dedup,
)
from .base import Connector
from .google_news import _struct_time_a_iso

# Feeds fijos de Fase 1. Configurables por si una URL cambia. El nombre (clave)
# es el ``nombre_medio`` autoritativo que se persiste.
FEEDS_DEFAULT: dict[str, str] = {
    "Startupeable": "https://startupeable.com/feed/",
    "Contxto": "https://contxto.com/feed/",
    "LAVCA": "https://www.lavca.org/feed/",
    "LatamList": "https://latamlist.com/feed/",
    "Bloomberg Línea": "https://www.bloomberglinea.com/arc/outboundfeeds/rss/?outputType=xml",
    "Forbes México": "https://www.forbes.com.mx/feed/",
    "El CEO": "https://elceo.com/feed/",
    "Xataka México": "https://www.xataka.com.mx/tag/feeds/rss2.xml",
}


def _normalizar_texto(texto: str) -> str:
    """Minúsculas + sin acentos, para una coincidencia literal robusta."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sin_acentos.lower()


class RssFijosConnector(Connector):
    name = "rss_fijos"
    origen_declaracion_default = "prensa"

    def __init__(self, feeds: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.feeds = dict(feeds) if feeds is not None else dict(FEEDS_DEFAULT)

    # -- search ---------------------------------------------------------
    def search(self, query: QuerySpec) -> Iterable[RawItem]:
        objetivo = _normalizar_texto(query.empresa)
        items: list[RawItem] = []
        for medio, url in self.feeds.items():
            try:
                texto = self.rate_limiter.run(lambda u=url: self._get(u))
                feed = feedparser.parse(texto)
            except Exception as exc:  # un feed caído no tumba a los demás
                self.emit_health(f"{self.name}:{medio}", ok=False, detalle=str(exc)[:200])
                continue

            self.emit_health(f"{self.name}:{medio}", ok=True,
                             detalle=f"{len(feed.entries)} entradas")

            for entry in feed.entries:
                titulo = entry.get("title", "")
                resumen = entry.get("summary", "")
                # Filtro estructural: ¿el texto menciona literalmente la empresa?
                if objetivo and objetivo not in _normalizar_texto(f"{titulo} {resumen}"):
                    continue
                link = entry.get("link", "")
                meta = {
                    "titulo": titulo,
                    "link": link,
                    "medio": medio,
                    "fecha_publicacion": _struct_time_a_iso(entry.get("published_parsed")),
                    "empresa": query.empresa,
                    "tipo_evento": query.tipo_evento,
                }
                crudo = "\n".join(filter(None, [titulo, resumen, link]))
                items.append(RawItem(url=link, contenido=crudo, formato="xml", meta=meta))
        return items

    # -- fetch ----------------------------------------------------------
    def fetch(self, url: str) -> RawItem:
        """Trae el HTML de una URL puntual (crudo, sin parsear)."""
        html = self.rate_limiter.run(lambda: self._get(url))
        return RawItem(url=url, contenido=html, formato="html", meta={})

    def _get(self, url: str) -> str:
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp.text

    # -- normalize ------------------------------------------------------
    def normalize(self, raw: RawItem) -> EvidenceRecord:
        m = raw.meta
        empresa = m.get("empresa", "")
        url_fuente = m.get("link") or raw.url
        return EvidenceRecord(
            cita_textual=(m.get("titulo") or "").strip(),
            fecha_extraccion=ahora_iso(),
            url_fuente=url_fuente,
            nombre_medio=m.get("medio", "").strip(),
            empresa_mencionada=empresa,
            tipo_evento=m.get("tipo_evento", ""),
            origen_declaracion=self.origen_declaracion_default,
            hash_dedup=calcular_hash_dedup(empresa, url_fuente),
            fecha_publicacion=m.get("fecha_publicacion"),
            persona_citada=None,
            cargo=None,
            connector=self.name,
        )
