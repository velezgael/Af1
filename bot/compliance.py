"""Reglas para llamar de forma responsable: formato de teléfonos y horarios."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo


def normalize_phone(raw: str, default_country_code: str = "52") -> str | None:
    """Convierte un teléfono a formato E.164 (+5215512345678). Devuelve None si no es válido."""
    if not raw:
        return None
    raw = raw.strip()
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if has_plus:
        number = digits
    elif raw.startswith("00"):
        number = digits[2:]
    elif len(digits) == 10:
        number = default_country_code + digits
    else:
        number = digits
    if not 8 <= len(number) <= 15:
        return None
    return "+" + number


def within_calling_hours(
    now: datetime | None,
    tz: str,
    start_hour: int,
    end_hour: int,
    days: tuple[int, ...],
) -> bool:
    """True si la hora local está dentro del horario permitido para llamar."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone) if now else datetime.now(zone)
    return local.weekday() in days and start_hour <= local.hour < end_hour
