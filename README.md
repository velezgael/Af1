# Bot de llamadas de ventas 📞🤖

Un bot que **llama por teléfono a negocios y conversa con voz** para presentar tu producto y agendar demostraciones.

- **Twilio** hace la llamada, convierte la voz del negocio a texto y lee en voz alta lo que dice el bot.
- **Claude** (Anthropic) decide qué decir en cada momento, siguiendo el guion que tú escribes en `campana.toml`.
- Todo se guarda en una base de datos local (`llamadas.db`): conversación, resultado, cita y notas.

```
  python -m bot llamar ──► Twilio llama al negocio
                               │
            negocio contesta ──┤──► /twilio/voice    → el bot saluda
            negocio habla ─────┤──► /twilio/respond  → Claude decide qué responder → Twilio lo dice
            llamada termina ───┘──► /twilio/status   → se guarda el resultado
```

## Qué hace

- Saluda y **se presenta como asistente virtual** (obligatorio, ver "Reglas legales").
- Pide hablar con el dueño o encargado; si no está, pregunta cuándo volver a llamar.
- Presenta el producto, hace preguntas para calificar, responde objeciones y **agenda una demo**.
- Detecta **buzón de voz** (deja un mensaje corto o cuelga).
- Si piden **"no me vuelvan a llamar"**, los agrega automáticamente a la lista de no llamar.
- Opcional: **transfiere** a un vendedor humano cuando el prospecto está interesado.
- Solo llama en **horario permitido** (lunes a viernes 9:00–19:00, hora de CDMX, configurable).
- Clasifica cada llamada: `cita_agendada`, `interesado`, `no_interesado`, `volver_a_llamar`, `no_llamar`, `numero_equivocado`, `transferido`.

## 💸 Opción gratis

| Parte | Gratis | Notas |
|---|---|---|
| Inteligencia artificial | ✅ **Groq** (recomendado), **Gemini** u **Ollama** | Pon `LLM_PROVIDER=groq` y tu `FREE_API_KEY` en `.env`. Groq y Gemini tienen límites diarios gratuitos; Ollama corre en tu PC. |
| Probar la conversación por texto | ✅ | `python -m bot simular` |
| Escuchar al bot en una llamada real | ✅ Prueba gratis de Twilio (crédito inicial, sin tarjeta) | Solo puede llamar a **números verificados**, por ejemplo tu propio celular. |
| Llamar a negocios reales | ❌ | Hay que pasar Twilio a cuenta de pago (número mensual + costo por minuto). |

Nota: en el plan gratis de Gemini, Google puede usar las conversaciones para mejorar sus productos.

## Lo que necesitas

1. **Python 3.11 o más nuevo.**
2. **Cuenta de Twilio** con un número de teléfono que pueda llamar a México → <https://console.twilio.com>
   (en cuentas de prueba solo puedes llamar a números verificados).
3. **Clave de IA**: gratis en Groq → <https://console.groq.com/keys> (o Claude, de pago → <https://console.anthropic.com>)
4. Una **URL pública** para que Twilio llegue a tu computadora. Para pruebas usa [ngrok](https://ngrok.com):
   `ngrok http 8000`.

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate          # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # y llena tus claves en .env
```

## Uso paso a paso

**1. Escribe tu guion** en `campana.toml`: nombre de tu empresa, producto, precios, objetivo y respuestas a objeciones.
El bot no inventa información: solo usa lo que pongas ahí.

**2. Pruébalo por texto (sin llamar a nadie):** tú haces el papel del negocio.

```bash
python -m bot simular --negocio "Taquería El Güero"
```

**3. Importa tus prospectos** desde un CSV con columnas `negocio,telefono,contacto,ciudad,notas`
(mira `prospectos.ejemplo.csv`). Los teléfonos de 10 dígitos se toman como de México (+52).

```bash
python -m bot importar prospectos.csv
```

**4. Arranca el servidor** (en otra terminal) y la URL pública:

```bash
uvicorn bot.server:app --port 8000
ngrok http 8000          # copia la URL https://... en PUBLIC_BASE_URL dentro de .env
```

**5. Lanza las llamadas:**

```bash
python -m bot llamar --prueba            # solo muestra a quién llamaría
python -m bot llamar --limite 5          # llama a 5 prospectos, con 30 s de pausa entre cada una
```

**6. Revisa los resultados:**

```bash
python -m bot reporte
python -m bot reporte --csv reporte.csv  # para abrir en Excel
```

Otros comandos: `python -m bot no-llamar 5512345678` agrega un número a tu lista de no llamar.

## Configuración (`.env`)

| Variable | Para qué sirve |
|---|---|
| `ANTHROPIC_MODEL` | Modelo de Claude (default `claude-opus-5-5`). Para respuestas más rápidas y baratas puedes usar `claude-haiku-5-5`. |
| `ANTHROPIC_EFFORT` | `low` (default) responde más rápido, ideal para teléfono. |
| `VOICE` / `LANGUAGE` | Voz de Twilio (`Polly.Mia-Neural`, español de México) e idioma de reconocimiento (`es-MX`). |
| `HUMAN_TRANSFER_NUMBER` | Si lo llenas, el bot transfiere a los interesados a ese número. |
| `CALL_START_HOUR`, `CALL_END_HOUR`, `CALL_DAYS`, `TIMEZONE` | Horario en que se permite llamar. |
| `MAX_TURNS`, `MAX_SILENCES` | Límite de turnos por llamada y de silencios antes de colgar. |

## ⚖️ Reglas legales — léelas antes de llamar

Este bot hace llamadas comerciales automatizadas con voz artificial. Eso está regulado:

- **México:** antes de llamar, revisa que los números **no estén inscritos en el REPEP** de Profeco
  (<https://repep.profeco.gob.mx>) y agrégalos con `python -m bot no-llamar`. Respeta los horarios y
  la solicitud de no volver a llamar (el bot ya lo hace automáticamente).
- **Estados Unidos:** las llamadas con voz artificial requieren **consentimiento previo por escrito** (TCPA).
  No uses este bot para llamar a EE. UU. sin ese consentimiento.
- El bot **siempre dice que es un asistente virtual** y nunca lo niega. No lo cambies.
- No grabes las llamadas sin avisar (el bot no graba por defecto).
- Llama solo a negocios con los que tenga sentido hablar, y con volumen moderado: las llamadas masivas
  hacen que tu número sea marcado como spam.

## Pruebas

```bash
python -m pytest -q
```

## Estructura

```
bot/
  agent.py       Cerebro del bot: instrucciones y llamada a Claude
  server.py      Webhooks que usa Twilio durante la llamada
  cli.py         Comandos: importar, llamar, reporte, no-llamar, simular
  db.py          Base de datos SQLite
  compliance.py  Teléfonos y horarios permitidos
  config.py      Lee el .env y campana.toml
campana.toml     Tu guion de ventas (edítalo)
```
