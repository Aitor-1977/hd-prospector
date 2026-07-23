"""Capa 7 — Motor Onlife: tests de observación conductual."""
import json

import pytest

from hd_scraper.onlife import (
    FUENTES,
    TIPOS_SENAL,
    _es_changelog,
    _hash_senal,
    _parsear_feed_simple,
    observar,
    observar_blog,
    observar_github,
    observar_hackernews,
    obtener_perfil,
    persistir_señales,
)


# ── utilidades internas ──────────────────────────────────────────────────────

def test_hash_senal_determinista():
    h1 = _hash_senal("Acme", "github", "actividad_tech", "https://github.com/acme")
    h2 = _hash_senal("Acme", "github", "actividad_tech", "https://github.com/acme")
    assert h1 == h2


def test_hash_senal_diferente():
    h1 = _hash_senal("Acme", "github", "actividad_tech", "url1")
    h2 = _hash_senal("Acme", "github", "actividad_tech", "url2")
    assert h1 != h2


def test_es_changelog_positivo():
    assert _es_changelog("Release v2.3.0")
    assert _es_changelog("Changelog: new features")
    assert _es_changelog("Version 1.0 update")
    assert _es_changelog("Nueva función: dashboard")
    assert _es_changelog("Hotfix for authentication")


def test_es_changelog_negativo():
    assert not _es_changelog("Reflexiones sobre el mercado")
    assert not _es_changelog("Entrevista con el CEO")
    assert not _es_changelog("Nuestra historia como empresa")


# ── parser RSS simple ───────────────────────────────────────────────────────

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Blog</title>
<item><title>Release v3.0</title><link>https://blog.acme.com/v3</link><pubDate>Mon, 01 Jan 2025</pubDate></item>
<item><title>New dashboard</title><link>https://blog.acme.com/dashboard</link><pubDate>Sun, 15 Dec 2024</pubDate></item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Blog</title>
<entry><title>Atom Post</title><link href="https://blog.acme.com/atom1"/><published>2025-01-01T00:00:00Z</published></entry>
</feed>"""


def test_parsear_rss():
    items = _parsear_feed_simple(RSS_SAMPLE)
    assert len(items) == 2
    assert items[0]["titulo"] == "Release v3.0"
    assert items[0]["url"] == "https://blog.acme.com/v3"


def test_parsear_atom():
    items = _parsear_feed_simple(ATOM_SAMPLE)
    assert len(items) == 1
    assert items[0]["titulo"] == "Atom Post"
    assert items[0]["url"] == "https://blog.acme.com/atom1"


def test_parsear_feed_invalido():
    assert _parsear_feed_simple("not xml at all") == []
    assert _parsear_feed_simple("") == []


def test_parsear_feed_limita_20():
    items_xml = "".join(
        f'<item><title>Post {i}</title><link>https://x.com/{i}</link></item>'
        for i in range(30)
    )
    rss = f'<rss><channel>{items_xml}</channel></rss>'
    items = _parsear_feed_simple(rss)
    assert len(items) <= 20


# ── observar_github ──────────────────────────────────────────────────────────

GITHUB_REPOS = json.dumps([
    {
        "name": "api-server",
        "description": "Main API server",
        "language": "Python",
        "stargazers_count": 120,
        "forks_count": 15,
        "pushed_at": "2025-06-15T10:00:00Z",
        "html_url": "https://github.com/acme/api-server",
    },
    {
        "name": "frontend",
        "description": "Web frontend",
        "language": "TypeScript",
        "stargazers_count": 80,
        "forks_count": 10,
        "pushed_at": "2025-06-10T10:00:00Z",
        "html_url": "https://github.com/acme/frontend",
    },
    {
        "name": "docs",
        "description": "Documentation",
        "language": None,
        "stargazers_count": 5,
        "forks_count": 2,
        "pushed_at": "2025-05-01T10:00:00Z",
        "html_url": "https://github.com/acme/docs",
    },
])


def test_observar_github_extrae_señales():
    señales = observar_github("Acme Corp", "acme", lambda u: GITHUB_REPOS)
    assert len(señales) >= 1
    resumen = señales[0]
    assert resumen["fuente"] == "github"
    assert resumen["tipo_senal"] == "actividad_tech"
    assert "3 repos" in resumen["descripcion"]
    assert "205 estrellas" in resumen["descripcion"]
    data = json.loads(resumen["dato_json"])
    assert data["total_repos"] == 3
    assert "Python" in data["lenguajes"]


def test_observar_github_repos_recientes():
    señales = observar_github("Acme Corp", "acme", lambda u: GITHUB_REPOS)
    repos_señales = [s for s in señales if "Repo activo" in s["descripcion"]]
    assert len(repos_señales) <= 3
    assert all(s["fuente"] == "github" for s in repos_señales)


def test_observar_github_sin_repos():
    señales = observar_github("NoOrg", "noorg", lambda u: "[]")
    assert señales == []


def test_observar_github_error_red():
    def fail(url):
        raise Exception("connection refused")
    señales = observar_github("Acme", "acme", fail)
    assert señales == []


def test_observar_github_fallback_user():
    calls = []
    def track_get(url):
        calls.append(url)
        if "/orgs/" in url:
            raise Exception("404 Not Found")
        return GITHUB_REPOS
    señales = observar_github("Acme", "acme", track_get)
    assert len(calls) == 2
    assert "/users/" in calls[1]
    assert len(señales) >= 1


# ── observar_hackernews ──────────────────────────────────────────────────────

HN_RESPONSE = json.dumps({
    "hits": [
        {
            "title": "Acme Corp raises $50M",
            "url": "https://techcrunch.com/acme-50m",
            "points": 250,
            "num_comments": 45,
            "author": "techcrunch",
            "created_at": "2025-06-01T10:00:00Z",
            "objectID": "12345",
        },
        {
            "title": "Show HN: Acme's new API",
            "url": "https://acme.com/api",
            "points": 120,
            "num_comments": 30,
            "author": "acme_dev",
            "created_at": "2025-05-15T10:00:00Z",
            "objectID": "12346",
        },
    ],
    "nbHits": 42,
})


def test_observar_hackernews_extrae_señales():
    señales = observar_hackernews("Acme Corp", "Acme Corp", lambda u: HN_RESPONSE)
    assert len(señales) >= 1
    resumen = señales[0]
    assert resumen["fuente"] == "hackernews"
    assert resumen["tipo_senal"] == "comunidad"
    assert "42 menciones" in resumen["descripcion"]


def test_observar_hackernews_historias():
    señales = observar_hackernews("Acme Corp", "Acme Corp", lambda u: HN_RESPONSE)
    historias = [s for s in señales if "HN:" in s["descripcion"]]
    assert len(historias) == 2
    assert "250 pts" in historias[0]["descripcion"]
    assert "45 comentarios" in historias[0]["descripcion"]


def test_observar_hackernews_sin_resultados():
    resp = json.dumps({"hits": [], "nbHits": 0})
    señales = observar_hackernews("NoOrg", "NoOrg", lambda u: resp)
    assert señales == []


def test_observar_hackernews_error():
    def fail(url):
        raise Exception("network error")
    señales = observar_hackernews("Acme", "Acme", fail)
    assert señales == []


def test_observar_hackernews_query_limpieza():
    señales = observar_hackernews("Acme!", "Acme! Corp.", lambda u: HN_RESPONSE)
    assert len(señales) >= 1


def test_observar_hackernews_query_vacio():
    señales = observar_hackernews("!!!", "!!!", lambda u: "{}")
    assert señales == []


# ── observar_blog ────────────────────────────────────────────────────────────

def test_observar_blog_rss():
    señales = observar_blog("Acme Corp", "https://blog.acme.com/rss", lambda u: RSS_SAMPLE)
    assert len(señales) >= 1
    resumen = señales[0]
    assert resumen["fuente"] == "blog_changelog"
    assert "2 publicaciones" in resumen["descripcion"]


def test_observar_blog_detecta_changelog():
    señales = observar_blog("Acme Corp", "https://blog.acme.com/rss", lambda u: RSS_SAMPLE)
    release_señales = [s for s in señales if s["tipo_senal"] == "lanzamiento" and "Release" in s["descripcion"]]
    assert len(release_señales) >= 1


def test_observar_blog_sin_feed():
    señales = observar_blog("Acme", "", lambda u: "")
    assert señales == []


def test_observar_blog_feed_invalido():
    señales = observar_blog("Acme", "https://acme.com/rss", lambda u: "not xml")
    assert señales == []


def test_observar_blog_error():
    def fail(url):
        raise Exception("timeout")
    señales = observar_blog("Acme", "https://x.com/rss", fail)
    assert señales == []


# ── observar (orquestador) ───────────────────────────────────────────────────

def test_observar_solo_hackernews():
    señales = observar("Acme Corp", lambda u: HN_RESPONSE)
    assert len(señales) >= 1
    assert all(s["fuente"] == "hackernews" for s in señales)


def test_observar_github_y_hackernews():
    def http_get(url):
        if "github.com" in url:
            return GITHUB_REPOS
        return HN_RESPONSE

    señales = observar("Acme Corp", http_get, org_github="acme")
    fuentes = {s["fuente"] for s in señales}
    assert "github" in fuentes
    assert "hackernews" in fuentes


def test_observar_todas_las_fuentes():
    def http_get(url):
        if "github.com" in url:
            return GITHUB_REPOS
        if "hn.algolia" in url:
            return HN_RESPONSE
        return RSS_SAMPLE

    señales = observar("Acme Corp", http_get, org_github="acme", feed_url="https://blog.acme.com/rss")
    fuentes = {s["fuente"] for s in señales}
    assert "github" in fuentes
    assert "hackernews" in fuentes
    assert "blog_changelog" in fuentes


def test_observar_señales_tienen_campos_requeridos():
    señales = observar("Acme Corp", lambda u: HN_RESPONSE)
    for s in señales:
        assert "org_nombre" in s
        assert "fuente" in s
        assert "tipo_senal" in s
        assert "descripcion" in s
        assert s["org_nombre"] == "Acme Corp"
        assert s["tipo_senal"] in TIPOS_SENAL
        assert s["fuente"] in FUENTES


# ── persistir_señales (integración con DB) ───────────────────────────────────

def test_persistir_señales_nuevas(db, monkeypatch):
    monkeypatch.setattr("hd_scraper.onlife.get_db", lambda: db)
    señales = [{
        "org_nombre": "Acme Corp",
        "fuente": "github",
        "tipo_senal": "actividad_tech",
        "url": "https://github.com/acme",
        "descripcion": "GitHub: 10 repos",
        "dato_json": "{}",
    }]
    nuevas = persistir_señales(señales)
    assert nuevas == 1


def test_persistir_señales_dedup(db, monkeypatch):
    monkeypatch.setattr("hd_scraper.onlife.get_db", lambda: db)
    s = {
        "org_nombre": "Acme Corp",
        "fuente": "hackernews",
        "tipo_senal": "comunidad",
        "url": "https://news.ycombinator.com/item?id=123",
        "descripcion": "HN mention",
        "dato_json": "{}",
    }
    assert persistir_señales([s]) == 1
    assert persistir_señales([s]) == 0


def test_persistir_señales_vacia(db, monkeypatch):
    monkeypatch.setattr("hd_scraper.onlife.get_db", lambda: db)
    assert persistir_señales([]) == 0


def test_persistir_multiples_señales(db, monkeypatch):
    monkeypatch.setattr("hd_scraper.onlife.get_db", lambda: db)
    señales = [
        {"org_nombre": "Acme", "fuente": "github", "tipo_senal": "actividad_tech",
         "url": "https://github.com/acme", "descripcion": "GH", "dato_json": "{}"},
        {"org_nombre": "Acme", "fuente": "hackernews", "tipo_senal": "comunidad",
         "url": "https://hn.com/1", "descripcion": "HN", "dato_json": "{}"},
    ]
    assert persistir_señales(señales) == 2


# ── obtener_perfil ───────────────────────────────────────────────────────────

def test_obtener_perfil_vacio(db, monkeypatch):
    monkeypatch.setattr("hd_scraper.onlife.get_db", lambda: db)
    perfil = obtener_perfil("NoExiste")
    assert perfil["total_señales"] == 0
    assert perfil["org_nombre"] == "NoExiste"
    assert perfil["fuentes_observadas"] == []


def test_obtener_perfil_con_datos(db, monkeypatch):
    monkeypatch.setattr("hd_scraper.onlife.get_db", lambda: db)
    señales = [
        {"org_nombre": "Acme", "fuente": "github", "tipo_senal": "actividad_tech",
         "url": "https://github.com/acme", "descripcion": "GH", "dato_json": "{}"},
        {"org_nombre": "Acme", "fuente": "hackernews", "tipo_senal": "comunidad",
         "url": "https://hn.com/1", "descripcion": "HN", "dato_json": "{}"},
    ]
    persistir_señales(señales)
    perfil = obtener_perfil("Acme")
    assert perfil["total_señales"] == 2
    assert "github" in perfil["fuentes_observadas"]
    assert "hackernews" in perfil["fuentes_observadas"]
    assert perfil["por_fuente"]["github"] == 1
    assert perfil["por_fuente"]["hackernews"] == 1


# ── constantes ───────────────────────────────────────────────────────────────

def test_tipos_senal_completos():
    esperados = {"actividad_tech", "lanzamiento", "comunidad", "contratacion", "presencia"}
    assert set(TIPOS_SENAL) == esperados


def test_fuentes_completas():
    esperadas = {"github", "hackernews", "blog_changelog"}
    assert set(FUENTES) == esperadas
