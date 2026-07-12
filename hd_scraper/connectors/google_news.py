"""Conector Google News RSS (Fase 1, primer conector de punta a punta).

Descubre notas de prensa que mencionan a una empresa a través del feed RSS de
búsqueda de Google News.

Sobre la invariante "no interpreta":
  - ``tipo_evento`` NO se infiere leyendo la nota. Viaja en la ``QuerySpec``:
    lo declara el operador al lanzar la corrida (p. ej. "buscar señales de
    'ronda' de la empresa X"). Es una propiedad estructural de la consulta.
  - ``origen_declaracion`` es ``prensa`` por estructura: la fuente es un feed
    de noticias, no el operador ni el usuario.
El conector solo extrae y reordena campos ya presentes en el RSS.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote_plus

import feedparser

from ..db.models import (
    EvidenceRecord,
    QuerySpec,
    RawItem,
    ahora_iso,
    calcular_hash_dedup,
)
from .base import Connector

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def _struct_time_a_iso(parsed: time.struct_time | None) -> str | None:
    """Convierte el ``published_parsed`` de feedparser (UTC) a ISO 8601."""
    if not parsed:
        return None
    try:
        dt = datetime(*parsed[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


class GoogleNewsConnector(Connector):
    name = "google_news"
    origen_declaracion_default = "prensa"

    def __init__(self, hl: str = "es-419", gl: str = "MX", ceid: str = "MX:es",
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self.hl = hl
        self.gl = gl
        self.ceid = ceid

    # -- search ---------------------------------------------------------
    def _build_url(self, query: QuerySpec) -> str:
        # La consulta a Google News se arma con el nombre de empresa (entre
        # comillas para precisión) y términos extra opcionales. No se agregan
        # palabras que sesguen la interpretación del tipo_evento.
        q = f'"{query.empresa}"'
        if query.terminos:
            q += f" {query.terminos}"
        return (
            f"{GOOGLE_NEWS_RSS}?q={quote_plus(q)}"
            f"&hl={self.hl}&gl={self.gl}&ceid={quote_plus(self.ceid)}"
        )

    def search(self, query: QuerySpec) -> Iterable[RawItem]:
        url = self._build_url(query)
        resp = self.rate_limiter.run(lambda: self._get(url))
        feed = feedparser.parse(resp)
        items: list[RawItem] = []
        for entry in feed.entries:
            fuente = None
            src = entry.get("source")
            if isinstance(src, dict):
                fuente = src.get("title")
            elif src is not None:
                fuente = getattr(src, "title", None) or str(src)

            meta = {
                "titulo": entry.get("title", ""),
                "link": entry.get("link", ""),
                "fuente": fuente,
                "fecha_publicacion": _struct_time_a_iso(entry.get("published_parsed")),
                # Contexto estructural de la consulta (no del contenido):
                "empresa": query.empresa,
                "tipo_evento": query.tipo_evento,
            }
            # El crudo retenido es la entry serializada (JSON), vinculada por hash.
            crudo = json.dumps(
                {k: entry.get(k) for k in ("title", "link", "published", "summary")},
                ensure_ascii=False,
            )
            items.append(RawItem(url=meta["link"], contenido=crudo, formato="json", meta=meta))
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
            nombre_medio=(m.get("fuente") or "Google News").strip(),
            empresa_mencionada=empresa,
            tipo_evento=m.get("tipo_evento", ""),
            origen_declaracion=self.origen_declaracion_default,
            hash_dedup=calcular_hash_dedup(empresa, url_fuente),
            fecha_publicacion=m.get("fecha_publicacion"),
            persona_citada=None,   # el RSS no la provee de forma estructural
            cargo=None,
            connector=self.name,
        )
