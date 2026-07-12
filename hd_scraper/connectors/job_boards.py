"""Conector de job boards públicos JSON (Fase 1, cuarto conector).

Consulta las vacantes públicas de una empresa por su *slug* en tres
plataformas: Greenhouse, Lever y Ashby. Cada plataforma expone un endpoint JSON
sin autenticación.

Sobre la invariante "no interpreta" (aquí es especialmente clara):
  - ``tipo_evento`` = ``contratacion`` por ESTRUCTURA: una vacante publicada es,
    por definición, una señal de contratación. No se lee la vacante para
    decidirlo; lo determina el tipo de fuente.
  - ``origen_declaracion`` = ``operador`` por ESTRUCTURA: la vacante la publica
    la propia empresa (el operador), no la prensa ni un usuario.
  - ``nombre_medio`` = nombre de la plataforma (Greenhouse/Lever/Ashby).

Salud por plataforma: cada una es una sub-fuente (``job_boards:<Plataforma>``).
Un 404 significa "ese slug no existe en esa plataforma" y NO cuenta como fallo
de salud; un 5xx / error de red sí.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Iterable

import httpx
from dateutil import parser as date_parser

from ..db.models import (
    EvidenceRecord,
    QuerySpec,
    RawItem,
    ahora_iso,
    calcular_hash_dedup,
)
from .base import Connector


def _iso_or_none(valor: str | None) -> str | None:
    if not valor:
        return None
    try:
        return date_parser.isoparse(valor).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _ms_a_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


# --- Adaptadores por plataforma: raw JSON -> lista de puestos normalizados ---

def _parse_greenhouse(data: dict) -> list[dict]:
    out = []
    for j in data.get("jobs", []) if isinstance(data, dict) else []:
        out.append({
            "titulo": j.get("title", ""),
            "url": j.get("absolute_url", ""),
            "fecha_publicacion": _iso_or_none(j.get("updated_at")),
        })
    return out


def _parse_lever(data: list) -> list[dict]:
    out = []
    for j in data if isinstance(data, list) else []:
        out.append({
            "titulo": j.get("text", ""),
            "url": j.get("hostedUrl", ""),
            "fecha_publicacion": _ms_a_iso(j.get("createdAt")),
        })
    return out


def _parse_ashby(data: dict) -> list[dict]:
    out = []
    for j in data.get("jobs", []) if isinstance(data, dict) else []:
        out.append({
            "titulo": j.get("title", ""),
            "url": j.get("jobUrl") or j.get("applyUrl") or "",
            "fecha_publicacion": _iso_or_none(j.get("publishedAt") or j.get("updatedAt")),
        })
    return out


# Plataforma -> (plantilla de URL por slug, parser)
PLATFORMS: dict[str, tuple[str, Callable]] = {
    "Greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", _parse_greenhouse),
    "Lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", _parse_lever),
    "Ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", _parse_ashby),
}


class JobBoardsConnector(Connector):
    name = "job_boards"
    origen_declaracion_default = "operador"
    requires_slug = True

    #: tipo_evento estructural: una vacante es una señal de contratación.
    tipo_evento_estructural = "contratacion"

    def __init__(self, platforms: dict | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.platforms = dict(platforms) if platforms is not None else dict(PLATFORMS)

    # -- search ---------------------------------------------------------
    def search(self, query: QuerySpec) -> Iterable[RawItem]:
        if not query.slug:
            # Sin slug no hay a quién consultar; no es un fallo de fuente.
            return []
        items: list[RawItem] = []
        for plataforma, (tmpl, parser) in self.platforms.items():
            url = tmpl.format(slug=query.slug)
            try:
                texto = self.rate_limiter.run(lambda u=url: self._get(u))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue  # ese slug no está en esta plataforma: no es fallo
                self.emit_health(f"{self.name}:{plataforma}", ok=False,
                                 detalle=f"HTTP {exc.response.status_code}")
                continue
            except Exception as exc:
                self.emit_health(f"{self.name}:{plataforma}", ok=False, detalle=str(exc)[:200])
                continue

            try:
                data = json.loads(texto) if texto.strip() else None
            except json.JSONDecodeError:
                self.emit_health(f"{self.name}:{plataforma}", ok=False, detalle="json invalido")
                continue

            puestos = parser(data)
            self.emit_health(f"{self.name}:{plataforma}", ok=True,
                             detalle=f"{len(puestos)} puestos")
            for p in puestos:
                meta = {
                    "titulo": p["titulo"],
                    "link": p["url"],
                    "medio": plataforma,
                    "fecha_publicacion": p["fecha_publicacion"],
                    "empresa": query.empresa,
                    # tipo_evento estructural, no del contenido:
                    "tipo_evento": self.tipo_evento_estructural,
                }
                crudo = json.dumps(p, ensure_ascii=False)
                items.append(RawItem(url=p["url"], contenido=crudo, formato="json", meta=meta))
        return items

    # -- fetch ----------------------------------------------------------
    def fetch(self, url: str) -> RawItem:
        """Trae el crudo de una URL puntual (JSON/HTML sin parsear)."""
        texto = self.rate_limiter.run(lambda: self._get(url))
        return RawItem(url=url, contenido=texto, formato="json", meta={})

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
            tipo_evento=m.get("tipo_evento", self.tipo_evento_estructural),
            origen_declaracion=self.origen_declaracion_default,
            hash_dedup=calcular_hash_dedup(empresa, url_fuente),
            fecha_publicacion=m.get("fecha_publicacion"),
            persona_citada=None,
            cargo=None,
            connector=self.name,
        )
