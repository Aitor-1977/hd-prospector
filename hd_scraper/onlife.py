"""Capa 7 — Motor Onlife: observación conductual de organizaciones.

Captura señales de COMPORTAMIENTO organizacional desde espacios digitales
donde la vida operativa realmente ocurre: repositorios, foros técnicos,
changelogs, comunidades. Complementa al Drift Narrativo (Capa 6) que
observa el discurso — aquí se observa la ACCIÓN.

Fuentes implementadas:
  - GitHub: actividad en repositorios públicos (commits, repos, lenguajes)
  - Hacker News: menciones y discusión en la comunidad tech
  - Blog/Changelog: publicaciones recientes vía RSS

Principios:
  - Observa comportamiento, NO interpreta.
  - Cada señal es un HECHO verificable con URL fuente.
  - Determinista: misma entrada → misma salida.
  - Nunca genera hipótesis ni calcula Deuda Cultural™.

Tipos de señal (cerrados):
  - actividad_tech: actividad en repositorios, código, releases
  - lanzamiento: productos, versiones, features publicadas
  - comunidad: menciones, discusión en foros y comunidades
  - contratacion: vacantes, cambios de equipo (ya cubierto por job_boards)
  - presencia: visibilidad en plataformas (Product Hunt, conferencias)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Callable, Optional
from xml.etree import ElementTree

from .db.database import get_db
from .db.models import ahora_iso

logger = logging.getLogger("hd_scraper.onlife")

TIPOS_SENAL = (
    "actividad_tech", "lanzamiento", "comunidad", "contratacion", "presencia",
)

FUENTES = ("github", "hackernews", "blog_changelog")


def _hash_senal(org: str, fuente: str, tipo: str, contenido: str) -> str:
    return hashlib.sha256(
        f"{org}|{fuente}|{tipo}|{contenido}".encode()
    ).hexdigest()[:32]


# ── GitHub (API pública, sin auth) ──────────────────────────────────────────

_GH_API = "https://api.github.com"


def _github_org_url(org_github: str) -> str:
    return f"{_GH_API}/orgs/{org_github}/repos?sort=updated&per_page=30"


def _github_user_url(org_github: str) -> str:
    return f"{_GH_API}/users/{org_github}/repos?sort=updated&per_page=30"


def observar_github(
    org_nombre: str,
    org_github: str,
    http_get: Callable[[str], str],
) -> list[dict]:
    """Observa repositorios públicos de una organización en GitHub."""
    señales: list[dict] = []
    repos_data = None

    for url_fn in (_github_org_url, _github_user_url):
        try:
            raw = http_get(url_fn(org_github))
            repos_data = json.loads(raw)
            if isinstance(repos_data, list):
                break
        except Exception:
            continue

    if not repos_data or not isinstance(repos_data, list):
        logger.info("Onlife/GitHub: sin repos para %s", org_github)
        return señales

    total_repos = len(repos_data)
    total_stars = sum(r.get("stargazers_count", 0) for r in repos_data)
    lenguajes = {}
    repos_recientes = []

    for repo in repos_data:
        lang = repo.get("language")
        if lang:
            lenguajes[lang] = lenguajes.get(lang, 0) + 1

        pushed = repo.get("pushed_at", "")
        if pushed:
            repos_recientes.append({
                "nombre": repo.get("name", ""),
                "descripcion": (repo.get("description") or "")[:200],
                "lenguaje": lang or "",
                "estrellas": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "ultimo_push": pushed,
                "url": repo.get("html_url", ""),
            })

    repos_recientes.sort(key=lambda r: r["ultimo_push"], reverse=True)

    señales.append({
        "org_nombre": org_nombre,
        "fuente": "github",
        "tipo_senal": "actividad_tech",
        "url": f"https://github.com/{org_github}",
        "descripcion": (
            f"GitHub: {total_repos} repos públicos, "
            f"{total_stars} estrellas totales, "
            f"lenguajes: {', '.join(sorted(lenguajes, key=lenguajes.get, reverse=True)[:5])}"
        ),
        "dato_json": json.dumps({
            "total_repos": total_repos,
            "total_stars": total_stars,
            "lenguajes": lenguajes,
            "repos_recientes": repos_recientes[:10],
        }, ensure_ascii=False),
    })

    for repo in repos_recientes[:3]:
        if not repo["nombre"]:
            continue
        señales.append({
            "org_nombre": org_nombre,
            "fuente": "github",
            "tipo_senal": "actividad_tech",
            "url": repo["url"],
            "descripcion": (
                f"Repo activo: {repo['nombre']} "
                f"({repo['lenguaje'] or 'sin lenguaje'}, "
                f"★{repo['estrellas']}) — "
                f"último push {repo['ultimo_push'][:10]}"
            ),
            "dato_json": json.dumps(repo, ensure_ascii=False),
        })

    return señales


# ── Hacker News (Algolia API, libre) ────────────────────────────────────────

_HN_SEARCH = "https://hn.algolia.com/api/v1/search"


def observar_hackernews(
    org_nombre: str,
    query: str,
    http_get: Callable[[str], str],
) -> list[dict]:
    """Busca menciones de la organización en Hacker News."""
    señales: list[dict] = []
    q = re.sub(r"[^\w\s]", "", query).strip()
    if not q:
        return señales

    url = f"{_HN_SEARCH}?query={q}&tags=story&hitsPerPage=10"
    try:
        raw = http_get(url)
        data = json.loads(raw)
    except Exception as exc:
        logger.info("Onlife/HN: error buscando %s — %s", q, exc)
        return señales

    hits = data.get("hits", [])
    if not hits:
        return señales

    total = data.get("nbHits", len(hits))
    señales.append({
        "org_nombre": org_nombre,
        "fuente": "hackernews",
        "tipo_senal": "comunidad",
        "url": f"https://hn.algolia.com/?q={q}",
        "descripcion": f"Hacker News: {total} menciones encontradas para «{q}»",
        "dato_json": json.dumps({"total_menciones": total, "query": q},
                                ensure_ascii=False),
    })

    for hit in hits[:5]:
        title = hit.get("title", "")
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        points = hit.get("points", 0)
        comments = hit.get("num_comments", 0)
        created = hit.get("created_at", "")

        señales.append({
            "org_nombre": org_nombre,
            "fuente": "hackernews",
            "tipo_senal": "comunidad",
            "url": story_url,
            "descripcion": (
                f"HN: «{title[:150]}» — "
                f"{points} pts, {comments} comentarios"
            ),
            "dato_json": json.dumps({
                "titulo": title,
                "puntos": points,
                "comentarios": comments,
                "autor": hit.get("author", ""),
                "fecha": created,
                "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
            }, ensure_ascii=False),
        })

    return señales


# ── Blog / Changelog (RSS) ─────────────────────────────────────────────────

def observar_blog(
    org_nombre: str,
    feed_url: str,
    http_get: Callable[[str], str],
) -> list[dict]:
    """Lee un feed RSS/Atom de blog o changelog y extrae señales."""
    señales: list[dict] = []
    if not feed_url:
        return señales

    try:
        raw = http_get(feed_url)
    except Exception as exc:
        logger.info("Onlife/Blog: error leyendo %s — %s", feed_url, exc)
        return señales

    items = _parsear_feed_simple(raw)
    if not items:
        return señales

    señales.append({
        "org_nombre": org_nombre,
        "fuente": "blog_changelog",
        "tipo_senal": "lanzamiento",
        "url": feed_url,
        "descripcion": f"Blog/Changelog: {len(items)} publicaciones recientes",
        "dato_json": json.dumps({"total_items": len(items), "feed_url": feed_url},
                                ensure_ascii=False),
    })

    for item in items[:5]:
        tipo = "lanzamiento" if _es_changelog(item["titulo"]) else "presencia"
        señales.append({
            "org_nombre": org_nombre,
            "fuente": "blog_changelog",
            "tipo_senal": tipo,
            "url": item["url"],
            "descripcion": f"Post: «{item['titulo'][:150]}»",
            "dato_json": json.dumps(item, ensure_ascii=False),
        })

    return señales


def _parsear_feed_simple(xml_text: str) -> list[dict]:
    """Parser RSS/Atom minimalista sin dependencias externas."""
    items = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item_el in root.iter("item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        pub = (item_el.findtext("pubDate") or "").strip()
        if title and link:
            items.append({"titulo": title, "url": link, "fecha": pub})

    if not items:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = (link_el.get("href", "") if link_el is not None else "").strip()
            pub = (entry.findtext("{http://www.w3.org/2005/Atom}published")
                   or entry.findtext("{http://www.w3.org/2005/Atom}updated")
                   or "").strip()
            if title and link:
                items.append({"titulo": title, "url": link, "fecha": pub})

    return items[:20]


_CHANGELOG_MARKERS = (
    "release", "v1.", "v2.", "v3.", "v0.", "changelog", "update",
    "lanzamiento", "versión", "version", "nueva función", "new feature",
    "mejora", "improvement", "fix", "hotfix", "patch",
)


def _es_changelog(titulo: str) -> bool:
    t = titulo.lower()
    return any(m in t for m in _CHANGELOG_MARKERS)


# ── Orquestador ─────────────────────────────────────────────────────────────

def observar(
    org_nombre: str,
    http_get: Callable[[str], str],
    org_github: Optional[str] = None,
    feed_url: Optional[str] = None,
) -> list[dict]:
    """Ejecuta todas las fuentes onlife disponibles para una organización.

    Retorna la lista combinada de señales (sin persistir).
    """
    señales: list[dict] = []

    if org_github:
        señales.extend(observar_github(org_nombre, org_github, http_get))

    señales.extend(observar_hackernews(org_nombre, org_nombre, http_get))

    if feed_url:
        señales.extend(observar_blog(org_nombre, feed_url, http_get))

    return señales


# ── Persistencia ────────────────────────────────────────────────────────────

def persistir_señales(señales: list[dict]) -> int:
    """Guarda señales onlife en la base de datos. Retorna nuevas insertadas."""
    db = get_db()
    ahora = ahora_iso()
    nuevas = 0
    for s in señales:
        dedup = _hash_senal(
            s["org_nombre"], s["fuente"], s["tipo_senal"],
            s.get("url", "") or s.get("descripcion", ""),
        )
        cur = db.execute(
            """INSERT INTO onlife_signals
                 (org_nombre, fuente, tipo_senal, dato_json, url,
                  descripcion, fecha_observacion, hash_dedup, creado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (hash_dedup) DO NOTHING""",
            (s["org_nombre"], s["fuente"], s["tipo_senal"],
             s.get("dato_json", "{}"), s.get("url", ""),
             s["descripcion"], ahora, dedup, ahora),
        )
        if getattr(cur, "rowcount", 0):
            nuevas += 1
    return nuevas


def obtener_perfil(org_nombre: str) -> dict:
    """Devuelve el perfil onlife completo de una organización."""
    db = get_db()
    señales = db.fetch_all(
        "SELECT * FROM onlife_signals "
        "WHERE org_nombre = ? ORDER BY fecha_observacion DESC",
        (org_nombre,),
    )
    por_fuente: dict[str, list] = {}
    for s in señales:
        row = dict(s)
        f = row.get("fuente", "desconocida")
        por_fuente.setdefault(f, []).append(row)

    return {
        "org_nombre": org_nombre,
        "total_señales": len(señales),
        "por_fuente": {f: len(sigs) for f, sigs in por_fuente.items()},
        "señales": [dict(s) for s in señales],
        "fuentes_observadas": list(por_fuente.keys()),
    }
