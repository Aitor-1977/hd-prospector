"""Conectores de ingesta Capa 0: mapeo, parseo de subtítulos, agrupado y resiliencia.

Todo con la red inyectada; ninguna prueba llama a Apify, yt-dlp ni al webhook real.
"""
from hd_scraper.ingesta import apify, webhook, youtube

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


# ── Apify: mapeo de item → payload ───────────────────────────────────────────

def test_apify_item_a_payload_varias_formas():
    assert apify.item_a_payload({"text": "Buscamos head of growth", "url": "u",
                                 "companyName": "Acme"}) == {
        "texto": "Buscamos head of growth", "url": "u", "org_name": "Acme"}
    # Sin texto -> None (no se envía).
    assert apify.item_a_payload({"url": "u"}) is None


def test_apify_correr_postea_cada_item():
    items = [
        {"description": "vacante senior de growth", "url": "u1", "company": "A"},
        {"title": "", "url": "u2"},                      # sin texto -> se omite
        {"text": "down round", "link": "u3", "source": "News"},
    ]
    enviados = []
    res = apify.correr("DS", token="tok",
                       http_get_json=lambda url: items,
                       enviar_fn=lambda p: (enviados.append(p) or {"senales_detectadas": 1}))
    assert res["items"] == 3 and res["enviados"] == 2 and res["senales_detectadas"] == 2
    assert {e["url"] for e in enviados} == {"u1", "u3"}


def test_apify_sin_token_lanza():
    try:
        apify.correr("DS", token="")
        assert False
    except RuntimeError as e:
        assert "APIFY_TOKEN" in str(e)


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
