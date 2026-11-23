"""Configuración del bot, leída de variables de entorno (archivo .env)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    # Twilio
    twilio_account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    twilio_from_number: str = field(default_factory=lambda: os.getenv("TWILIO_FROM_NUMBER", ""))
    validate_twilio_signature: bool = field(
        default_factory=lambda: _bool("VALIDATE_TWILIO_SIGNATURE", True)
    )

    # URL pública donde Twilio puede llegar a este servidor (ngrok, Render, etc.)
    public_base_url: str = field(
        default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    )

    # Número de una persona real para transferir a los interesados (opcional)
    human_transfer_number: str = field(
        default_factory=lambda: os.getenv("HUMAN_TRANSFER_NUMBER", "")
    )

    # Proveedor de IA: claude (de pago) | groq | gemini | ollama (gratis)
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "claude").lower())
    # Para groq / gemini / ollama
    free_api_key: str = field(default_factory=lambda: os.getenv("FREE_API_KEY", ""))
    free_model: str = field(default_factory=lambda: os.getenv("FREE_MODEL", ""))
    free_base_url: str = field(default_factory=lambda: os.getenv("FREE_BASE_URL", ""))

    # Claude
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    )
    anthropic_effort: str = field(default_factory=lambda: os.getenv("ANTHROPIC_EFFORT", "low"))

    # Voz e idioma
    language: str = field(default_factory=lambda: os.getenv("LANGUAGE", "es-MX"))
    voice: str = field(default_factory=lambda: os.getenv("VOICE", "Polly.Mia-Neural"))

    # Reglas de llamadas
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "America/Mexico_City"))
    call_start_hour: int = field(default_factory=lambda: _int("CALL_START_HOUR", 9))
    call_end_hour: int = field(default_factory=lambda: _int("CALL_END_HOUR", 19))
    # 0 = lunes ... 6 = domingo
    call_days: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(d) for d in os.getenv("CALL_DAYS", "0,1,2,3,4").split(",") if d.strip()
        )
    )
    max_turns: int = field(default_factory=lambda: _int("MAX_TURNS", 20))
    max_silences: int = field(default_factory=lambda: _int("MAX_SILENCES", 2))
    default_country_code: str = field(
        default_factory=lambda: os.getenv("DEFAULT_COUNTRY_CODE", "52")
    )

    # Archivos
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(ROOT / "llamadas.db")))
    campaign_file: str = field(
        default_factory=lambda: os.getenv("CAMPAIGN_FILE", str(ROOT / "campana.toml"))
    )


settings = Settings()


def load_campaign(path: str | None = None) -> dict:
    """Lee el guion de la campaña de ventas (archivo TOML)."""
    with open(path or settings.campaign_file, "rb") as f:
        return tomllib.load(f)
