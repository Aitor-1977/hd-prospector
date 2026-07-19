"""Conector yt-dlp (video): transcripción de YouTube → /webhook/ingesta.

Extrae los subtítulos (VTT, auto o manuales) con yt-dlp, limpia el texto, lo
agrupa en ventanas con su timestamp y postea cada bloque al webhook. yt-dlp se
invoca por subprocess (el paquete no se importa), y el runner y el envío se
inyectan para testear sin red ni yt-dlp instalado.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
import tempfile
from typing import Callable, Optional

from . import config
from .webhook import enviar

log = logging.getLogger("hd_scraper.ingesta.youtube")

_RE_TAG = re.compile(r"<[^>]+>")                       # tags de estilo del VTT
_RE_TS = re.compile(r"(\d{2}:\d{2}:\d{2})[.,]\d{3}")   # 00:12:30.500 / ,500


def parse_vtt(texto: str) -> list[tuple[str, str]]:
    """Devuelve [(timestamp hh:mm:ss, línea)] de un VTT o SRT."""
    cues: list[tuple[str, str]] = []
    ts_actual: Optional[str] = None
    for linea in (texto or "").splitlines():
        l = linea.strip()
        if "-->" in l:
            m = _RE_TS.search(l)
            ts_actual = m.group(1) if m else ts_actual
            continue
        if not l or l.upper() == "WEBVTT" or l.isdigit():
            continue
        limpio = _RE_TAG.sub("", l).strip()
        if limpio and ts_actual is not None:
            cues.append((ts_actual, limpio))
    return cues


def _ts_a_seg(ts: str) -> int:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + int(float(s))


def agrupar(cues: list[tuple[str, str]], ventana_s: int = 60) -> list[tuple[str, str]]:
    """Agrupa cues en ventanas de ~ventana_s s; dedup de líneas repetidas (autocaption)."""
    if not cues:
        return []
    bloques: list[tuple[str, str]] = []
    inicio_ts = cues[0][0]
    inicio_seg = _ts_a_seg(inicio_ts)
    buff: list[str] = []
    vistos: set[str] = set()
    for ts, linea in cues:
        if linea in vistos:
            continue
        if _ts_a_seg(ts) - inicio_seg >= ventana_s and buff:
            bloques.append((inicio_ts, " ".join(buff)))
            inicio_ts, inicio_seg, buff, vistos = ts, _ts_a_seg(ts), [], set()
        vistos.add(linea)
        buff.append(linea)
    if buff:
        bloques.append((inicio_ts, " ".join(buff)))
    return bloques


def _runner_ytdlp(video_url: str, lang: str) -> str:
    """Descarga subtítulos con el binario yt-dlp y devuelve el contenido VTT."""
    with tempfile.TemporaryDirectory() as tmp:
        salida = os.path.join(tmp, "sub")
        cmd = [
            "yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
            "--sub-langs", f"{lang},{lang}-orig,en", "--sub-format", "vtt",
            "-o", salida, video_url,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        vtts = glob.glob(os.path.join(tmp, "*.vtt"))
        if not vtts:
            raise RuntimeError("yt-dlp no produjo subtítulos para el video")
        with open(vtts[0], encoding="utf-8") as fh:
            return fh.read()


def descargar_subs(video_url: str, lang: str = "es", *,
                   runner: Optional[Callable[[str, str], str]] = None) -> str:
    return (runner or _runner_ytdlp)(video_url, lang)


def correr(
    video_url: str,
    org_name: Optional[str] = None,
    *,
    lang: str = "es",
    ventana_s: Optional[int] = None,
    runner: Optional[Callable[[str, str], str]] = None,
    enviar_fn: Callable[[dict], dict] = enviar,
) -> dict:
    """Transcribe el video, agrupa por ventana y postea cada bloque con su timestamp."""
    ventana = config.VENTANA_VIDEO_S if ventana_s is None else ventana_s
    vtt = descargar_subs(video_url, lang, runner=runner)
    bloques = agrupar(parse_vtt(vtt), ventana)
    enviados = detectadas = 0
    for ts, texto in bloques:
        try:
            resp = enviar_fn({"texto": texto, "url": video_url,
                              "timestamp": ts, "org_name": org_name})
            enviados += 1
            detectadas += int((resp or {}).get("senales_detectadas", 0))
        except Exception as exc:
            log.error("youtube: no se pudo enviar bloque %s: %s", ts, exc)
    return {"conector": "youtube", "bloques": len(bloques), "enviados": enviados,
            "senales_detectadas": detectadas}
