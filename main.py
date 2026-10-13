"""
main.py
-------
Orquestador principal de monitor-normatividad.

Flujo de una ejecución (`run_once`):
  1. Consulta las normas más recientes publicadas en el sitio de la
     Alcaldía de Santa Rosa de Cabal (Risaralda).
  2. Compara sus IDs contra el registro local (seen_norms.json).
  3. Si hay normas nuevas, arma un correo HTML y lo envía vía Microsoft
     Graph a los destinatarios configurados.
  4. Actualiza el registro local solo si el correo se envió con éxito
     (para no "perder" una alerta si el envío falla).

Modos de ejecución:
  - `python main.py`           -> ejecuta una sola pasada (recomendado para
                                   cron / launchd, que ya controlan la
                                   periodicidad).
  - `python main.py --loop`    -> queda corriendo en primer plano y ejecuta
                                   una pasada cada hora usando la librería
                                   `schedule` (útil si no se quiere usar
                                   cron/launchd).
  - `python main.py --once`    -> equivalente explícito al modo por defecto.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

import schedule
from mailer import GraphMailer, GraphMailError
from plantillas import construir_html_normas
from scraper import obtener_normas_recientes
from storage import DEFAULT_PATH as SEEN_NORMS_PATH
from storage import cargar_vistos, guardar_vistos, registrar_ejecucion

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")


def configurar_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    nivel = os.getenv("LOG_LEVEL", "INFO").upper()

    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(nivel)

    # Consola (útil al ejecutar manualmente / ver salida de cron)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formato)
    root.addHandler(console)

    # Archivo rotativo: hasta 5 archivos de 1 MB cada uno
    archivo = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "app-normatividad.log"),
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    archivo.setFormatter(formato)
    root.addHandler(archivo)


logger = logging.getLogger("app-normatividad.main")


def run_once() -> int:
    """Ejecuta una pasada completa. Devuelve un código de salida (0 = éxito)."""
    load_dotenv()

    page_size = int(os.getenv("SCRAPE_PAGE_SIZE", "30"))

    tenant_id = os.getenv("MS_TENANT_ID")
    client_id = os.getenv("MS_CLIENT_ID")
    client_secret = os.getenv("MS_CLIENT_SECRET")
    mail_sender = os.getenv("MAIL_SENDER")
    mail_recipients = [
        addr for addr in (os.getenv("MAIL_RECIPIENTS") or "").split(",") if addr.strip()
    ]

    faltantes = [
        nombre
        for nombre, valor in {
            "MS_TENANT_ID": tenant_id,
            "MS_CLIENT_ID": client_id,
            "MS_CLIENT_SECRET": client_secret,
            "MAIL_SENDER": mail_sender,
            "MAIL_RECIPIENTS": mail_recipients,
        }.items()
        if not valor
    ]
    if faltantes:
        logger.error(
            "Faltan variables de entorno obligatorias en .env: %s", ", ".join(faltantes)
        )
        registrar_ejecucion(
            {
                "fecha": datetime.now().isoformat(timespec="seconds"),
                "resultado": "error_configuracion",
                "normas_nuevas": 0,
                "error": f"Faltan variables de entorno: {', '.join(faltantes)}",
            }
        )
        return 2

    try:
        logger.info("Iniciando ejecución: consultando normas recientes...")
        normas = obtener_normas_recientes(page_size=page_size)
    except Exception as exc:
        logger.exception("Error al consultar el sitio de normatividad")
        registrar_ejecucion(
            {
                "fecha": datetime.now().isoformat(timespec="seconds"),
                "resultado": "error_consulta",
                "normas_nuevas": 0,
                "error": str(exc),
            }
        )
        return 1

    registro = cargar_vistos(SEEN_NORMS_PATH)
    vistos_ids = set(registro["seen_ids"])

    nuevas = [n for n in normas if n.content_id not in vistos_ids]

    if not nuevas:
        logger.info("Sin novedades: no hay normas nuevas frente al registro local.")
        registrar_ejecucion(
            {
                "fecha": datetime.now().isoformat(timespec="seconds"),
                "resultado": "sin_novedades",
                "normas_nuevas": 0,
            }
        )
        return 0

    logger.info("Se encontraron %d norma(s) nueva(s): %s", len(nuevas), [n.titulo for n in nuevas])

    cuerpo_html = construir_html_normas(nuevas)
    asunto = (
        f"[Normatividad Santa Rosa de Cabal] {len(nuevas)} publicación(es) nueva(s)"
    )

    try:
        mailer = GraphMailer(tenant_id, client_id, client_secret)
        mailer.enviar_correo(
            sender=mail_sender,
            destinatarios=mail_recipients,
            asunto=asunto,
            cuerpo_html=cuerpo_html,
        )
    except (GraphMailError, ValueError, Exception) as exc:
        logger.exception(
            "No se pudo enviar el correo de alerta; no se marcarán las normas como "
            "vistas para reintentar en la próxima ejecución."
        )
        registrar_ejecucion(
            {
                "fecha": datetime.now().isoformat(timespec="seconds"),
                "resultado": "error_envio",
                "normas_nuevas": len(nuevas),
                "titulos": [n.titulo for n in nuevas],
                "error": str(exc),
            }
        )
        return 1

    # Solo se actualiza el registro si el correo se envió correctamente,
    # para no perder la alerta ante un fallo transitorio de Graph.
    for n in nuevas:
        vistos_ids.add(n.content_id)
        registro["detalle"][str(n.content_id)] = {
            "titulo": n.titulo,
            "tipo": n.tipo,
            "url_detalle": n.url_detalle,
            "notificado_en": datetime.now().isoformat(timespec="seconds"),
        }
    registro["seen_ids"] = sorted(vistos_ids)
    guardar_vistos(registro, SEEN_NORMS_PATH)
    logger.info("Registro local actualizado (%d IDs en total).", len(vistos_ids))

    registrar_ejecucion(
        {
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "resultado": "notificado",
            "normas_nuevas": len(nuevas),
            "titulos": [n.titulo for n in nuevas],
        }
    )

    return 0


def _modo_loop() -> None:
    logger.info("Modo --loop activo: se ejecutará una pasada cada hora (Ctrl+C para salir).")
    schedule.every(1).hours.do(run_once)
    run_once()  # primera ejecución inmediata
    while True:
        schedule.run_pending()
        time.sleep(30)


def main() -> None:
    configurar_logging()

    parser = argparse.ArgumentParser(
        description="Monitor de normatividad municipal con alertas por correo (MS Graph)."
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Ejecuta en primer plano, repitiendo cada hora (usa la librería 'schedule'). "
        "Si no se indica, se ejecuta una sola pasada (recomendado para cron/launchd).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta una sola pasada (comportamiento por defecto; explícito).",
    )
    args = parser.parse_args()

    if args.loop and not args.once:
        _modo_loop()
        return

    codigo_salida = run_once()
    sys.exit(codigo_salida)


if __name__ == "__main__":
    main()
