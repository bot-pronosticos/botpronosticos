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
        f"• `/picks` — Resumen del día: qué se envió, qué apostaste, estado de banca\n"
        f"• `/proximos` — Pronósticos anticipados de la próxima jornada\n"
        f"• `/bankroll` — Consultar saldo actual de tu banca\n"
        f"• `/setbank 500` — Establecer o reiniciar tu banca\n"
        f"• `/stats` — Estadísticas y tasa de acierto del modelo\n"
        f"• `/help` — Ver esta guía\n\n"
        f"_Pulsa los botones en cada pick para registrar si apostaste o si solo lo sigues en observación._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def picks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Inspección de partidos de HOY (próximas 24 horas):
    - Si encuentra picks de alto valor que no se hayan emitido, envía las tarjetas interactivas.
    - Muestra el resumen compacto de los pronósticos de hoy y el estado de la banca.
    - Partidos de otras fechas (como Sevilla vs Betis) NUNCA se muestran aquí.
    """
    wait_msg = await update.message.reply_text(
        "🔍 *Inspeccionando partidos de hoy (próximas 24h)...*", parse_mode="Markdown"
    )

    try:
        df_hist = get_cached_history()
        # 1. Escaneo estricto de las próximas 24h
        today_picks = predict_upcoming(df_hist, max_hours=24)

        already_sent = get_already_sent_keys()
        new_picks = [
            p for p in today_picks
            if (p["partido_id"], p["mercado"], p["outcome"]) not in already_sent
        ]

        current_bank = get_current_bankroll()

        # Si hay picks nuevos de hoy que no se hayan enviado, emitir las tarjetas
        if new_picks:
            picks_to_send = new_picks[:3]
            await update.message.reply_text(
                f"🎯 *{len(picks_to_send)} nuevo(s) pronóstico(s) para hoy*\n"
                f"_Referencia: Pinnacle / 1xBet · Criterio EV+_",
                parse_mode="Markdown"
            )
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

        # 2. Consultar picks en BD cuyo kickoff sea efectivamente HOY (ventana 24h)
        all_picks_db = select("picks", limit=500)
        partidos_db = {p["id"]: p for p in select("partidos", limit=1000)}
        now_utc = pd.Timestamp.now(tz="UTC")
        today_str = now_utc.strftime("%Y-%m-%d")

        today_active_picks = []
        for p in all_picks_db:
            pid = p.get("partido_id")
            partido = partidos_db.get(pid)
            if not partido and pid:
                fetched = select("partidos", {"id": pid}, limit=1)
                if fetched:
                    partido = fetched[0]
                    partidos_db[pid] = partido

            if not partido:
                continue
            ko_str = partido.get("kickoff", "")
            if not ko_str:
                continue
            try:
                dt_k = pd.Timestamp(ko_str)
                if dt_k.tzinfo is None:
                    dt_k = dt_k.tz_localize("UTC")
                else:
                    dt_k = dt_k.tz_convert("UTC")
                # Solo incluir partidos de hoy (kickoff en ventana de 24h o disputados hoy)
                if dt_k.strftime("%Y-%m-%d") == today_str or (0 <= (dt_k - now_utc).total_seconds() <= 86400):
                    today_active_picks.append((p, partido))
            except Exception:
                continue

        await wait_msg.delete()

        # Si no hay ningún pick hoy
        if not today_active_picks and not new_picks:
            await update.message.reply_text(
                "📭 *Sin pronósticos de alto valor para hoy*\n\n"
                "El modelo evaluó la jornada de las próximas 24h (Fútbol, NBA y Tenis) "
                "pero ningún encuentro supera los filtros estrictos de valor (cuota 1.45–2.45 · EV positivo · prob ≥ 44%).\n\n"
                "🛡️ *Disciplina:* No forzamos apuestas si no existe ventaja matemática sobre la casa.\n"
                "👉 Para consultar partidos de los próximos días, escribe `/proximos`.",
                parse_mode="Markdown"
            )
            return

        # Si hay picks activos de hoy, mostrar resumen compacto
        if today_active_picks:
            lines = []
            apostados, no_apostados, pendientes = 0, 0, 0
            SPORT_ICONS = {"football": "⚽", "basketball": "🏀", "tennis": "🎾", "americano": "🏈"}

            for p, partido in today_active_picks:
                pick_id   = p.get("id", "")
                outcome   = p.get("outcome", "")
                cuota     = float(p.get("cuota", 0))
                ev        = float(p.get("ev", 0)) * 100
                resultado = p.get("resultado_correcto")

                status = get_pick_placed_status(pick_id)
                if status is True:
                    apostados += 1
                    estado_icon = "✅"
                    estado_txt  = "Apostada"
                elif status is False:
                    no_apostados += 1
                    estado_icon = "⏭️"
                    estado_txt  = "Observación"
                else:
                    pendientes += 1
                    estado_icon = "⏳"
                    estado_txt  = "Pendiente"

                result_txt = ""
                if resultado is True:
                    result_txt = " · 🟢 Acertada"
                elif resultado is False:
                    result_txt = " · 🔴 Fallada"

                local     = partido.get("equipo_local", "")
                visitante = partido.get("equipo_visitante", "")
                deporte   = partido.get("deporte", "football")
                sport_icon = SPORT_ICONS.get(deporte, "🎯")
                matchup   = f"{local} vs {visitante}"

                outcome_txt = "Local" if outcome == "H" else ("Empate" if outcome == "D" else "Visitante")
                lines.append(
                    f"{estado_icon} {sport_icon} *{matchup}*\n"
                    f"   `{outcome_txt}` @ `{cuota:.2f}` · EV `+{ev:.1f}%` · {estado_txt}{result_txt}"
                )

            summary_header = (
                f"📋 *RESUMEN DE HOY — {today_str}*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• Pronósticos de hoy: `{len(today_active_picks)}`\n"
                f"• ✅ Apostadas: `{apostados}` · ⏭️ Observación: `{no_apostados}` · ⏳ Sin respuesta: `{pendientes}`\n"
                f"• 💼 Banca actual: `${current_bank:,.2f}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
            await update.message.reply_text(summary_header + "\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en /picks: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"⚠️ Error procesando /picks: {e}")


async def proximos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Pronósticos anticipados (próximos 7 días) — solo preview, NO guarda en picks table.
    Así el auto-scanner puede enviar la tarjeta real el día del partido sin deduplicar.
    """
    wait_msg = await update.message.reply_text(
        "📅 *Consultando pronósticos de los próximos 7 días...*", parse_mode="Markdown"
    )
    try:
        df_hist = get_cached_history()
        future_picks = predict_upcoming(df_hist, max_hours=168)

        await wait_msg.delete()

        if not future_picks:
            await update.message.reply_text(
                "📭 *Sin pronósticos en los próximos 7 días*\n\n"
                "No hay partidos con cuotas de referencia disponibles aún.",
                parse_mode="Markdown"
            )
            return

        # Excluir picks de hoy (esos los maneja el auto-scanner, no /proximos)
        now_utc = pd.Timestamp.now(tz="UTC")
        future_only = []
        for p in future_picks:
            ko_str = p.get("kickoff", "")
            if not ko_str:
                continue
            try:
                dt_k = pd.Timestamp(ko_str)
                if dt_k.tzinfo is None:
                    dt_k = dt_k.tz_localize("UTC")
                else:
                    dt_k = dt_k.tz_convert("UTC")
                if dt_k > now_utc + pd.Timedelta(hours=24):
                    future_only.append(p)
            except Exception:
                continue

        if not future_only:
            await update.message.reply_text(
                "📭 *Sin partidos anticipados disponibles*\n\n"
                "Solo hay partidos de hoy — esos llegan solos via el escaneo automático.",
                parse_mode="Markdown"
            )
            return

        await update.message.reply_text(
            f"📅 *ANTICIPOS — Próximos 7 días* ({len(future_only[:5])} picks)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ _Vista previa: las tarjetas definitivas llegarán solas el día del partido con cuotas actualizadas._",
            parse_mode="Markdown"
        )

        current_bank = get_current_bankroll()
        for p in future_only[:5]:
            # Solo mandamos la tarjeta formateada — SIN insertar en picks table
            msg = format_pick_message(p, bankroll=current_bank)
            await update.message.reply_text(msg, parse_mode="Markdown")

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
        await query.answer("✅ Registrada como APOSTADA")
        new_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Apostada · 🔄 Cambiar a No aposté", callback_data=f"bet_skipped:{pick_id}")
        ]])
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        logger.info(f"Pick {pick_id[:8]}: MARCADO COMO APOSTADO")

    elif action == "bet_skipped":
        set_pick_placed_status(pick_id, False)
        await query.answer("⏭️ Registrada EN OBSERVACIÓN (sin apostar)")
        new_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭️ En observación · 🔄 Cambiar a Aposté", callback_data=f"bet_placed:{pick_id}")
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
        BotCommand("picks",    "📋 Resumen del día (picks enviados + estado)"),
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
