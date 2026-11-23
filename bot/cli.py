"""Línea de comandos: importar prospectos, lanzar llamadas, ver resultados y probar el bot."""

from __future__ import annotations

import argparse
import csv
import sys
import time

from . import db
from .compliance import normalize_phone, within_calling_hours
from .config import load_campaign, settings


def cmd_importar(args: argparse.Namespace) -> None:
    ok, malos = 0, 0
    with open(args.archivo, newline="", encoding="utf-8-sig") as f, db.connect() as conn:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            telefono = normalize_phone(row.get("telefono", ""), settings.default_country_code)
            if not telefono or not row.get("negocio"):
                malos += 1
                print(f"  Saltado (falta negocio o teléfono inválido): {row}")
                continue
            db.upsert_lead(conn, {**row, "telefono": telefono})
            ok += 1
    print(f"Importados: {ok}. Saltados: {malos}.")


def cmd_llamar(args: argparse.Namespace) -> None:
    if not args.ignorar_horario and not within_calling_hours(
        None, settings.timezone, settings.call_start_hour, settings.call_end_hour, settings.call_days
    ):
        sys.exit(
            f"Fuera del horario permitido ({settings.call_start_hour}:00-{settings.call_end_hour}:00, "
            f"días {settings.call_days}, zona {settings.timezone}). Usa --ignorar-horario para pruebas."
        )

    with db.connect() as conn:
        leads = db.pending_leads(conn, max_attempts=args.intentos)[: args.limite]
    if not leads:
        print("No hay prospectos pendientes.")
        return

    if args.prueba:
        for lead in leads:
            print(f"[prueba] Llamaría a {lead['negocio']} ({lead['telefono']})")
        return

    missing = [n for n in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "PUBLIC_BASE_URL")
               if not getattr(settings, n.lower())]
    if missing:
        sys.exit(f"Faltan variables en .env: {', '.join(missing)}")

    from twilio.rest import Client

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    base = settings.public_base_url
    for i, lead in enumerate(leads, 1):
        with db.connect() as conn:
            if db.is_do_not_call(conn, lead["telefono"]):
                continue
        try:
            call = client.calls.create(
                to=lead["telefono"],
                from_=settings.twilio_from_number,
                url=f"{base}/twilio/voice?lead_id={lead['id']}",
                method="POST",
                machine_detection="Enable",
                status_callback=f"{base}/twilio/status",
                status_callback_method="POST",
                status_callback_event=["completed"],
                timeout=25,
            )
        except Exception as e:  # noqa: BLE001 - se reporta y se sigue con el siguiente
            print(f"  [{i}/{len(leads)}] Error llamando a {lead['negocio']}: {e}")
            continue
        with db.connect() as conn:
            db.create_call(conn, call.sid, lead["id"])
        print(f"  [{i}/{len(leads)}] Llamando a {lead['negocio']} ({lead['telefono']}) -> {call.sid}")
        if i < len(leads):
            time.sleep(args.pausa)


def cmd_reporte(args: argparse.Namespace) -> None:
    with db.connect() as conn:
        rows = db.report(conn)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        print(f"Guardado {len(rows)} llamadas en {args.csv}")
        return
    if not rows:
        print("Aún no hay llamadas.")
        return
    for r in rows:
        print(f"{r['creado']}  {r['negocio'] or '?':<30} {r['telefono'] or '':<15} "
              f"{r['resultado']:<17} {r['cita'] or '':<25} {r['notas'] or ''}")


def cmd_no_llamar(args: argparse.Namespace) -> None:
    telefono = normalize_phone(args.telefono, settings.default_country_code)
    if not telefono:
        sys.exit("Teléfono inválido")
    with db.connect() as conn:
        db.add_do_not_call(conn, telefono, args.motivo)
    print(f"{telefono} agregado a la lista de no llamar.")


def cmd_simular(args: argparse.Namespace) -> None:
    """Conversa con el bot por texto, como si tú fueras el negocio. No hace llamadas reales."""
    from .agent import greeting, initial_history, make_agent

    agent = make_agent(load_campaign())
    lead = {"negocio": args.negocio, "contacto": args.contacto, "ciudad": "", "notas": ""}
    text = greeting(agent.campaign, lead)
    history = initial_history(text)
    print("Simulación (escribe lo que diría el negocio; Enter vacío = silencio; Ctrl+C para salir)\n")
    print(f"BOT: {text}")
    while True:
        try:
            heard = input("NEGOCIO: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        turn, history = agent.next_turn(lead, history, heard)
        print(f"BOT: {turn.decir}")
        print(f"     [{turn.accion} | {turn.resultado}{' | cita: ' + turn.cita if turn.cita else ''}]")
        if turn.accion != "continuar":
            print(f"\nNotas: {turn.notas}")
            return


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m bot", description="Bot de llamadas de ventas")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("importar", help="Importar prospectos desde un CSV")
    s.add_argument("archivo")
    s.set_defaults(func=cmd_importar)

    s = sub.add_parser("llamar", help="Llamar a los prospectos pendientes")
    s.add_argument("--limite", type=int, default=10, help="Máximo de llamadas (default 10)")
    s.add_argument("--pausa", type=float, default=30, help="Segundos entre llamadas (default 30)")
    s.add_argument("--intentos", type=int, default=3, help="Máximo de intentos por prospecto")
    s.add_argument("--prueba", action="store_true", help="Solo muestra a quién llamaría")
    s.add_argument("--ignorar-horario", action="store_true", help="Permite llamar fuera de horario (pruebas)")
    s.set_defaults(func=cmd_llamar)

    s = sub.add_parser("reporte", help="Ver resultados de las llamadas")
    s.add_argument("--csv", help="Guardar en un archivo CSV")
    s.set_defaults(func=cmd_reporte)

    s = sub.add_parser("no-llamar", help="Agregar un teléfono a la lista de no llamar")
    s.add_argument("telefono")
    s.add_argument("--motivo", default="Agregado manualmente")
    s.set_defaults(func=cmd_no_llamar)

    s = sub.add_parser("simular", help="Probar la conversación por texto, sin llamar")
    s.add_argument("--negocio", default="Taquería El Güero")
    s.add_argument("--contacto", default="")
    s.set_defaults(func=cmd_simular)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
