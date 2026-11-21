"""
scripts/scheduler.py
Orquestador Autónomo 24/7 para Render.com:
- Ingesta de cuotas y escaneos de picks AUTOMÁTICOS con criterio EV+:
    * 10:00 UTC (05:00 COL) -> Partidos de tarde (Fútbol Europeo, Tenis).
    * 16:00 UTC (11:00 COL) -> Partidos de noche europea.
    * 22:00 UTC (17:00 COL) -> Partidos nocturnos (NBA / NFL).
- Liquidación automática continua cada 2 horas via ESPN Scoreboard (sin límite de API).
- Healthcheck HTTP para UptimeRobot en $PORT.
- Interfaz interactiva de Telegram (/picks, /proximos, /bankroll, /stats, botones interactivos).
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

from harvesters.odds_harvester import harvest_odds, save_odds_to_db
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
        self.wfile.write(b'{"status": "online", "service": "bot-pronosticos-ml", "version": "3.0.0"}')

    def log_message(self, format, *args):
        pass


def start_health_server(port: int = 8080):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Healthcheck HTTP activo en puerto {port}")
    server.serve_forever()


def job_harvest():
    """Ingesta cuotas y fixtures desde feeds públicos ilimitados."""
    logger.info("⏰ [Cron] Ingesta programada de cuotas...")
    try:
        events = harvest_odds(force_refresh=True)
        saved = save_odds_to_db(events)
        logger.info(f"[Cron Harvest] {len(events)} eventos obtenidos, {saved} sincronizados.")
    except Exception as e:
        logger.error(f"Error en job_harvest: {e}")


def job_auto_scan_and_send(window_label: str = ""):
    """
    Escaneo automático: busca picks con valor para las próximas 24h y los emite
    directamente al chat como tarjetas interactivas con botones.
    """
    logger.info(f"⏰ [Cron] Escaneo automático de picks ({window_label})...")
    try:
        # Refrescar cuotas en vivo
        events = harvest_odds(force_refresh=True)
        save_odds_to_db(events)

        df_hist = load_all_history()
        picks = predict_upcoming(df_hist, max_hours=24)

        if picks:
            sent = broadcast_picks(picks, max_to_send=4)
            logger.info(f"[Auto-scan {window_label}] Picks nuevos enviados a Telegram: {sent}")
        else:
            logger.info(f"[Auto-scan {window_label}] Sin picks nuevos de valor en las próximas 24h.")
    except Exception as e:
        logger.error(f"Error en job_auto_scan_and_send ({window_label}): {e}")


def job_settlement():
    """Liquida resultados de partidos finalizados y actualiza bankroll."""
    logger.info("⏰ [Cron] Verificación y liquidación de resultados...")
    try:
        check_and_settle()
    except Exception as e:
        logger.error(f"Error en job_settlement: {e}")


def main():
    logger.info("=" * 60)
    logger.info("   INICIANDO BOT DE PRONÓSTICOS — ARQUITECTURA v3.0 (EV+)")
    logger.info("=" * 60)

    # 1. Healthcheck HTTP para UptimeRobot
    port = int(os.getenv("PORT", "8080"))
    th_http = threading.Thread(target=start_health_server, args=(port,), daemon=True)
    th_http.start()

    # 2. Liquidación inicial de pendientes al arrancar
    try:
        job_settlement()
    except Exception as e:
        logger.warning(f"Error en liquidación de arranque: {e}")

    # 3. Cron Scheduler
    scheduler = BackgroundScheduler()

    # Ingesta periódica de cuotas frescas cada 4 horas
    scheduler.add_job(job_harvest, CronTrigger(hour="*/4", minute="5"))

    # Escaneos automáticos de picks en ventanas estratégicas (hora Colombia: -5h UTC):
    # 10:00 UTC (05:00 COL) -> Jornada de mañana/tarde europea
    scheduler.add_job(
        job_auto_scan_and_send,
        CronTrigger(hour="10", minute="0"),
        kwargs={"window_label": "Mañana Europea"}
    )
    # 16:00 UTC (11:00 COL) -> Tarde/Noche europea
    scheduler.add_job(
        job_auto_scan_and_send,
        CronTrigger(hour="16", minute="0"),
        kwargs={"window_label": "Tarde Europea"}
    )
    # 22:00 UTC (17:00 COL) -> NBA & Noche Americana
    scheduler.add_job(
        job_auto_scan_and_send,
        CronTrigger(hour="22", minute="0"),
        kwargs={"window_label": "Noche NBA"}
    )

    # Liquidación de resultados cada 2 horas (00:30, 02:30, 04:30 ... UTC)
    scheduler.add_job(job_settlement, CronTrigger(hour="*/2", minute="30"))

    scheduler.start()
    logger.info("Scheduler activo: Escaneos 10:00, 16:00 y 22:00 UTC · Liquidaciones cada 2h.")

    # Notificación de arranque operativo a Telegram
    bank = get_current_bankroll()
    send_telegram_raw(
        f"🚀 *Bot de Pronósticos Multideporte Activo (v3.0.0)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Estado: `24/7 Cloud (Render + UptimeRobot)`\n"
        f"• Cobertura: *Fútbol (Top 5 Ligas) + NBA + NFL + Tenis*\n"
        f"• Motor: *Modelo Matemático EV+ & Quarter Kelly*\n"
        f"• Banca sincronizada: `${bank:,.2f}`\n"
        f"• Escaneos automáticos: `10:00, 16:00 y 22:00 UTC`\n"
        f"• Liquidación continua: `Cada 2 horas` vía ESPN API\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 _Escribe /picks para consultar el resumen o emitir las mejores oportunidades de hoy._"
    )

    # 4. Bot interactivo (hilo principal)
    logger.info("Iniciando escucha de Telegram (comandos + callbacks)...")
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
