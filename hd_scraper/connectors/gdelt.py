"""Conector GDELT DOC 2.0 API (Fase 1, segundo conector).

GDELT DOC 2.0 (https://api.gdeltproject.org/api/v2/doc/doc) indexa notas de
prensa globales. Consultamos en modo ``ArtList`` (lista de artículos) en JSON.

Sobre la invariante "no interpreta":
  - ``tipo_evento`` viaja en la ``QuerySpec`` (lo declara el operador). No se
    infiere leyendo el artículo.
  - ``origen_declaracion`` es ``prensa`` por estructura: GDELT agrega prensa.
El conector solo extrae y reordena campos ya presentes en la respuesta.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote_plus

from ..db.models import (
    EvidenceRecord,
    QuerySpec,
    RawItem,
    ahora_iso,
    calcular_hash_dedup,
)
from .base import Connector

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


def _seendate_a_iso(seendate: str | None) -> str | None:
    """Convierte el ``seendate`` de GDELT (``YYYYMMDDTHHMMSSZ``) a ISO 8601."""
    if not seendate:
        return None
    try:
        dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


class GdeltConnector(Connector):
    name = "gdelt"
    origen_declaracion_default = "prensa"

    def __init__(self, maxrecords: int = 75, timespan: str = "3months",
                 sourcelang: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.maxrecords = max(1, min(maxrecords, 250))  # límite duro de la API
        self.timespan = timespan
        self.sourcelang = sourcelang

    # -- search ---------------------------------------------------------
    def _build_url(self, query: QuerySpec) -> str:
        # La consulta usa el nombre de empresa entre comillas para precisión y
        # términos extra opcionales. No se agregan palabras que sesguen el
        # tipo_evento (que ya viene declarado en la QuerySpec).
        q = f'"{query.empresa}"'
        if query.terminos:
            q += f" {query.terminos}"
        if self.sourcelang:
            q += f" sourcelang:{self.sourcelang}"
        url = (
            f"{GDELT_DOC_API}?query={quote_plus(q)}"
            f"&mode=ArtList&format=json&maxrecords={self.maxrecords}"
            f"&timespan={quote_plus(self.timespan)}&sort=DateDesc"
        )
        return url

    def search(self, query: QuerySpec) -> Iterable[RawItem]:
        url = self._build_url(query)
        texto = self.rate_limiter.run(lambda: self._get(url))
        data = self._parse_json(texto)
        items: list[RawItem] = []
        for art in data.get("articles", []):
            link = art.get("url", "")
            meta = {
                "titulo": art.get("title", ""),
                "link": link,
                "fuente": art.get("domain"),
                "fecha_publicacion": _seendate_a_iso(art.get("seendate")),
                "idioma": art.get("language"),
                # Contexto estructural de la consulta (no del contenido):
                "empresa": query.empresa,
                "tipo_evento": query.tipo_evento,
            }
            crudo = json.dumps(art, ensure_ascii=False)
            items.append(RawItem(url=link, contenido=crudo, formato="json", meta=meta))
        return items

    @staticmethod
    def _parse_json(texto: str) -> dict:
        # GDELT puede devolver cuerpo vacío cuando no hay resultados.
        texto = (texto or "").strip()
        if not texto:
            return {"articles": []}
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            # Respuesta no-JSON (p. ej. rate limit HTML): sin artículos.
            return {"articles": []}

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
            nombre_medio=(m.get("fuente") or "GDELT").strip(),
            empresa_mencionada=empresa,
            tipo_evento=m.get("tipo_evento", ""),
            origen_declaracion=self.origen_declaracion_default,
            hash_dedup=calcular_hash_dedup(empresa, url_fuente),
            fecha_publicacion=m.get("fecha_publicacion"),
            persona_citada=None,   # GDELT ArtList no la provee de forma estructural
            cargo=None,
            connector=self.name,
        )
