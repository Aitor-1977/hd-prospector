"""Conectores de ingesta Capa 0: mapeo, parseo de subtítulos, agrupado y resiliencia.

Todo con la red inyectada; ninguna prueba llama a Apify, yt-dlp ni al webhook real.
"""
from hd_scraper.ingesta import noticias, webhook, youtube

_NOOP = lambda s: None  # noqa: E731


# ── webhook: resiliencia (reintentos + backoff) ──────────────────────────────

def test_webhook_reintenta_y_luego_exito():
    intentos = {"n": 0}
    esperas = []

    def post(url, json, headers, timeout):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise RuntimeError("503 temporal")
        return {"ok": True, "senales_detectadas": 1}

    r = webhook.enviar({"texto": "x"}, url="http://w", token="t", http_post=post,
                       max_retries=4, backoff_base=1.0, sleep=esperas.append)
    assert r["ok"] is True and intentos["n"] == 3
    assert esperas == [1.0, 2.0]          # backoff exponencial antes de cada reintento


def test_webhook_agota_reintentos_y_lanza():
    def post(url, json, headers, timeout):
        raise RuntimeError("caída total")
    try:
        webhook.enviar({"texto": "x"}, http_post=post, max_retries=3, sleep=_NOOP)
        assert False, "debió lanzar"
    except RuntimeError as e:
        assert "3 intentos" in str(e)


def test_webhook_incluye_token_en_headers():
    capturado = {}

    def post(url, json, headers, timeout):
        capturado.update(headers)
        return {}
    webhook.enviar({"t": 1}, token="secreto", http_post=post, max_retries=1)
    assert capturado.get("X-Ingest-Token") == "secreto"


# ── Noticias (RSS gratis): mapeo de entrada → payload ────────────────────────

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Kavak recauda una ronda Serie E - Bloomberg Línea</title>
    <link>https://news.example/kavak</link>
    <description>&lt;p&gt;La fintech de autos &lt;b&gt;levanta capital&lt;/b&gt;.&lt;/p&gt;</description>
    <source url="https://bloomberglinea.com">Bloomberg Línea</source>
  </item>
  <item>
    <title></title>
    <link>https://news.example/vacio</link>
  </item>
  <item>
    <title>Clip reestructura su equipo comercial - El Financiero</title>
    <link>https://news.example/clip</link>
    <source url="https://elfinanciero.com">El Financiero</source>
  </item>
</channel></rss>"""


def test_noticias_parse_feed_mapea_y_limpia_html():
    payloads = noticias.parse_feed(RSS)
    # La entrada sin título se omite; quedan 2.
    assert len(payloads) == 2
    p0 = payloads[0]
    assert p0["url"] == "https://news.example/kavak"
    assert p0["org_name"] == "Bloomberg Línea"
    assert "levanta capital" in p0["texto"] and "<b>" not in p0["texto"]


def test_noticias_correr_por_query_postea_cada_nota():
    enviados = []
    capturado = {}

    def get(url):
        capturado["url"] = url
        return RSS

    res = noticias.correr(query="fintech México ronda",
                          http_get=get,
                          enviar_fn=lambda p: (enviados.append(p) or {"senales_detectadas": 1}))
    assert "news.google.com/rss/search" in capturado["url"]   # Google News RSS (gratis)
    assert res["items"] == 2 and res["enviados"] == 2 and res["senales_detectadas"] == 2


def test_noticias_correr_por_feed_directo():
    res = noticias.correr(feed_url="https://un-medio.com/rss",
                          http_get=lambda url: RSS,
                          enviar_fn=lambda p: {"senales_detectadas": 0})
    assert res["items"] == 2


def test_noticias_sin_query_ni_feed_lanza():
    try:
        noticias.correr(http_get=lambda url: RSS)
        assert False
    except ValueError:
        pass


# ── yt-dlp: parseo VTT, agrupado y envío con timestamp ───────────────────────

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hola a todos <c>bienvenidos</c>

00:00:03.000 --> 00:00:05.000
Hola a todos

00:01:10.000 --> 00:01:12.000
hablemos de la expansión lenta
"""


def test_parse_vtt_extrae_timestamps_y_limpia_tags():
    cues = youtube.parse_vtt(VTT)
    assert ("00:00:01", "Hola a todos bienvenidos") in cues
    assert all("<c>" not in linea for _, linea in cues)


def test_agrupar_ventana_y_dedup():
    cues = youtube.parse_vtt(VTT)
    bloques = youtube.agrupar(cues, ventana_s=60)
    # La línea repetida "Hola a todos" no se duplica; el segundo bloque abre a ~1:10.
    assert len(bloques) == 2
    assert bloques[0][0] == "00:00:01"
    assert bloques[1][0] == "00:01:10" and "expansión lenta" in bloques[1][1]


def test_youtube_correr_envia_bloques_con_timestamp():
    enviados = []
    res = youtube.correr(
        "https://yt/abc", "Acme", ventana_s=60,
        runner=lambda url, lang: VTT,
        enviar_fn=lambda p: (enviados.append(p) or {"senales_detectadas": 1}))
    assert res["bloques"] == 2 and res["enviados"] == 2
    assert all(e["url"] == "https://yt/abc" and e["timestamp"] for e in enviados)
    assert enviados[1]["timestamp"] == "00:01:10"
