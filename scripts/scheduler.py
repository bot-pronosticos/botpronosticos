"""
scripts/scheduler.py
Orquestador Autónomo 24/7 para Render.com:
- Ingesta de cuotas cada 6h.
- Escaneo de picks AUTOMÁTICO: 08:00 UTC (partidos de tarde) y 14:00 UTC (partidos de noche).
- Liquidación de resultados a las 23:30 UTC.
- Healthcheck HTTP para UptimeRobot.
- /picks en el bot es solo resumen diario — las tarjetas se envían solas.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from scripts.run_pipeline import run_pipeline
from bot.telegram_notifier import broadcast_picks, send_telegram_raw, get_current_bankroll
from ml.data_loader import load_all_history
from ml.predictor import predict_upcoming
from scripts.check_results import check_and_settle
from bot.bot_service import build_app


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "online", "service": "bot-pronosticos-ml"}')

    def log_message(self, format, *args):
        pass


def start_health_server(port: int = 8080):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Healthcheck HTTP activo en puerto {port}")
    server.serve_forever()


def job_harvest():
    """Ingesta cuotas y fixtures desde The Odds API."""
    logger.info("⏰ [Cron] Ingesta programada de cuotas y partidos...")
    try:
        run_pipeline()
    except Exception as e:
        logger.error(f"Error en job_harvest: {e}")


def job_auto_scan_and_send(window_label: str = ""):
    """
    Escaneo automático: busca picks con valor para las próximas 24h y los envía
    directamente al chat como tarjetas con botones.
    Se ejecuta a las 08:00 UTC (partidos de tarde) y 14:00 UTC (partidos de noche).
    """
    logger.info(f"⏰ [Cron] Escaneo automático de picks ({window_label})...")
    try:
        # Primero refrescar datos para tener cuotas actualizadas
        run_pipeline()

        df_hist = load_all_history()
        picks = predict_upcoming(df_hist, max_hours=24)

        if picks:
            sent = broadcast_picks(picks, max_to_send=5)
            logger.info(f"[Auto-scan {window_label}] Picks enviados: {sent}")
        else:
            logger.info(f"[Auto-scan {window_label}] Sin picks de valor en las próximas 24h.")
    except Exception as e:
        logger.error(f"Error en job_auto_scan_and_send ({window_label}): {e}")


def job_settlement():
    """Liquida resultados de partidos finalizados."""
    logger.info("⏰ [Cron] Liquidando resultados...")
    try:
        check_and_settle()
    except Exception as e:
        logger.error(f"Error en job_settlement: {e}")


def main():
    logger.info("=" * 60)
    logger.info("   INICIANDO BOT DE PRONÓSTICOS — MODO CLOUD 24/7")
    logger.info("=" * 60)

    # 1. Healthcheck HTTP
    port = int(os.getenv("PORT", "8080"))
    th_http = threading.Thread(target=start_health_server, args=(port,), daemon=True)
    th_http.start()

    # 2. Cron Scheduler
    scheduler = BackgroundScheduler()

    # Escaneo + envío automático de picks (refresca cuotas justo antes de predecir):
    # 10:00 UTC (05:00 COL) → picks europeos del día
    scheduler.add_job(
        job_auto_scan_and_send,
        CronTrigger(hour="10", minute="0"),
        kwargs={"window_label": "mañana"}
    )
    # 17:00 UTC (12:00 COL) → picks NBA nocturnos + Europa tarde
    scheduler.add_job(
        job_auto_scan_and_send,
        CronTrigger(hour="17", minute="0"),
        kwargs={"window_label": "tarde"}
    )

    # Liquidación de resultados (23:30 UTC / 18:30 COL)
    scheduler.add_job(job_settlement, CronTrigger(hour="23", minute="30"))

    scheduler.start()
    logger.info("Scheduler optimizado activo: escaneos 10:00 y 17:00 UTC · Liquidación 23:30 UTC")

    # Notificación de arranque
    bank = get_current_bankroll()
    send_telegram_raw(
        f"🤖 *Bot de Pronósticos · Versión Actualizada (v2.4.1)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Estado: `24/7 Cloud (Render)`\n"
        f"• Cobertura: *Fútbol (Top 5 Ligas) + NBA + Tenis*\n"
        f"• Banca sincronizada: `${bank:,.2f}`\n"
        f"• Escaneos automáticos: `10:00 y 17:00 UTC` (05:00 y 12:00 COL)\n"
        f"• Liquidación automática: `23:30 UTC` (18:30 COL)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ *Novedades de esta versión:*\n"
        f"• `/picks`: Resumen del día corregido y activo.\n"
        f"• Botones de apuesta ahora permiten cambiar de opinión (`🔄`).\n"
        f"• Cuota de API optimizada para rendir todo el mes.\n"
        f"• Sincronización de marcadores en vivo 100% automatizada."
    )

    # 3. Bot interactivo (hilo principal)
    logger.info("Iniciando escucha de Telegram (comandos + botones)...")
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
