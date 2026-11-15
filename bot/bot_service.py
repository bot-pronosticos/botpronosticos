"""
bot/bot_service.py
Servicio interactivo de Telegram multideporte (Fútbol, NBA, NFL):
- /picks: Pronósticos con valor matemático para hoy (ventana 24h).
- /proximos: Pronósticos de la próxima jornada (hasta 7 días).
- /bankroll: Saldo y gestión de riesgo.
- /setbank: Fijar nuevo capital.
- /stats: Rendimiento acumulado.
- Cero repeticiones de cuotas gracias a deduplicación en Supabase.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)
from loguru import logger
import pandas as pd
from datetime import timezone

from config.settings import TELEGRAM_BOT_TOKEN
from bot.formatter import format_pick_message, format_stats_message
from bot.telegram_notifier import (
    get_current_bankroll, set_pick_placed_status, get_pick_placed_status,
    set_bankroll_value, get_already_sent_keys, save_pick_meta
)
from harvesters.db_client import select, insert, update
from ml.data_loader import load_all_history
from ml.predictor import predict_upcoming, format_kickoff_label

_history_cache = None


def get_cached_history():
    global _history_cache
    if _history_cache is None:
        try:
            _history_cache = load_all_history()
        except Exception as e:
            logger.warning(f"Error cargando historial de fútbol: {e}")
            _history_cache = None
    return _history_cache


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    text = (
        f"👋 *Hola {user}*\n\n"
        f"🤖 *Bot de Pronósticos Multideporte · Modelo ML EV+*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Cobertura: *Fútbol, Baloncesto (NBA) & Tenis (ATP)*\n"
        f"• Mercado transversal: *Ganador / Moneyline (máxima liquidez, menor comisión)*\n"
        f"• Cuotas de referencia: *Pinnacle / 1xBet / Betfair*\n"
        f"• Rango estricto de cuotas: `1.45 a 2.45`\n"
        f"• Gestión de riesgo: `Quarter Kelly (máx 4% por pick)`\n\n"
        f"📌 *Comandos Disponibles:*\n"
        f"• `/picks` — Pronósticos de la jornada de *hoy*\n"
        f"• `/proximos` — Pronósticos anticipados de la próxima fecha\n"
        f"• `/bankroll` — Consultar saldo actual de tu banca\n"
        f"• `/setbank 500` — Establecer o reiniciar tu banca\n"
        f"• `/stats` — Estadísticas y tasa de acierto del modelo\n"
        f"• `/help` — Ver esta guía\n\n"
        f"_Pulsa los botones en cada pick para registrar si apostaste o si solo lo sigues en observación._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def picks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Inspección diaria: partidos de hoy (próximas 24h).
    """
    wait_msg = await update.message.reply_text(
        "🔍 *Inspeccionando partidos de hoy (Fútbol, NBA y Tenis)...*", parse_mode="Markdown"
    )

    try:
        df_hist = get_cached_history()
        today_picks = predict_upcoming(df_hist, max_hours=24)

        await wait_msg.delete()

        if not today_picks:
            await update.message.reply_text(
                "📭 *Sin pronósticos de alto valor para hoy*\n\n"
                "El modelo evaluó la jornada de hoy (Fútbol, NBA y Tenis) pero ningún encuentro "
                "supera los filtros estrictos de valor (cuota 1.45–2.45 · EV positivo · prob ≥ 44%).\n\n"
                "🛡️ *Disciplina:* No forzamos apuestas cuando no existe ventaja matemática sobre la casa.\n"
                "👉 Si deseas consultar partidos de los próximos días, escribe `/proximos`.",
                parse_mode="Markdown"
            )
            return

        already_sent = get_already_sent_keys()
        new_picks = [
            p for p in today_picks
            if (p["partido_id"], p["mercado"], p["outcome"]) not in already_sent
        ]

        if not new_picks:
            await update.message.reply_text(
                "✅ *Pronósticos de hoy al día*\n\n"
                "Ya has recibido todos los pronósticos disponibles para los partidos de hoy. "
                "Revisa los mensajes anteriores en este chat para marcar tus apuestas.\n\n"
                f"💼 Consulta tu banca con `/bankroll` o espera a la liquidación nocturna.",
                parse_mode="Markdown"
            )
            return

        picks_to_send = new_picks[:3]
        await update.message.reply_text(
            f"🎯 *{len(picks_to_send)} pronóstico(s) con valor para hoy*\n"
            f"_Referencia: Pinnacle / 1xBet · Criterio EV+_",
            parse_mode="Markdown"
        )

        current_bank = get_current_bankroll()
        for p in picks_to_send:
            msg = format_pick_message(p, bankroll=current_bank)

            pick_record = {
                "partido_id":     p["partido_id"],
                "mercado":        p["mercado"],
                "outcome":        p["outcome"],
                "prob_modelo":    p["prob_modelo"],
                "prob_implicita": p["prob_implicita"],
                "ev":             p["ev"],
                "cuota":          p["cuota"],
                "bookmaker":      p["bookmaker"],
                "kelly_fraccion": p["kelly"],
            }
            res = insert("picks", [pick_record])
            if not res:
                continue

            pick_id = res[0]["id"]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Aposté a esta", callback_data=f"bet_placed:{pick_id}"),
                InlineKeyboardButton("⏭️ No aposté",    callback_data=f"bet_skipped:{pick_id}")
            ]])
            sent_msg = await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
            save_pick_meta(pick_id=pick_id, msg_id=sent_msg.message_id)

    except Exception as e:
        logger.error(f"Error en /picks: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"⚠️ Error procesando pronósticos: {e}")


async def proximos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Pronósticos anticipados de los próximos 7 días.
    """
    wait_msg = await update.message.reply_text(
        "📅 *Consultando pronósticos anticipados de la próxima fecha...*", parse_mode="Markdown"
    )
    try:
        df_hist = get_cached_history()
        future_picks = predict_upcoming(df_hist, max_hours=168)

        await wait_msg.delete()

        if not future_picks:
            await update.message.reply_text(
                "📭 *Sin pronósticos programados*\n\n"
                "No hay partidos con cuotas de referencia en los próximos 7 días.",
                parse_mode="Markdown"
            )
            return

        already_sent = get_already_sent_keys()
        new_picks = [
            p for p in future_picks
            if (p["partido_id"], p["mercado"], p["outcome"]) not in already_sent
        ]

        if not new_picks:
            await update.message.reply_text(
                "✅ *Pronósticos de la próxima fecha al día*\n\n"
                "Los pronósticos de la próxima jornada ya fueron emitidos en el chat.",
                parse_mode="Markdown"
            )
            return

        picks_to_send = new_picks[:3]
        await update.message.reply_text(
            f"📅 *PRONÓSTICOS ANTICIPADOS (Próxima Jornada)*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ _Nota: Estos partidos se disputan en los próximos días. Las cuotas y alineaciones pueden presentar variaciones._",
            parse_mode="Markdown"
        )

        current_bank = get_current_bankroll()
        for p in picks_to_send:
            msg = format_pick_message(p, bankroll=current_bank)

            pick_record = {
                "partido_id":     p["partido_id"],
                "mercado":        p["mercado"],
                "outcome":        p["outcome"],
                "prob_modelo":    p["prob_modelo"],
                "prob_implicita": p["prob_implicita"],
                "ev":             p["ev"],
                "cuota":          p["cuota"],
                "bookmaker":      p["bookmaker"],
                "kelly_fraccion": p["kelly"],
            }
            res = insert("picks", [pick_record])
            if not res:
                continue

            pick_id = res[0]["id"]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Aposté a esta", callback_data=f"bet_placed:{pick_id}"),
                InlineKeyboardButton("⏭️ No aposté",    callback_data=f"bet_skipped:{pick_id}")
            ]])
            sent_msg = await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
            save_pick_meta(pick_id=pick_id, msg_id=sent_msg.message_id)

    except Exception as e:
        logger.error(f"Error en /proximos: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"⚠️ Error: {e}")


async def handle_bet_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if ":" not in data:
        await query.answer()
        return

    action, pick_id = data.split(":", 1)

    if action == "bet_placed":
        set_pick_placed_status(pick_id, True)
        await query.answer("✅ Apuesta registrada en tu banca")
        new_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Registrada en tu banca", callback_data="none")
        ]])
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        logger.info(f"Pick {pick_id[:8]}: MARCADO COMO APOSTADO")

    elif action == "bet_skipped":
        set_pick_placed_status(pick_id, False)
        await query.answer("⏭️ En observación. Tu banca no variará.")
        new_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭️ En observación (sin apostar)", callback_data="none")
        ]])
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        logger.info(f"Pick {pick_id[:8]}: MARCADO COMO NO APOSTADO")


async def bankroll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_current_bankroll()
    text = (
        f"💼 *ESTADO DE TU BANCA*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 *Saldo disponible:* `${current:,.2f}`\n"
        f"📐 *Gestión de riesgo:* `Quarter Kelly (máx 4.0% por pick)`\n"
        f"🛡️ *Protección:* Solo los picks marcados con `✅ Aposté` impactan este saldo.\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 _Para modificar tu saldo base, escribe:_ `/setbank 500`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def setbank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        current = get_current_bankroll()
        await update.message.reply_text(
            f"💼 *Tu saldo actual es:* `${current:,.2f}`\n\n"
            f"Para fijar un nuevo capital escribe `/setbank` seguido del valor.\n"
            f"Ejemplo: `/setbank 500` o `/setbank 2000`",
            parse_mode="Markdown"
        )
        return

    try:
        raw_val = context.args[0].replace("$", "").replace(",", "").strip()
        new_bank = float(raw_val)
        if new_bank <= 0:
            await update.message.reply_text("⚠️ El saldo debe ser un número positivo.")
            return

        set_bankroll_value(new_bank)
        await update.message.reply_text(
            f"💼 *Banca configurada correctamente*\n\n"
            f"Nuevo saldo base: `${new_bank:,.2f}`\n"
            f"Todos los stakes sugeridos se calcularán sobre este capital.",
            parse_mode="Markdown"
        )
        logger.info(f"Bankroll actualizado a: ${new_bank:.2f}")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Formato inválido. Ingresa solo el número. Ejemplo: `/setbank 300`",
            parse_mode="Markdown"
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        picks_db = select("picks", limit=5000)
        total = len(picks_db)
        evaluados = [p for p in picks_db if p.get("resultado_correcto") is not None]
        apostados = []
        for p in evaluados:
            st = get_pick_placed_status(p.get("id"))
            if st is True:
                apostados.append(p)

        aciertos    = sum(1 for p in evaluados if p.get("resultado_correcto") is True)
        aciertos_ap = sum(1 for p in apostados if p.get("resultado_correcto") is True)
        pct = (aciertos / len(evaluados) * 100) if evaluados else 0.0
        roi_list = [float(p.get("ganancia_unidades", 0) or 0) for p in evaluados]
        roi = sum(roi_list)

        data = {
            "total_picks":        total,
            "evaluados":          len(evaluados),
            "apostados":          len(apostados),
            "aciertos":           aciertos,
            "aciertos_apostados": aciertos_ap,
            "pct_acierto":        pct,
            "roi_unidades":       roi,
        }
        msg = format_stats_message(data)
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error consultando estadísticas: {e}")


async def post_init(application):
    from telegram import BotCommand
    commands = [
        BotCommand("picks",    "🎯 Pronósticos de hoy (Fútbol, NBA, Tenis)"),
        BotCommand("proximos", "📅 Pronósticos anticipados próxima jornada"),
        BotCommand("bankroll", "💼 Consultar saldo de banca actual"),
        BotCommand("setbank",  "💵 Configurar monto de banca (ej: /setbank 500)"),
        BotCommand("stats",    "📈 Estadísticas de acierto del modelo"),
        BotCommand("help",     "ℹ️ Información y comandos"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Menú de comandos de Telegram registrado OK.")


def build_app():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",    start_command))
    app.add_handler(CommandHandler("picks",    picks_command))
    app.add_handler(CommandHandler("proximos", proximos_command))
    app.add_handler(CommandHandler("bankroll", bankroll_command))
    app.add_handler(CommandHandler("setbank",  setbank_command))
    app.add_handler(CommandHandler("stats",    stats_command))
    app.add_handler(CommandHandler("help",     start_command))
    app.add_handler(CallbackQueryHandler(handle_bet_button))
    return app


def run_bot():
    logger.info("Iniciando servicio interactivo de Telegram...")
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    run_bot()
