"""
bot/telegram_notifier.py
Módulo de envío de alertas con botones interactivos para registro de apuestas:
- Botón "✅ Aposté a esta" -> Impacta el bankroll al liquidar.
- Botón "⏭️ No aposté" -> Solo monitorea el resultado sin tocar la banca.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import time
import httpx
from loguru import logger
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BANKROLL, DATA_DIR
from bot.formatter import format_pick_message
from harvesters.db_client import insert, select, update

API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
MAP_FILE = DATA_DIR / "picks_telegram_map.json"
PLACED_FILE = DATA_DIR / "picks_placed_map.json"
BANKROLL_FILE = DATA_DIR / "bankroll_state.json"


def get_current_bankroll() -> float:
    """Retorna el bankroll actual persistido."""
    if BANKROLL_FILE.exists():
        try:
            d = json.loads(BANKROLL_FILE.read_text(encoding="utf-8"))
            return float(d.get("bankroll", BANKROLL))
        except Exception:
            pass
    return float(BANKROLL)


def set_bankroll_value(new_amount: float) -> float:
    """Fija un nuevo valor base de bankroll."""
    val = max(1.0, float(new_amount))
    BANKROLL_FILE.write_text(json.dumps({"bankroll": round(val, 2)}, indent=2), encoding="utf-8")
    return val


def update_bankroll(delta: float) -> float:
    """Actualiza y persiste el bankroll tras liquidar una apuesta."""
    current = get_current_bankroll()
    new_val = max(1.0, current + delta)
    BANKROLL_FILE.write_text(json.dumps({"bankroll": round(new_val, 2)}, indent=2), encoding="utf-8")
    return new_val


def _load_map() -> dict:
    if MAP_FILE.exists():
        try:
            return json.loads(MAP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_map(data: dict):
    MAP_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_placed_map() -> dict:
    if PLACED_FILE.exists():
        try:
            return json.loads(PLACED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def set_pick_placed_status(pick_id: str, placed: bool):
    """Guarda si el usuario hizo o no la apuesta."""
    d = _load_placed_map()
    d[pick_id] = placed
    PLACED_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def get_pick_placed_status(pick_id: str) -> bool | None:
    """Retorna True si apostó, False si pasó, None si aún no ha pulsado."""
    d = _load_placed_map()
    return d.get(pick_id)


def send_telegram_raw(text: str, chat_id: str = None, reply_to_message_id: int = None, reply_markup: dict = None) -> int | None:
    """Envía mensaje raw a Telegram con soporte de hilos y botones interactivos."""
    target_chat = chat_id or TELEGRAM_CHAT_ID
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(API_URL, json=payload)
            if resp.status_code == 200 and resp.json().get("ok"):
                return resp.json()["result"]["message_id"]
            else:
                logger.error(f"Error Telegram {resp.status_code}: {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Fallo conexión Telegram: {e}")
        return None


def get_already_sent_keys() -> set:
    try:
        sent_rows = select("picks", limit=2000)
        return {(r["partido_id"], r["mercado"], r["outcome"]) for r in sent_rows if "partido_id" in r}
    except Exception as e:
        logger.warning(f"Error consultando picks previos en BD: {e}")
        return set()


def broadcast_picks(picks: list[dict], max_to_send: int = 5) -> int:
    """
    Envía pronósticos acompañados de botones interactivos para marcar si se apostó.
    """
    if not picks:
        logger.info("No hay picks para enviar.")
        return 0

    already_sent = get_already_sent_keys()
    msg_map = _load_map()
    current_bank = get_current_bankroll()
    sent_count = 0

    for pick in picks:
        if sent_count >= max_to_send:
            break

        key = (pick["partido_id"], pick["mercado"], pick["outcome"])
        if key in already_sent:
            continue

        # Formatear texto con banca actual
        msg = format_pick_message(pick, bankroll=current_bank)

        # Generar registro temporal previo para obtener pick_id
        pick_record = {
            "partido_id":     pick["partido_id"],
            "mercado":        pick["mercado"],
            "outcome":        pick["outcome"],
            "prob_modelo":    pick["prob_modelo"],
            "prob_implicita": pick["prob_implicita"],
            "ev":             pick["ev"],
            "cuota":          pick["cuota"],
            "bookmaker":      pick["bookmaker"],
            "kelly_fraccion": pick["kelly"],
        }
        res = insert("picks", [pick_record])
        if not res:
            continue

        pick_id = res[0]["id"]

        # Botones interactivos vinculados al pick_id
        inline_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aposté a esta", "callback_data": f"bet_placed:{pick_id}"},
                    {"text": "⏭️ No aposté", "callback_data": f"bet_skipped:{pick_id}"}
                ]
            ]
        }

        msg_id = send_telegram_raw(msg, reply_markup=inline_keyboard)

        if msg_id:
            sent_count += 1
            already_sent.add(key)
            msg_map[pick_id] = msg_id
            _save_map(msg_map)

            logger.info(f"Pick emitido con botones (ID {pick_id[:8]} - Msg {msg_id}): {pick['equipo_local']} vs {pick['equipo_visitante']}")
            time.sleep(1.5)

    return sent_count


def notify_pick_result(
    pick_id: str,
    is_win: bool,
    score_text: str,
    net_cash: float,
    was_placed: bool | None,
    current_bankroll: float,
    telegram_msg_id: int = None
) -> bool:
    """
    Responde en hilo. Si fue apostada, informa el impacto en banca; si no, indica que la banca quedó intacta.
    """
    if not telegram_msg_id:
        msg_map = _load_map()
        telegram_msg_id = msg_map.get(pick_id)

    if was_placed is True:
        # Apuesta realizada: impacta banca
        if is_win:
            text = (
                f"✅ *ACERTADA (Apostada)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado: `{score_text}`\n"
                f"Ganancia: `+{net_cash:.2f}$`\n"
                f"💼 *Tu banca actual:* `${current_bankroll:.2f}`"
            )
        else:
            text = (
                f"❌ *NO SE DIO (Apostada)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado: `{score_text}`\n"
                f"Pérdida: `-{abs(net_cash):.2f}$`\n"
                f"💼 *Tu banca actual:* `${current_bankroll:.2f}`"
            )
    else:
        # Apuesta no realizada o en observación: banca intacta
        if is_win:
            text = (
                f"✅ *ACERTADA (No apostada)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado: `{score_text}`\n"
                f"🛡️ *Tu banca no varió:* `${current_bankroll:.2f}`\n"
                f"_El modelo acertó la proyección._"
            )
        else:
            text = (
                f"❌ *NO SE DIO (No apostada)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado: `{score_text}`\n"
                f"🛡️ *Tu banca protegida:* `${current_bankroll:.2f}`"
            )

    ok = send_telegram_raw(text, reply_to_message_id=telegram_msg_id)
    return bool(ok)
