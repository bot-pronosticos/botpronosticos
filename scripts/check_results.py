"""
scripts/check_results.py
Feedback Loop & Liquidación Automática de Resultados Multideporte:
1. Consulta marcadores oficiales de partidos finalizados desde ESPN Scoreboard API (100% público, gratuito, sin cuota).
2. Soporte para Fútbol (EPL, LaLiga, Serie A, Bundesliga, Champions), Baloncesto (NBA), NFL y Tenis.
3. Actualiza goles_local / goles_visitante y estado='finalizado' en Supabase (`partidos`).
4. Liquida todos los picks pendientes con marcador final conocido.
5. Si el usuario pulsó '✅ Aposté a esta' -> Actualiza el bankroll real en Supabase y notifica en hilo.
6. Si pulsó '⏭️ No aposté' o no pulsó -> Notifica acierto/fallo pero mantiene bankroll intacto.
7. Emite resumen consolidado de jornada si se liquidaron picks.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone, timedelta
import re
import difflib
import httpx
from loguru import logger

from harvesters.db_client import select, update
from bot.telegram_notifier import (
    notify_pick_result, send_telegram_raw, get_current_bankroll, update_bankroll,
    get_pick_placed_status, get_pick_telegram_msg
)

# Endpoints públicos de ESPN por deporte / liga
ESPN_SCOREBOARDS = {
    "eng.1":          "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
    "esp.1":          "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard",
    "ita.1":          "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard",
    "ger.1":          "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard",
    "fra.1":          "https://site.api.espn.com/apis/site/v2/sports/soccer/fra.1/scoreboard",
    "uefa.champions": "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard",
    "nba":            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "nfl":            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "atp":            "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
}

COMMON_REPLACEMENTS = {
    "man utd": "manchester united",
    "man city": "manchester city",
    "la lakers": "los angeles lakers",
    "la clippers": "los angeles clippers",
    "ny knicks": "new york knicks",
    "gs warriors": "golden state warriors",
}


def clean_tokens(s: str) -> set[str]:
    """Extrae palabras clave para cotejo insensible a formato."""
    s = s.lower()
    for k, v in COMMON_REPLACEMENTS.items():
        s = s.replace(k, v)
    for drop in [" fc", " cf", " afc", " sc", " club", " cd", " ud", " bc"]:
        s = s.replace(drop, "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {t for t in s.split() if len(t) > 2}


def match_team_names(t1: str, t2: str) -> bool:
    """Verifica si dos nombres de equipo corresponden a la misma franquicia."""
    tok1 = clean_tokens(t1)
    tok2 = clean_tokens(t2)
    if not tok1 or not tok2:
        return False
    # Si comparten el nombre característico (>= 4 letras, no genérico)
    overlap = {t for t in tok1.intersection(tok2) if len(t) >= 4 and t not in {"united", "city", "real", "athletic", "club"}}
    if overlap:
        return True
    if tok1 == tok2:
        return True
    return difflib.SequenceMatcher(None, " ".join(sorted(tok1)), " ".join(sorted(tok2))).ratio() >= 0.7


def fetch_espn_scores(sport_key: str, days_back: int = 2) -> list[dict]:
    """Obtiene eventos finalizados de ESPN para una liga en los últimos días."""
    url = ESPN_SCOREBOARDS.get(sport_key)
    if not url:
        return []

    completed_events = []
    now = datetime.now(timezone.utc)

    # Consulta hoy y los días previos
    dates_to_check = [(now - timedelta(days=d)).strftime("%Y%m%d") for d in range(days_back + 1)]

    with httpx.Client(timeout=10) as client:
        for dt_str in dates_to_check:
            try:
                resp = client.get(f"{url}?dates={dt_str}")
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for ev in data.get("events", []):
                    status = ev.get("status", {})
                    # Verificar si el partido concluyó
                    is_completed = status.get("type", {}).get("completed", False)
                    if not is_completed:
                        continue

                    competitions = ev.get("competitions", [])
                    if not competitions:
                        continue
                    comps = competitions[0].get("competitors", [])
                    if len(comps) < 2:
                        continue

                    home_team, away_team = None, None
                    home_score, away_score = None, None

                    for c in comps:
                        ha = c.get("homeAway")
                        team_name = c.get("team", {}).get("displayName", "")
                        raw_score = c.get("score")
                        try:
                            score_val = int(raw_score) if raw_score is not None else None
                        except (ValueError, TypeError):
                            score_val = None

                        if ha == "home":
                            home_team = team_name
                            home_score = score_val
                        elif ha == "away":
                            away_team = team_name
                            away_score = score_val

                    if home_team and away_team and home_score is not None and away_score is not None:
                        completed_events.append({
                            "home_team": home_team,
                            "away_team": away_team,
                            "home_score": home_score,
                            "away_score": away_score,
                            "event_name": ev.get("name", ""),
                            "date": dt_str
                        })
            except Exception as e:
                logger.debug(f"Error consultando ESPN {sport_key} ({dt_str}): {e}")

    return completed_events


def update_scores_from_espn() -> int:
    """
    Sincroniza marcadores reales de partidos finalizados desde ESPN Scoreboard API.
    Sin autenticación, sin límite de cuota.
    """
    unsettled = get_unsettled_picks()
    if not unsettled:
        logger.info("No hay picks pendientes por liquidar.")
        return 0

    pids = [p["partido_id"] for p in unsettled if p.get("partido_id")]
    partidos_pendientes = []
    for pid in pids:
        r = select("partidos", {"id": pid}, limit=1)
        if r and r[0].get("estado") != "finalizado":
            partidos_pendientes.append(r[0])

    if not partidos_pendientes:
        logger.info("Los partidos de los picks pendientes ya están finalizados o no existen.")
        return 0

    logger.info(f"Sincronizando marcadores para {len(partidos_pendientes)} partidos pendientes...")
    updated_count = 0

    # Iterar por todos los scoreboards de ESPN
    for skey in ESPN_SCOREBOARDS.keys():
        try:
            espn_events = fetch_espn_scores(skey, days_back=2)
            if not espn_events:
                continue

            for partido in list(partidos_pendientes):
                p_local = partido.get("equipo_local", "")
                p_vis   = partido.get("equipo_visitante", "")

                for ev in espn_events:
                    # Match home and away
                    if match_team_names(p_local, ev["home_team"]) and match_team_names(p_vis, ev["away_team"]):
                        update("partidos", {
                            "goles_local": ev["home_score"],
                            "goles_visitante": ev["away_score"],
                            "estado": "finalizado"
                        }, {"id": partido["id"]})
                        logger.info(f"✅ Marcador actualizado: {p_local} {ev['home_score']} - {ev['away_score']} {p_vis}")
                        updated_count += 1
                        partidos_pendientes.remove(partido)
                        break
        except Exception as e:
            logger.error(f"Error en sincronización ESPN {skey}: {e}")

    return updated_count


def get_unsettled_picks() -> list[dict]:
    """Picks en BD pendientes de validación."""
    rows = select("picks", limit=500)
    return [p for p in rows if p.get("resultado_correcto") is None]


def check_and_settle():
    logger.info("=== VERIFICACIÓN Y LIQUIDACIÓN DE RESULTADOS ===")

    # 1. Sincronizar marcadores desde ESPN API
    try:
        update_scores_from_espn()
    except Exception as e:
        logger.error(f"Error actualizando marcadores desde ESPN: {e}")

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

        mercado = pick.get("mercado", "h2h")
        if mercado == "ou25":
            tot_goals = gl + gv
            if predicted == "OVER":
                is_win = (tot_goals > 2.5)
            elif predicted == "UNDER":
                is_win = (tot_goals < 2.5)
            else:
                is_win = False
        else:
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
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• Partidos liquidados: `{settled_count}`\n"
            f"• Aciertos del modelo: `{wins}/{settled_count}`\n"
            f"• Saldo en banca: `${get_current_bankroll():,.2f}`"
        )
        send_telegram_raw(summary_msg)


if __name__ == "__main__":
    check_and_settle()
