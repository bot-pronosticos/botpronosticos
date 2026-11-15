"""
scripts/scheduler.py
Fase 5: Orquestador Autónomo para despliegue en la nube (Railway / Render).
- Ejecuta el bot interactivo de Telegram (escucha /picks, /bankroll y botones de apuesta).
- Ejecuta APScheduler en background (ingesta cada 6h, alertas de picks a las 07:00 y 13:00 UTC, liquidación a las 23:30 UTC).
- Mantiene servidor HTTP de healthcheck para UptimeRobot / Railway.
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
    logger.info("⏰ [Cron] Ingesta programada de cuotas y partidos...")
    try:
        run_pipeline()
    except Exception as e:
        logger.error(f"Error en job_harvest: {e}")


def job_predict_and_alert():
    logger.info("⏰ [Cron] Evaluando pronósticos del día...")
    try:
        df_hist = load_all_history()
        picks = predict_upcoming(df_hist)
        if picks:
            sent = broadcast_picks(picks, max_to_send=3)
            logger.info(f"Picks emitidos con botones: {sent}")
        else:
            logger.info("Sin pronósticos de alto valor en este ciclo.")
    except Exception as e:
        logger.error(f"Error en job_predict_and_alert: {e}")


def job_settlement():
    logger.info("⏰ [Cron] Liquidando resultados...")
    try:
        check_and_settle()
    except Exception as e:
        logger.error(f"Error en job_settlement: {e}")


def main():
    logger.info("=" * 60)
    logger.info("   INICIANDO BOT DE PRONÓSTICOS — MODO CLOUD 24/7")
    logger.info("=" * 60)

    # 1. Healthcheck HTTP Server
    port = int(os.getenv("PORT", "8080"))
    th_http = threading.Thread(target=start_health_server, args=(port,), daemon=True)
    th_http.start()

    # 2. Cron Scheduler en Background
    scheduler = BackgroundScheduler()
    # Ingesta cada 6 horas (00:15, 06:15, 12:15, 18:15 UTC)
    scheduler.add_job(job_harvest, CronTrigger(hour="0,6,12,18", minute="15"))
    # Alertas pre-jornada (07:00 y 13:00 UTC)
    scheduler.add_job(job_predict_and_alert, CronTrigger(hour="7,13", minute="00"))
    # Liquidación de resultados (23:30 UTC)
    scheduler.add_job(job_settlement, CronTrigger(hour="23", minute="30"))
    scheduler.start()
    logger.info("Scheduler de fondo iniciado correctamente.")

    # Notificación de arranque a Telegram
    bank = get_current_bankroll()
    send_telegram_raw(
        f"🚀 *Sistema de Pronósticos Iniciado en la Nube*\n\n"
        f"• Estado: `24/7 Activo`\n"
        f"• Banca actual: `${bank:.2f}`\n"
        f"• Botones de registro listos en cada pick."
    )

    # 3. Bot interactivo de Telegram en hilo principal (escucha comandos y botones)
    logger.info("Iniciando escucha continua de botones y comandos en Telegram...")
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
