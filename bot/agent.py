"""El "cerebro" del bot: decide qué decir en cada turno de la llamada usando Claude."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import anthropic

from .config import settings

log = logging.getLogger(__name__)

RESULTADOS = [
    "en_curso",          # la conversación sigue
    "interesado",        # quiere más información / que lo contacten
    "cita_agendada",     # se acordó fecha y hora
    "no_interesado",
    "volver_a_llamar",   # no está el encargado, llamar en otro momento
    "no_llamar",         # pidió que no le vuelvan a llamar
    "numero_equivocado",
    "transferido",       # se pasó la llamada a un humano
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decir": {
            "type": "string",
            "description": "Lo que el bot dirá en voz alta. Español hablado, 1 a 3 frases cortas.",
        },
        "accion": {"type": "string", "enum": ["continuar", "colgar", "transferir"]},
        "resultado": {"type": "string", "enum": RESULTADOS},
        "cita": {
            "type": "string",
            "description": "Fecha y hora acordada, o cuándo volver a llamar. Vacío si no aplica.",
        },
        "notas": {
            "type": "string",
            "description": "Resumen breve para el equipo de ventas: quién contestó, necesidades, objeciones.",
        },
    },
    "required": ["decir", "accion", "resultado", "cita", "notas"],
    "additionalProperties": False,
}

CALL_ANSWERED = "[La llamada fue contestada. Ya dijiste el saludo inicial.]"
NO_SPEECH = "[No se escuchó respuesta del otro lado.]"


@dataclass
class Turn:
    decir: str
    accion: str
    resultado: str
    cita: str
    notas: str

    @classmethod
    def fallback(cls) -> "Turn":
        return cls(
            decir="Disculpe, tengo problemas con la línea. Le llamamos en otro momento. ¡Que tenga buen día!",
            accion="colgar",
            resultado="volver_a_llamar",
            cita="",
            notas="El bot no pudo generar respuesta (error técnico).",
        )


def build_system_prompt(campaign: dict) -> str:
    """Instrucciones fijas de la campaña (se cachean entre llamadas)."""
    c = campaign
    preguntas = "\n".join(f"- {p}" for p in c.get("preguntas_calificacion", []))
    objeciones = "\n".join(
        f"- Si dicen \"{o['objecion']}\": {o['respuesta']}" for o in c.get("objeciones", [])
    )
    transferir = (
        "Si el prospecto está interesado y quiere hablar ya con una persona, usa accion=\"transferir\" "
        "y dile que lo comunicas con un asesor."
        if settings.human_transfer_number
        else "No puedes transferir llamadas: si quieren hablar con una persona, ofrece que un asesor les llame y usa resultado=\"interesado\"."
    )
    return f"""Eres {c['agente_nombre']}, asistente virtual de ventas de {c['empresa']}. Estás en una LLAMADA TELEFÓNICA \
saliente a un negocio en México. Todo lo que escribes en "decir" se convierte en voz, y lo que dice la otra persona \
te llega transcrito automáticamente (puede tener errores de transcripción: interprétalo con sentido común).

## Qué vendes
{c['producto']}

Propuesta de valor: {c['propuesta_valor']}

Información adicional que puedes usar (no inventes nada fuera de esto; si no sabes algo, ofrece que un asesor le dé el dato):
{c.get('informacion_extra', '')}

## Objetivo de la llamada
{c['objetivo']}

Preguntas para calificar al prospecto (hazlas de forma natural, una por turno, no como interrogatorio):
{preguntas}

## Manejo de objeciones
{objeciones}

## Cómo hablar
- Español de México, cordial, profesional y natural; usa "usted".
- Frases cortas: máximo 2 o 3 por turno. Es una llamada, no un correo. Haz UNA pregunta a la vez.
- Nada de emojis, viñetas, markdown, URLs ni abreviaturas. Escribe los números como se dicen \
("a las cuatro de la tarde", "quinientos pesos").
- Si contesta una recepcionista o empleado, pide amablemente hablar con el dueño o encargado \
y, si no está, pregunta cuándo es buen momento para llamar (resultado="volver_a_llamar", y anota el horario en "cita").

## Reglas obligatorias
- Ya te presentaste como asistente virtual en el saludo. Si te preguntan si eres un robot o una persona, di la verdad: \
eres un asistente virtual de {c['empresa']}.
- Si piden que no les vuelvan a llamar, o que los quiten de la lista: discúlpate, confirma que no se les volverá a llamar, \
y usa accion="colgar" con resultado="no_llamar".
- Si dicen claramente que no les interesa, agradece sin insistir más de una vez y cuelga (resultado="no_interesado").
- Si es número equivocado o no es el negocio, discúlpate y cuelga (resultado="numero_equivocado").
- Nunca presiones, no mientas, no prometas descuentos ni condiciones que no estén en la información de arriba.
- No pidas datos sensibles (tarjetas, contraseñas, datos bancarios).
- Al agendar una cita, confirma día y hora en voz alta y anótala en "cita".
- {transferir}
- Cuando termines (cita agendada, rechazo, etc.), despídete en "decir" y usa accion="colgar".
- Si no se escucha nada, pregunta una vez si te escuchan.

## Formato
Responde SIEMPRE con el JSON indicado. "resultado" es tu evaluación actual de la llamada \
("en_curso" mientras siga la conversación sin un resultado claro). "notas" es un resumen acumulado para el equipo de ventas."""


def build_lead_context(lead: dict[str, Any]) -> str:
    """Datos del negocio al que se llama (cambian en cada llamada)."""
    return (
        "## Negocio al que llamas\n"
        f"- Nombre: {lead.get('negocio') or 'desconocido'}\n"
        f"- Contacto: {lead.get('contacto') or 'desconocido'}\n"
        f"- Ciudad: {lead.get('ciudad') or 'desconocida'}\n"
        f"- Notas previas: {lead.get('notas') or 'ninguna'}"
    )


def greeting(campaign: dict, lead: dict[str, Any]) -> str:
    """Saludo inicial fijo (se dice al instante, sin esperar al modelo)."""
    contacto = lead.get("contacto") or ""
    template = campaign.get(
        "saludo",
        "Hola, buen día. Le habla {agente}, asistente virtual de {empresa}. ¿Me comunica con el encargado de {negocio}, por favor?",
    )
    return template.format(
        agente=campaign["agente_nombre"],
        empresa=campaign["empresa"],
        negocio=lead.get("negocio") or "el negocio",
        contacto=contacto or "el encargado",
    ).strip()


def initial_history(greeting_text: str) -> list[dict[str, Any]]:
    """Historial con el saludo ya dicho, listo para recibir lo que responda el negocio."""
    saludo = {
        "decir": greeting_text,
        "accion": "continuar",
        "resultado": "en_curso",
        "cita": "",
        "notas": "",
    }
    return [
        {"role": "user", "content": CALL_ANSWERED},
        {"role": "assistant", "content": [{"type": "text", "text": json.dumps(saludo, ensure_ascii=False)}]},
    ]


class SalesAgent:
    def __init__(self, campaign: dict, client: anthropic.Anthropic | None = None):
        self.campaign = campaign
        self.client = client or anthropic.Anthropic(max_retries=1, timeout=12.0)
        self.system_prompt = build_system_prompt(campaign)

    def next_turn(
        self, lead: dict[str, Any], history: list[dict[str, Any]], heard: str
    ) -> tuple[Turn, list[dict[str, Any]]]:
        """Recibe lo que dijo el negocio y devuelve (lo que dirá el bot, historial actualizado)."""
        heard = heard.strip()
        user_text = f"Negocio: {heard}" if heard else NO_SPEECH
        messages = history + [{"role": "user", "content": user_text}]

        try:
            response = self.client.beta.messages.create(
                model=settings.anthropic_model,
                max_tokens=2048,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": build_lead_context(lead)},
                ],
                messages=messages,
                output_config={
                    "effort": settings.anthropic_effort,
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                },
            )
        except anthropic.RateLimitError:
            log.exception("Límite de uso de la API de Claude")
            return Turn.fallback(), messages
        except anthropic.APIStatusError as e:
            log.exception("Error de la API de Claude (HTTP %s)", e.status_code)
            return Turn.fallback(), messages
        except anthropic.APIConnectionError:
            log.exception("Sin conexión con la API de Claude")
            return Turn.fallback(), messages

        if response.stop_reason in ("refusal", "max_tokens"):
            log.warning("Respuesta no utilizable: stop_reason=%s", response.stop_reason)
            return Turn.fallback(), messages

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
            turn = Turn(**{k: str(data[k]) for k in RESPONSE_SCHEMA["required"]})
        except (json.JSONDecodeError, KeyError, TypeError):
            log.warning("No se pudo leer la respuesta del modelo: %r", text[:300])
            return Turn.fallback(), messages

        # Se guarda el contenido completo (incluye bloques de razonamiento) tal cual,
        # para reenviarlo sin cambios en el siguiente turno.
        assistant_content = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
        messages.append({"role": "assistant", "content": assistant_content})
        return turn, messages


# ---------------------------------------------------------------------------
# Opción gratis: proveedores compatibles con la API de OpenAI (Groq, Gemini, Ollama)
# ---------------------------------------------------------------------------

FREE_PROVIDERS = {
    # nombre: (URL base, modelo por defecto)
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:7b"),
}

JSON_INSTRUCTIONS = """

Responde ÚNICAMENTE con un objeto JSON válido (sin texto antes ni después) con exactamente estas claves:
{"decir": "...", "accion": "continuar|colgar|transferir", "resultado": "%s", "cita": "", "notas": "..."}""" % "|".join(RESULTADOS)


def _content_text(content: Any) -> str:
    """Convierte el contenido de un mensaje (texto o lista de bloques) a texto plano."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def parse_turn(text: str) -> Turn | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not str(data.get("decir", "")).strip():
        return None
    accion = data.get("accion") if data.get("accion") in ("continuar", "colgar", "transferir") else "continuar"
    resultado = data.get("resultado") if data.get("resultado") in RESULTADOS else "en_curso"
    return Turn(
        decir=str(data["decir"]).strip(),
        accion=accion,
        resultado=resultado,
        cita=str(data.get("cita") or ""),
        notas=str(data.get("notas") or ""),
    )


class FreeAgent:
    """Mismo bot, pero usando un proveedor gratuito (Groq, Gemini u Ollama)."""

    def __init__(self, campaign: dict, client: Any = None):
        from openai import OpenAI

        provider = settings.llm_provider
        if provider not in FREE_PROVIDERS:
            raise ValueError(f"LLM_PROVIDER desconocido: {provider}. Usa claude, groq, gemini u ollama.")
        base_url, default_model = FREE_PROVIDERS[provider]
        self.model = settings.free_model or default_model
        self.campaign = campaign
        self.client = client or OpenAI(
            base_url=settings.free_base_url or base_url,
            api_key=settings.free_api_key or "ollama",  # Ollama no necesita clave
            timeout=12.0,
            max_retries=1,
        )
        self.system_prompt = build_system_prompt(campaign) + JSON_INSTRUCTIONS

    def next_turn(
        self, lead: dict[str, Any], history: list[dict[str, Any]], heard: str
    ) -> tuple[Turn, list[dict[str, Any]]]:
        heard = heard.strip()
        user_text = f"Negocio: {heard}" if heard else NO_SPEECH
        messages = history + [{"role": "user", "content": user_text}]
        chat = [{"role": "system", "content": self.system_prompt + "\n\n" + build_lead_context(lead)}]
        chat += [{"role": m["role"], "content": _content_text(m["content"])} for m in messages]

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=chat,
                response_format={"type": "json_object"},
                temperature=0.4,
                max_tokens=1024,
            )
            text = response.choices[0].message.content or ""
        except Exception:  # noqa: BLE001 - cualquier fallo del proveedor: colgar con cortesía
            log.exception("Error del proveedor gratuito (%s)", settings.llm_provider)
            return Turn.fallback(), messages

        turn = parse_turn(text)
        if turn is None:
            log.warning("No se pudo leer la respuesta del modelo: %r", text[:300])
            return Turn.fallback(), messages
        messages.append({"role": "assistant", "content": text})
        return turn, messages


def make_agent(campaign: dict) -> SalesAgent | FreeAgent:
    """Crea el bot con el proveedor elegido en LLM_PROVIDER."""
    if settings.llm_provider == "claude":
        return SalesAgent(campaign)
    return FreeAgent(campaign)
