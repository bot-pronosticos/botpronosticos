"""
scripts/check_results.py
Feedback Loop & Liquidación Automática de Resultados:
1. Consulta The Odds API /scores para obtener marcadores de partidos terminados.
2. Actualiza goles_local / goles_visitante y estado='finalizado' en Supabase.
3. Liquida todos los picks pendientes con marcador final conocido.
4. Si el usuario pulsó '✅ Aposté a esta' -> Actualiza el bankroll real en Supabase y notifica en hilo.
5. Si pulsó '⏭️ No aposté' o no pulsó -> Notifica acierto/fallo pero mantiene bankroll intacto.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone
import httpx
from loguru import logger

from harvesters.db_client import select, update
from config.settings import ODDS_API_KEY
from harvesters.odds_harvester import SPORTS_CONFIG
from bot.telegram_notifier import (
    notify_pick_result, send_telegram_raw, get_current_bankroll, update_bankroll,
    get_pick_placed_status, get_pick_telegram_msg
)


def update_scores_from_odds_api() -> int:
    """
    Sincroniza marcadores reales de partidos finalizados desde The Odds API (/scores).
    """
    pending = select("partidos", {"estado": "pendiente"}, limit=500)
    if not pending:
        logger.info("No hay partidos pendientes en BD.")
        return 0

    pending_by_source = {p["source_id"]: p for p in pending if p.get("source_id")}
    logger.info(f"Partidos pendientes en BD con source_id: {len(pending_by_source)}")

    pending_sports = {p.get("liga") for p in pending}
    pending_deportes = {p.get("deporte") for p in pending}

    sports_to_query = []
    for skey, meta in SPORTS_CONFIG.items():
        if meta["name"] in pending_sports or meta["deporte"] in pending_deportes:
            sports_to_query.append(skey)

    if not sports_to_query:
        sports_to_query = list(SPORTS_CONFIG.keys())[:3]

    logger.info(f"Sincronizando scores para {len(sports_to_query)} deportes...")
    updated_count = 0

    for skey in sports_to_query:
        try:
            resp = httpx.get(
                f"https://api.the-odds-api.com/v4/sports/{skey}/scores",
                params={"apiKey": ODDS_API_KEY, "daysFrom": 3},
                timeout=15
            )
            if resp.status_code != 200:
                continue
            events = resp.json()
            for ev in events:
                if not ev.get("completed"):
                    continue
                sid = ev.get("id")
                if sid in pending_by_source:
                    scores = ev.get("scores", [])
                    if not scores:
                        continue

                    sh, sa = None, None
                    for s in scores:
                        if s.get("name") == ev.get("home_team"):
                            sh = int(round(float(s.get("score", 0))))
                        elif s.get("name") == ev.get("away_team"):
                            sa = int(round(float(s.get("score", 0))))

                    if sh is not None and sa is not None:
                        partido = pending_by_source[sid]
                        update("partidos", {
                            "goles_local": sh,
                            "goles_visitante": sa,
                            "estado": "finalizado"
                        }, {"id": partido["id"]})
                        logger.info(f"Score sincronizado: {partido['equipo_local']} {sh} - {sa} {partido['equipo_visitante']}")
                        updated_count += 1
        except Exception as e:
            logger.error(f"Error consultando scores de {skey}: {e}")

    logger.info(f"Partidos actualizados con marcador final: {updated_count}")
    return updated_count


def get_unsettled_picks() -> list[dict]:
    """Picks en BD pendientes de validación."""
    rows = select("picks", limit=500)
    return [p for p in rows if p.get("resultado_correcto") is None]


def check_and_settle():
    logger.info("=== VERIFICACIÓN Y LIQUIDACIÓN DE RESULTADOS ===")

    # 1. Sincronizar marcadores desde The Odds API primero
    try:
        update_scores_from_odds_api()
    except Exception as e:
        logger.error(f"Error actualizando marcadores: {e}")

    # 2. Consultar picks pendientes
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
            final_bank = current_bank

        # Actualizar estado en Supabase
        update("picks", {
            "resultado_correcto": is_win,
            "ganancia_unidades": round(ganancia, 4) if was_placed else 0.0,
            "verificado_en": datetime.now(timezone.utc).isoformat()
        }, {"id": pick_id})

        score_text = f"{local} {gl} - {gv} {visitante}"
        tg_id = get_pick_telegram_msg(pick_id)

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
