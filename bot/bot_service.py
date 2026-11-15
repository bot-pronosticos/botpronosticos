"""
bot/bot_service.py
Servicio de bot interactivo de Telegram.
Maneja comandos (/start, /picks, /stats, /bankroll) y clics en botones de apuestas.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)
from loguru import logger

from config.settings import TELEGRAM_BOT_TOKEN
from bot.formatter import format_pick_message, format_stats_message
from bot.telegram_notifier import (
    get_current_bankroll, set_pick_placed_status, get_pick_placed_status, set_bankroll_value
)
from harvesters.db_client import select
from ml.data_loader import load_all_history
from ml.predictor import predict_upcoming

_history_cache = None


def get_cached_history():
    global _history_cache
    if _history_cache is None:
        _history_cache = load_all_history()
    return _history_cache


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    text = (
        f"👋 Hola {user}.\n\n"
        f"🤖 *Bot de Pronósticos Deportivos de Alta Precisión*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Cuotas sólidas (1.45 a 2.40) referenciadas en *Pinnacle / Bet365*.\n"
        f"• Gestión dinámica de bankroll con botones interactivos.\n\n"
        f"📌 *Comandos:*\n"
        f"• `/picks` — Ver los mejores pronósticos del día\n"
        f"• `/bankroll` — Ver saldo actual de tu banca\n"
        f"• `/stats` — Estadísticas acumuladas"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def picks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 *Consultando mejores pronósticos con valor...*", parse_mode="Markdown")
    try:
        df_hist = get_cached_history()
        picks = predict_upcoming(df_hist)

        if not picks:
            await update.message.reply_text(
                "🛡️ *Sin pronósticos por ahora.*\n\n"
                "Ningún partido actual supera el umbral de confianza y cuota estricta (1.45-2.40).",
                parse_mode="Markdown"
            )
            return

        current_bank = get_current_bankroll()
        for p in picks[:3]:
            msg = format_pick_message(p, bankroll=current_bank)
            pid = p.get("partido_id")
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Aposté a esta", callback_data=f"bet_placed:{pid}"),
                    InlineKeyboardButton("⏭️ No aposté", callback_data=f"bet_skipped:{pid}")
                ]
            ])
            await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en /picks: {e}")
        await update.message.reply_text(f"⚠️ Error: {e}")


async def handle_bet_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el clic en los botones 'Aposté' y 'No aposté'."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action, pick_id = data.split(":", 1) if ":" in data else (data, "")

    if action == "bet_placed":
        set_pick_placed_status(pick_id, True)
        new_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Apuesta Registrada en tu Banca", callback_data="none")]
        ])
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        logger.info(f"Usuario marcó apuesta realizada: {pick_id}")

    elif action == "bet_skipped":
        set_pick_placed_status(pick_id, False)
        new_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ En Seguimiento (Sin apostar)", callback_data="none")]
        ])
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        logger.info(f"Usuario marcó apuesta descartada: {pick_id}")


async def bankroll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_current_bankroll()
    text = (
        f"💼 *ESTADO DE TU BANCA*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 *Saldo Actual:* `${current:.2f}`\n"
        f"📐 *Gestión de riesgo:* `Quarter Kelly (Max 4% por pick)`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_Solo las apuestas marcadas con 'Aposté a esta' actualizan este saldo al finalizar el partido._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        picks_db = select("picks", limit=5000)
        total = len(picks_db)
        evaluados = [p for p in picks_db if p.get("resultado_correcto") is not None]
        aciertos  = sum(1 for p in evaluados if p.get("resultado_correcto") is True)
        pct = (aciertos / len(evaluados) * 100) if evaluados else 0.0
        roi_list = [float(p.get("ganancia_unidades", 0) or 0) for p in evaluados]
        roi = sum(roi_list)

        data = {
            "total_picks": total,
            "aciertos": aciertos,
            "pct_acierto": pct,
            "roi_unidades": roi,
        }
        msg = format_stats_message(data)
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


async def setbank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite al usuario fijar o reiniciar su bankroll con: /setbank 200"""
    if not context.args:
        current = get_current_bankroll()
        await update.message.reply_text(
            f"ℹ️ Tu banca actual es: `${current:.2f}`\n\n"
            f"Para cambiarla, escribe `/setbank` seguido de tu monto.\n"
            f"Ejemplo: `/setbank 500` o `/setbank 200000`",
            parse_mode="Markdown"
        )
        return

    try:
        raw_val = context.args[0].replace("$", "").replace(",", "").strip()
        new_bank = float(raw_val)
        if new_bank <= 0:
            await update.message.reply_text("⚠️ El monto debe ser un número positivo.")
            return

        set_bankroll_value(new_bank)
        await update.message.reply_text(
            f"💼 *Banca actualizada con éxito*\n\n"
            f"Nuevo saldo base: `${new_bank:,.2f}`\n"
            f"Todos los pronósticos y stakes sugeridos ahora se calcularán sobre este monto.",
            parse_mode="Markdown"
        )
        logger.info(f"Usuario actualizó bankroll a: {new_bank}")
    except ValueError:
        await update.message.reply_text("⚠️ Por favor ingresa un número válido. Ejemplo: `/setbank 300`", parse_mode="Markdown")


async def post_init(application):
    """Registra el menú de comandos nativo en Telegram."""
    from telegram import BotCommand
    commands = [
        BotCommand("picks", "🎯 Ver mejores pronósticos del día"),
        BotCommand("bankroll", "💼 Consultar saldo de banca actual"),
        BotCommand("setbank", "💵 Configurar nuevo monto de banca"),
        BotCommand("stats", "📈 Estadísticas y tasa de acierto"),
        BotCommand("help", "ℹ️ Ayuda e información del sistema"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Menú de comandos de Telegram registrado OK.")


def build_app():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("picks", picks_command))
    app.add_handler(CommandHandler("bankroll", bankroll_command))
    app.add_handler(CommandHandler("setbank", setbank_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CallbackQueryHandler(handle_bet_button))
    return app


def run_bot():
    logger.info("Iniciando servicio de bot interactivo...")
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    run_bot()
