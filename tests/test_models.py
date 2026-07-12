from hd_scraper.db.models import (
    calcular_hash_dedup,
    normalizar_empresa,
    normalizar_url,
)


def test_normalizar_url_quita_query_fragmento_y_slash():
    a = normalizar_url("https://News.Example.com/nota/123/?utm=x#frag")
    b = normalizar_url("https://news.example.com/nota/123")
    assert a == b == "https://news.example.com/nota/123"


def test_normalizar_empresa_colapsa_espacios_y_baja_caso():
    assert normalizar_empresa("  Nu   Bank ") == "nu bank"


def test_hash_dedup_estable_y_sensible_a_empresa_y_url():
    h1 = calcular_hash_dedup("Nubank", "https://x.com/a?b=1")
    h2 = calcular_hash_dedup("nubank", "https://x.com/a")  # normaliza igual
    assert h1 == h2
    h3 = calcular_hash_dedup("Otra", "https://x.com/a")
    assert h1 != h3
