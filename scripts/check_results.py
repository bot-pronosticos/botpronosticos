"""
scripts/check_results.py
Fase 4: Feedback Loop & Verificación Automática de Resultados.
- Si el usuario pulsó '✅ Aposté a esta' -> Actualiza el bankroll real y notifica ganancia/pérdida.
- Si el usuario pulsó '⏭️ No aposté' o no pulsó -> Notifica si acertó o falló pero mantiene el bankroll intacto.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone
from loguru import logger

from harvesters.db_client import select, update
from bot.telegram_notifier import (
    notify_pick_result, send_telegram_raw, get_current_bankroll, update_bankroll, get_pick_placed_status
)


def get_unsettled_picks() -> list[dict]:
    """Picks en BD pendientes de validación."""
    rows = select("picks", limit=500)
    return [p for p in rows if p.get("resultado_correcto") is None]


def check_and_settle():
    logger.info("=== VERIFICACIÓN Y LIQUIDACIÓN DE RESULTADOS ===")
    pending_picks = get_unsettled_picks()
    logger.info(f"Picks pendientes de resolver: {len(pending_picks)}")

    if not pending_picks:
        logger.info("No hay picks pendientes por liquidar.")
        return

    settled_count = 0
    wins = 0
    net_units = 0.0

    for pick in pending_picks:
        pick_id    = pick.get("id")
        partido_id = pick.get("partido_id")
        partidos = select("partidos", {"id": partido_id}, limit=1)
        if not partidos:
            continue

        match = partidos[0]
        local = match.get("equipo_local", "Local")
        visitante = match.get("equipo_visitante", "Visitante")
        kickoff_str = match.get("kickoff", "")

        # Si aún no ha comenzado el partido, omitir
        if kickoff_str:
            try:
                dt_k = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < dt_k:
                    continue
            except Exception:
                pass

        # Marcadores registrados
        gl = match.get("goles_local")
        gv = match.get("goles_visitante")

        if gl is None or gv is None:
            continue

        # Determinar resultado real (H / D / A)
        if gl > gv:
            actual_res = "H"
        elif gv > gl:
            actual_res = "A"
        else:
            actual_res = "D"

        predicted = pick.get("outcome")
        cuota     = float(pick.get("cuota", 1.0))
        kelly_f   = float(pick.get("kelly_fraccion", 0.02))

        # Determinar si el usuario realizó la apuesta o no
        was_placed = get_pick_placed_status(pick_id)
        current_bank = get_current_bankroll()
        stake = min(current_bank * kelly_f, current_bank * 0.04)
        stake = max(stake, 1.0)

        is_win = (predicted == actual_res)

        if is_win:
            ganancia = stake * (cuota - 1.0)
            wins += 1
        else:
            ganancia = -stake

        # Solo actualizar bankroll si el usuario realmente apostó
        if was_placed is True:
            final_bank = update_bankroll(ganancia)
            net_units += ganancia
        else:
            final_bank = current_bank  # Banca intacta

        # Actualizar estado en Supabase
        update("picks", {
            "resultado_correcto": is_win,
            "ganancia_unidades": round(ganancia, 4) if was_placed else 0.0,
            "verificado_en": datetime.now(timezone.utc).isoformat()
        }, {"id": pick_id})

        score_text = f"{local} {gl} - {gv} {visitante}"
        tg_id = pick.get("telegram_message_id")

        # Notificar en hilo a Telegram
        notify_pick_result(
            pick_id=pick_id,
            is_win=is_win,
            score_text=score_text,
            net_cash=ganancia,
            was_placed=was_placed,
            current_bankroll=final_bank,
            telegram_msg_id=tg_id
        )

        settled_count += 1
        logger.info(f"Pick resuelto: {local} vs {visitante} | Acertado: {is_win} | Apostada: {was_placed}")

    if settled_count > 0:
        summary_msg = (
            f"📊 *RESUMEN DE JORNADA CULMINADA*\n"
            f"• Partidos liquidados: `{settled_count}`\n"
            f"• Aciertos del modelo: `{wins}/{settled_count}`\n"
            f"• Saldo en banca: `${get_current_bankroll():.2f}`"
        )
        send_telegram_raw(summary_msg)


if __name__ == "__main__":
    check_and_settle()
