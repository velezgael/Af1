"""Servidor web que Twilio llama durante cada llamada (webhooks de voz)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Gather, VoiceResponse

from . import db
from .agent import SalesAgent, Turn, greeting, initial_history
from .config import load_campaign, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot.server")

app = FastAPI(title="Bot de llamadas de ventas")

_agent: SalesAgent | None = None


def get_agent() -> SalesAgent:
    global _agent
    if _agent is None:
        _agent = SalesAgent(load_campaign())
    return _agent


# ---------- Utilidades ----------

def twiml(resp: VoiceResponse) -> Response:
    return Response(content=str(resp), media_type="application/xml")


def say(node: VoiceResponse | Gather, text: str) -> None:
    node.say(text, voice=settings.voice, language=settings.language)


def speak_and_listen(text: str) -> VoiceResponse:
    """Dice el texto y escucha la respuesta del negocio."""
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"{settings.public_base_url}/twilio/respond",
        method="POST",
        language=settings.language,
        speech_timeout="auto",
        action_on_empty_result=True,
    )
    say(gather, text)
    resp.append(gather)
    # Si Gather no devuelve nada, Twilio continúa aquí
    resp.redirect(f"{settings.public_base_url}/twilio/respond", method="POST")
    return resp


def speak_and_hangup(text: str) -> VoiceResponse:
    resp = VoiceResponse()
    if text:
        say(resp, text)
    resp.hangup()
    return resp


async def form_data(request: Request) -> dict[str, str]:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if settings.validate_twilio_signature:
        validator = RequestValidator(settings.twilio_auth_token)
        url = settings.public_base_url + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(url, params, signature):
            log.warning("Firma de Twilio inválida en %s", url)
            raise HTTPException(status_code=403, detail="Firma inválida")
    return params


# ---------- Endpoints ----------

@app.get("/health")
def health() -> dict[str, str]:
    return {"ok": "true"}


@app.post("/twilio/voice")
async def voice(request: Request) -> Response:
    """Twilio llama aquí cuando el negocio contesta."""
    params = await form_data(request)
    call_sid = params.get("CallSid", "")
    answered_by = params.get("AnsweredBy", "")
    lead_id_raw = request.query_params.get("lead_id")
    lead_id = int(lead_id_raw) if lead_id_raw and lead_id_raw.isdigit() else None

    with db.connect() as conn:
        db.create_call(conn, call_sid, lead_id)
        lead = db.get_lead(conn, lead_id) if lead_id else None
        lead = lead or {"negocio": "", "contacto": "", "ciudad": "", "notas": ""}

        # Contestó una máquina (buzón de voz / fax)
        if answered_by.startswith("machine") or answered_by == "fax":
            campaign = get_agent().campaign
            mensaje = campaign.get("mensaje_buzon", "").format(
                agente=campaign["agente_nombre"], empresa=campaign["empresa"]
            )
            db.update_call(
                conn, call_sid, contestada_por=answered_by, resultado="volver_a_llamar",
                notas="Contestó buzón de voz.",
            )
            return twiml(speak_and_hangup(mensaje))

        text = greeting(get_agent().campaign, lead)
        db.update_call(
            conn, call_sid, contestada_por=answered_by or "desconocido",
            estado="en_conversacion", historial=initial_history(text),
        )
    log.info("Llamada %s contestada (%s)", call_sid, answered_by or "sin detección")
    return twiml(speak_and_listen(text))


@app.post("/twilio/respond")
async def respond(request: Request) -> Response:
    """Twilio llama aquí cada vez que el negocio termina de hablar."""
    params = await form_data(request)
    call_sid = params.get("CallSid", "")
    heard = params.get("SpeechResult", "").strip()

    with db.connect() as conn:
        call = db.get_call(conn, call_sid)
        if not call:
            return twiml(speak_and_hangup(""))
        lead = db.get_lead(conn, call["lead_id"]) if call["lead_id"] else None

    lead = lead or {}
    silencios = 0 if heard else call["silencios"] + 1
    if silencios > settings.max_silences:
        with db.connect() as conn:
            db.update_call(conn, call_sid, silencios=silencios, resultado="volver_a_llamar",
                           notas=(call["notas"] + " Nadie respondió.").strip())
        return twiml(speak_and_hangup("Parece que no me escucha. Le llamamos en otro momento. Hasta luego."))

    log.info("[%s] Negocio: %s", call_sid, heard or "(silencio)")
    turn, history = await run_in_threadpool(get_agent().next_turn, lead, call["historial"], heard)
    log.info("[%s] Bot (%s/%s): %s", call_sid, turn.accion, turn.resultado, turn.decir)

    turnos = call["turnos"] + 1
    if turn.accion == "continuar" and turnos >= settings.max_turns:
        turn = Turn(
            decir=turn.decir + " Le agradezco mucho su tiempo; un asesor le dará seguimiento. Hasta luego.",
            accion="colgar",
            resultado="interesado" if turn.resultado == "en_curso" else turn.resultado,
            cita=turn.cita,
            notas=turn.notas,
        )

    updates: dict[str, Any] = {
        "historial": history,
        "turnos": turnos,
        "silencios": silencios,
        "resultado": turn.resultado,
        "notas": turn.notas,
    }
    if turn.cita:
        updates["cita"] = turn.cita

    with db.connect() as conn:
        if turn.resultado == "no_llamar" and lead.get("telefono"):
            db.add_do_not_call(conn, lead["telefono"], "Lo pidió durante la llamada")
        if turn.accion == "transferir" and settings.human_transfer_number:
            updates["resultado"] = "transferido"
            db.update_call(conn, call_sid, **updates)
            resp = VoiceResponse()
            say(resp, turn.decir)
            resp.dial(settings.human_transfer_number, caller_id=settings.twilio_from_number or None)
            return twiml(resp)
        db.update_call(conn, call_sid, **updates)

    if turn.accion == "continuar":
        return twiml(speak_and_listen(turn.decir))
    return twiml(speak_and_hangup(turn.decir))


@app.post("/twilio/status")
async def status(request: Request) -> Response:
    """Twilio avisa aquí cuando la llamada cambia de estado (terminada, ocupado, sin respuesta...)."""
    params = await form_data(request)
    call_sid = params.get("CallSid", "")
    estado = params.get("CallStatus", "")
    duracion = int(params.get("CallDuration", "0") or 0)
    with db.connect() as conn:
        call = db.get_call(conn, call_sid)
        if call:
            fields: dict[str, Any] = {"estado": estado, "duracion": duracion}
            if estado in ("busy", "no-answer", "failed", "canceled") and call["resultado"] == "en_curso":
                fields["resultado"] = "volver_a_llamar"
            db.update_call(conn, call_sid, **fields)
    log.info("Llamada %s: %s (%ss)", call_sid, estado, duracion)
    return Response(status_code=204)
