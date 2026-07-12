"""Log de salud por conector (tabla `salud_fuentes`).

Si una fuente falla 2 corridas seguidas (umbral configurable), se marca
``alerta = 1``. Una corrida exitosa reinicia el contador y limpia la alerta.
"""
from __future__ import annotations

from ..config import settings
from ..db.database import Database
from ..db.models import ahora_iso


def registrar_corrida(db: Database, fuente: str, ok: bool, detalle: str = "") -> dict:
    """Actualiza `salud_fuentes` con el resultado de una corrida de la fuente.

    Devuelve el estado resultante (incluye ``alerta`` y ``fallos_consecutivos``).
    """
    fila = db.fetch_one(
        "SELECT fallos_consecutivos FROM salud_fuentes WHERE fuente = ?", (fuente,)
    )
    previos = fila["fallos_consecutivos"] if fila else 0

    if ok:
        fallos = 0
        estado = "ok"
    else:
        fallos = previos + 1
        estado = "error"

    alerta = 1 if fallos >= settings.health_alert_threshold else 0
    ahora = ahora_iso()

    db.execute(
        """
        INSERT INTO salud_fuentes
            (fuente, ultima_corrida, ultimo_estado, fallos_consecutivos, alerta, detalle)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(fuente) DO UPDATE SET
            ultima_corrida = excluded.ultima_corrida,
            ultimo_estado = excluded.ultimo_estado,
            fallos_consecutivos = excluded.fallos_consecutivos,
            alerta = excluded.alerta,
            detalle = excluded.detalle
        """,
        (fuente, ahora, estado, fallos, alerta, detalle[:500]),
    )
    return {
        "fuente": fuente,
        "ultima_corrida": ahora,
        "ultimo_estado": estado,
        "fallos_consecutivos": fallos,
        "alerta": bool(alerta),
    }
