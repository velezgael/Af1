"""Base de datos SQLite: prospectos, llamadas (con su conversación) y lista de no llamar."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings

# Resultados con los que ya no se vuelve a llamar a ese prospecto
FINAL_OUTCOMES = {
    "interesado",
    "cita_agendada",
    "no_interesado",
    "no_llamar",
    "transferido",
    "numero_equivocado",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    negocio TEXT NOT NULL,
    telefono TEXT NOT NULL UNIQUE,
    contacto TEXT DEFAULT '',
    ciudad TEXT DEFAULT '',
    notas TEXT DEFAULT '',
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
    call_sid TEXT PRIMARY KEY,
    lead_id INTEGER REFERENCES leads(id),
    estado TEXT DEFAULT 'iniciada',
    contestada_por TEXT DEFAULT '',
    resultado TEXT DEFAULT 'en_curso',
    notas TEXT DEFAULT '',
    cita TEXT DEFAULT '',
    historial TEXT DEFAULT '[]',
    turnos INTEGER DEFAULT 0,
    silencios INTEGER DEFAULT 0,
    duracion INTEGER DEFAULT 0,
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS no_llamar (
    telefono TEXT PRIMARY KEY,
    motivo TEXT DEFAULT '',
    creado TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- Prospectos ----------

def upsert_lead(conn: sqlite3.Connection, lead: dict[str, str]) -> int:
    conn.execute(
        """INSERT INTO leads (negocio, telefono, contacto, ciudad, notas, creado)
           VALUES (:negocio, :telefono, :contacto, :ciudad, :notas, :creado)
           ON CONFLICT(telefono) DO UPDATE SET
             negocio=excluded.negocio, contacto=excluded.contacto,
             ciudad=excluded.ciudad, notas=excluded.notas""",
        {
            "negocio": lead.get("negocio", ""),
            "telefono": lead["telefono"],
            "contacto": lead.get("contacto", ""),
            "ciudad": lead.get("ciudad", ""),
            "notas": lead.get("notas", ""),
            "creado": _now(),
        },
    )
    row = conn.execute("SELECT id FROM leads WHERE telefono = ?", (lead["telefono"],)).fetchone()
    return int(row["id"])


def get_lead(conn: sqlite3.Connection, lead_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


def pending_leads(conn: sqlite3.Connection, max_attempts: int = 3) -> list[dict[str, Any]]:
    """Prospectos que no están en 'no llamar', sin resultado final y con pocos intentos."""
    placeholders = ",".join("?" for _ in FINAL_OUTCOMES)
    rows = conn.execute(
        f"""SELECT l.* FROM leads l
            WHERE l.telefono NOT IN (SELECT telefono FROM no_llamar)
              AND NOT EXISTS (
                SELECT 1 FROM calls c WHERE c.lead_id = l.id AND c.resultado IN ({placeholders})
              )
              AND (SELECT COUNT(*) FROM calls c WHERE c.lead_id = l.id) < ?
            ORDER BY l.id""",
        (*FINAL_OUTCOMES, max_attempts),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Lista de no llamar ----------

def add_do_not_call(conn: sqlite3.Connection, telefono: str, motivo: str = "") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO no_llamar (telefono, motivo, creado) VALUES (?, ?, ?)",
        (telefono, motivo, _now()),
    )


def is_do_not_call(conn: sqlite3.Connection, telefono: str) -> bool:
    return conn.execute("SELECT 1 FROM no_llamar WHERE telefono = ?", (telefono,)).fetchone() is not None


# ---------- Llamadas ----------

def create_call(conn: sqlite3.Connection, call_sid: str, lead_id: int | None) -> None:
    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO calls (call_sid, lead_id, creado, actualizado)
           VALUES (?, ?, ?, ?)""",
        (call_sid, lead_id, now, now),
    )


def get_call(conn: sqlite3.Connection, call_sid: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM calls WHERE call_sid = ?", (call_sid,)).fetchone()
    if not row:
        return None
    call = dict(row)
    call["historial"] = json.loads(call["historial"] or "[]")
    return call


def update_call(conn: sqlite3.Connection, call_sid: str, **fields: Any) -> None:
    if not fields:
        return
    if "historial" in fields:
        fields["historial"] = json.dumps(fields["historial"], ensure_ascii=False)
    fields["actualizado"] = _now()
    columns = ", ".join(f"{k} = :{k}" for k in fields)
    conn.execute(f"UPDATE calls SET {columns} WHERE call_sid = :call_sid", {**fields, "call_sid": call_sid})


def report(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT c.creado, l.negocio, l.telefono, l.contacto, c.estado, c.contestada_por,
                  c.resultado, c.cita, c.notas, c.duracion, c.call_sid
           FROM calls c LEFT JOIN leads l ON l.id = c.lead_id
           ORDER BY c.creado DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
