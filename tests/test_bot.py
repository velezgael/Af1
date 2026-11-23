import copy
import json
import os
from datetime import datetime, timezone

import pytest

os.environ["VALIDATE_TWILIO_SIGNATURE"] = "false"
os.environ["PUBLIC_BASE_URL"] = "https://ejemplo.test"


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text

    def model_dump(self, **_):
        return {"type": "text", "text": self.text}


class FakeResponse:
    def __init__(self, payload, stop_reason="end_turn"):
        self.stop_reason = stop_reason
        self.content = [FakeBlock(json.dumps(payload))]


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.replies.pop(0)


class FakeClient:
    def __init__(self, replies):
        self.beta = type("B", (), {})()
        self.beta.messages = FakeMessages(replies)


def reply(decir, accion="continuar", resultado="en_curso", cita="", notas=""):
    return FakeResponse(dict(decir=decir, accion=accion, resultado=resultado, cita=cita, notas=notas))


# ---------- compliance ----------

def test_normalize_phone():
    from bot.compliance import normalize_phone

    assert normalize_phone("55 1234 5678") == "+525512345678"
    assert normalize_phone("+52 33 9876 5432") == "+523398765432"
    assert normalize_phone("0052 81 5555 0000") == "+528155550000"
    assert normalize_phone("123") is None
    assert normalize_phone("") is None


def test_calling_hours():
    from bot.compliance import within_calling_hours

    # Lunes 24 nov 2026, 17:00 UTC = 11:00 en CDMX
    monday = datetime(2026, 11, 23, 17, 0, tzinfo=timezone.utc)
    assert within_calling_hours(monday, "America/Mexico_City", 9, 19, (0, 1, 2, 3, 4))
    # 03:00 UTC = 21:00 del día anterior en CDMX
    night = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
    assert not within_calling_hours(night, "America/Mexico_City", 9, 19, (0, 1, 2, 3, 4))
    sunday = datetime(2026, 11, 22, 17, 0, tzinfo=timezone.utc)
    assert not within_calling_hours(sunday, "America/Mexico_City", 9, 19, (0, 1, 2, 3, 4))


# ---------- agente ----------

def test_agent_parses_turn_and_keeps_history():
    from bot.agent import SalesAgent, greeting, initial_history
    from bot.config import load_campaign

    client = FakeClient([reply("Mucho gusto, Don Ramón. ¿Cómo reciben hoy sus pedidos?")])
    agent = SalesAgent(load_campaign(), client=client)
    lead = {"negocio": "Taquería", "contacto": "Don Ramón"}
    history = initial_history(greeting(agent.campaign, lead))
    turn, history = agent.next_turn(lead, history, "Sí, soy yo, dígame")

    assert turn.accion == "continuar"
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    sent = client.beta.messages.calls[0]
    assert sent["messages"][-1]["content"] == "Negocio: Sí, soy yo, dígame"
    assert sent["output_config"]["format"]["type"] == "json_schema"


def test_agent_refusal_falls_back_to_hangup():
    from bot.agent import SalesAgent
    from bot.config import load_campaign

    client = FakeClient([FakeResponse({}, stop_reason="refusal")])
    turn, _ = SalesAgent(load_campaign(), client=client).next_turn({}, [], "hola")
    assert turn.accion == "colgar"


# ---------- servidor (flujo completo de una llamada) ----------

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import bot.config as config
    import bot.server as server

    original = config.settings.db_path
    object.__setattr__(config.settings, "db_path", str(tmp_path / "t.db"))  # settings es inmutable
    server._agent = None
    yield TestClient(server.app), server
    object.__setattr__(config.settings, "db_path", original)


def test_full_call_flow_with_do_not_call(app_client):
    from bot import db
    from bot.agent import SalesAgent
    from bot.config import load_campaign

    client, server = app_client
    fake = FakeClient([
        reply("Claro. ¿Cómo reciben hoy los pedidos?"),
        reply("Disculpe la molestia, ya no le volveremos a llamar. Buen día.", "colgar", "no_llamar"),
    ])
    server._agent = SalesAgent(load_campaign(), client=fake)

    with db.connect() as conn:
        lead_id = db.upsert_lead(conn, {"negocio": "Taquería", "telefono": "+525512345678"})
        db.create_call(conn, "CA1", lead_id)

    r = client.post(f"/twilio/voice?lead_id={lead_id}", data={"CallSid": "CA1", "AnsweredBy": "human"})
    assert r.status_code == 200
    assert "<Gather" in r.text and "asistente virtual" in r.text

    r = client.post("/twilio/respond", data={"CallSid": "CA1", "SpeechResult": "Sí, dígame"})
    assert "<Gather" in r.text and "pedidos" in r.text

    r = client.post("/twilio/respond", data={"CallSid": "CA1", "SpeechResult": "No me vuelvan a llamar"})
    assert "<Hangup" in r.text

    r = client.post("/twilio/status", data={"CallSid": "CA1", "CallStatus": "completed", "CallDuration": "42"})
    assert r.status_code == 204

    with db.connect() as conn:
        call = db.get_call(conn, "CA1")
        assert call["resultado"] == "no_llamar"
        assert call["duracion"] == 42
        assert db.is_do_not_call(conn, "+525512345678")
        assert db.pending_leads(conn) == []


def test_voicemail_hangs_up(app_client):
    client, _ = app_client
    r = client.post("/twilio/voice", data={"CallSid": "CA2", "AnsweredBy": "machine_start"})
    assert "<Hangup" in r.text and "<Gather" not in r.text


def test_silence_limit(app_client):
    from bot import db

    client, server = app_client
    with db.connect() as conn:
        db.create_call(conn, "CA3", None)
    server._agent = __import__("bot.agent", fromlist=["SalesAgent"]).SalesAgent(
        __import__("bot.config", fromlist=["load_campaign"]).load_campaign(),
        client=FakeClient([reply("¿Me escucha?"), reply("¿Hola?")]),
    )
    client.post("/twilio/voice", data={"CallSid": "CA3", "AnsweredBy": "human"})
    client.post("/twilio/respond", data={"CallSid": "CA3", "SpeechResult": ""})
    client.post("/twilio/respond", data={"CallSid": "CA3", "SpeechResult": ""})
    r = client.post("/twilio/respond", data={"CallSid": "CA3", "SpeechResult": ""})
    assert "<Hangup" in r.text


# ---------- opción gratis (Groq / Gemini / Ollama) ----------

class FakeChat:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        msg = type("M", (), {"content": self.texts.pop(0)})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


def test_free_agent_parses_json_and_history(monkeypatch):
    import bot.config as config
    from bot.agent import FreeAgent, greeting, initial_history

    object.__setattr__(config.settings, "llm_provider", "groq")
    try:
        fake = FakeChat(['Claro: {"decir": "¿Cómo reciben sus pedidos?", "accion": "continuar", '
                         '"resultado": "en_curso", "cita": "", "notas": "Contestó el dueño"}'])
        agent = FreeAgent(config.load_campaign(), client=type("O", (), {"chat": fake})())
        history = initial_history(greeting(agent.campaign, {"negocio": "Taquería"}))
        turn, history = agent.next_turn({"negocio": "Taquería"}, history, "Sí, soy el dueño")
    finally:
        object.__setattr__(config.settings, "llm_provider", "claude")

    assert turn.decir == "¿Cómo reciben sus pedidos?"
    sent = fake.calls[0]["messages"]
    assert sent[0]["role"] == "system" and "JSON" in sent[0]["content"]
    assert all(isinstance(m["content"], str) for m in sent)
    assert history[-1]["role"] == "assistant"


def test_parse_turn_rejects_garbage():
    from bot.agent import parse_turn

    assert parse_turn("no es json") is None
    t = parse_turn('{"decir": "Hola", "accion": "bailar", "resultado": "raro"}')
    assert t.accion == "continuar" and t.resultado == "en_curso"
