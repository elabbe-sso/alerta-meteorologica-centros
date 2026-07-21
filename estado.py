"""
Almacenamiento mínimo para no reenviar la misma alerta más de una vez
cada cierto tiempo. Usa SQLite porque no requiere infraestructura
adicional para un prototipo o un despliegue pequeño; se puede migrar a
Postgres sin cambiar la interfaz de estas dos funciones.

Ventana de repetición: 24 horas. Si un punto sigue superando el mismo
umbral después de que pasen 24h desde el último aviso, se notifica de
nuevo (no es "avisado para siempre", es "como máximo un aviso por día
por punto+tipo de alerta").
"""

from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "estado_alertas.db"
VENTANA_REPETICION = timedelta(hours=24)


def _conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alertas_enviadas (
            clave TEXT PRIMARY KEY,
            enviado_en TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def ya_fue_enviada(clave: str) -> bool:
    """
    True si esta alerta ya se avisó hace MENOS de 24 horas (así que no
    hay que repetirla todavía). Si ya pasaron 24h o más, devuelve False
    para que se vuelva a notificar.
    """
    with _conectar() as conn:
        cur = conn.execute(
            "SELECT enviado_en FROM alertas_enviadas WHERE clave = ?", (clave,)
        )
        fila = cur.fetchone()
        if fila is None:
            return False

        enviado_en = datetime.fromisoformat(fila[0]).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - enviado_en) < VENTANA_REPETICION


def marcar_enviada(clave: str) -> None:
    """Registra (o actualiza) el momento en que se avisó esta alerta."""
    with _conectar() as conn:
        conn.execute(
            """
            INSERT INTO alertas_enviadas (clave, enviado_en)
            VALUES (?, ?)
            ON CONFLICT(clave) DO UPDATE SET enviado_en = excluded.enviado_en
            """,
            (clave, datetime.now(timezone.utc).isoformat()),
        )
