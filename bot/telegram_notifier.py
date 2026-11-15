"""
bot/telegram_notifier.py
Persistencia de estado en Supabase (tabla `api_usage` como key-value store de alta disponibilidad):
  - Bankroll: fila (api_name='bot_bankroll', fecha='2099-01-01', requests=cents)
  - Pick meta (telegram_message_id, placed): fila (api_name='pick_{id}', fecha='2099-01-01', requests=msg_id, limite=status)
JSON local en disco como cache de acceso rápido en memoria.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import time
import httpx
from datetime import date
from loguru import logger
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BANKROLL, DATA_DIR
from bot.formatter import format_pick_message
from harvesters.db_client import insert, select, update, upsert

API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
BANKROLL_FILE = DATA_DIR / "bankroll_state.json"
MAP_FILE = DATA_DIR / "picks_telegram_map.json"
PLACED_FILE = DATA_DIR / "picks_placed_map.json"

STATE_DATE = "2099-01-01"


# ── Bankroll ──────────────────────────────────────────────────────────────────

def _bankroll_from_supabase() -> float | None:
    try:
        rows = select("api_usage", {"api_name": "bot_bankroll", "fecha": STATE_DATE}, limit=1)
        if rows:
            cents = int(rows[0].get("requests", 0))
            if cents > 0:
                return round(cents / 100.0, 2)
    except Exception as e:
        logger.warning(f"bankroll Supabase read error: {e}")
    return None


def _bankroll_to_supabase(amount: float):
    try:
        cents = int(round(amount * 100))
        upsert("api_usage", [{
            "api_name": "bot_bankroll",
            "fecha":    STATE_DATE,
            "requests": cents,
            "limite":   1
        }], on_conflict="api_name,fecha")
    except Exception as e:
        logger.warning(f"bankroll Supabase write error: {e}")


def get_current_bankroll() -> float:
    """Supabase primero; fallback a cache local."""
    val = _bankroll_from_supabase()
    if val is not None:
        try:
            BANKROLL_FILE.write_text(json.dumps({"bankroll": round(val, 2)}), encoding="utf-8")
        except Exception:
            pass
        return val
    if BANKROLL_FILE.exists():
        try:
            d = json.loads(BANKROLL_FILE.read_text(encoding="utf-8"))
            return float(d.get("bankroll", BANKROLL))
        except Exception:
            pass
    return float(BANKROLL)


def set_bankroll_value(new_amount: float) -> float:
    val = max(1.0, float(new_amount))
    _bankroll_to_supabase(val)
    try:
        BANKROLL_FILE.write_text(json.dumps({"bankroll": round(val, 2)}, indent=2), encoding="utf-8")
    except Exception:
        pass
    return val


def update_bankroll(delta: float) -> float:
    current = get_current_bankroll()
    new_val = max(1.0, current + delta)
    _bankroll_to_supabase(new_val)
    try:
        BANKROLL_FILE.write_text(json.dumps({"bankroll": round(new_val, 2)}, indent=2), encoding="utf-8")
    except Exception:
        pass
    return new_val


# ── Metadata de Picks (telegram_msg_id y placed status) ───────────────────────

def save_pick_meta(pick_id: str, msg_id: int = None, placed: bool = None):
    """
    Guarda telegram_message_id y/o placed_by_user en Supabase api_usage:
      requests = msg_id (0 si no definido)
      limite   = 1 si apostó, 0 si pasó, -1 si pendiente
    """
    try:
        rows = select("api_usage", {"api_name": f"pick_{pick_id}", "fecha": STATE_DATE}, limit=1)
        cur_msg = msg_id if msg_id is not None else (rows[0]["requests"] if rows else 0)
        
        if placed is True:
            cur_lim = 1
        elif placed is False:
            cur_lim = 0
        else:
            cur_lim = rows[0]["limite"] if rows else -1

        upsert("api_usage", [{
            "api_name": f"pick_{pick_id}",
            "fecha":    STATE_DATE,
            "requests": cur_msg,
            "limite":   cur_lim
        }], on_conflict="api_name,fecha")
    except Exception as e:
        logger.warning(f"save_pick_meta Supabase error: {e}")

    # Cache local en disco
    if msg_id is not None:
        try:
            m = json.loads(MAP_FILE.read_text(encoding="utf-8")) if MAP_FILE.exists() else {}
            m[str(pick_id)] = msg_id
            MAP_FILE.write_text(json.dumps(m, indent=2), encoding="utf-8")
        except Exception:
            pass

    if placed is not None:
        try:
            p = json.loads(PLACED_FILE.read_text(encoding="utf-8")) if PLACED_FILE.exists() else {}
            p[str(pick_id)] = placed
            PLACED_FILE.write_text(json.dumps(p, indent=2), encoding="utf-8")
        except Exception:
            pass


def get_pick_placed_status(pick_id: str) -> bool | None:
    """Retorna True si apostó, False si omitió, None si no ha decidido."""
    try:
        rows = select("api_usage", {"api_name": f"pick_{pick_id}", "fecha": STATE_DATE}, limit=1)
        if rows:
            lim = rows[0].get("limite")
            if lim == 1:
                return True
            elif lim == 0:
                return False
    except Exception as e:
        logger.warning(f"get_pick_placed_status error: {e}")

    # Fallback local
    if PLACED_FILE.exists():
        try:
            p = json.loads(PLACED_FILE.read_text(encoding="utf-8"))
            if str(pick_id) in p:
                return p[str(pick_id)]
        except Exception:
            pass
    return None


def get_pick_telegram_msg(pick_id: str) -> int | None:
    """Retorna el telegram message_id guardado para este pick."""
    try:
        rows = select("api_usage", {"api_name": f"pick_{pick_id}", "fecha": STATE_DATE}, limit=1)
        if rows:
            mid = rows[0].get("requests", 0)
            if mid > 0:
                return mid
    except Exception as e:
        logger.warning(f"get_pick_telegram_msg error: {e}")

    if MAP_FILE.exists():
        try:
            m = json.loads(MAP_FILE.read_text(encoding="utf-8"))
            return m.get(str(pick_id))
        except Exception:
            pass
    return None


def set_pick_placed_status(pick_id: str, placed: bool):
    save_pick_meta(pick_id=pick_id, placed=placed)


# ── Telegram Raw Sender ───────────────────────────────────────────────────────

def send_telegram_raw(text: str, chat_id: str = None, reply_to_message_id: int = None, reply_markup: dict = None) -> int | None:
    """Envía mensaje raw a Telegram. Retorna message_id o None."""
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
                logger.error(f"Telegram error {resp.status_code}: {resp.text}")
                return None
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return None


def get_already_sent_keys() -> set:
    """Retorna set de (partido_id, mercado, outcome) ya emitidos según Supabase."""
    try:
        sent_rows = select("picks", limit=2000)
        return {(r["partido_id"], r["mercado"], r["outcome"]) for r in sent_rows if "partido_id" in r}
    except Exception as e:
        logger.warning(f"Error consultando picks previos: {e}")
        return set()


def broadcast_picks(picks: list[dict], max_to_send: int = 5) -> int:
    """
    Envía pronósticos con botones interactivos y persiste metadata.
    Deduplica contra Supabase para nunca repetir.
    """
    if not picks:
        return 0

    already_sent = get_already_sent_keys()
    current_bank = get_current_bankroll()
    sent_count = 0

    for pick in picks:
        if sent_count >= max_to_send:
            break

        key = (pick["partido_id"], pick["mercado"], pick["outcome"])
        if key in already_sent:
            continue

        msg = format_pick_message(pick, bankroll=current_bank)

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

        inline_keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Aposté a esta", "callback_data": f"bet_placed:{pick_id}"},
                {"text": "⏭️ No aposté",    "callback_data": f"bet_skipped:{pick_id}"}
            ]]
        }

        msg_id = send_telegram_raw(msg, reply_markup=inline_keyboard)

        if msg_id:
            sent_count += 1
            already_sent.add(key)
            save_pick_meta(pick_id=pick_id, msg_id=msg_id)
            logger.info(f"Pick emitido (ID {str(pick_id)[:8]} / Msg {msg_id}): "
                        f"{pick['equipo_local']} vs {pick['equipo_visitante']}")
            time.sleep(1.2)

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
    """Responde en hilo con el resultado y el impacto exacto en banca."""
    if not telegram_msg_id:
        telegram_msg_id = get_pick_telegram_msg(pick_id)

    if was_placed is True:
        if is_win:
            text = (
                f"✅ *ACERTADA (Apostada)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado final: `{score_text}`\n"
                f"Ganancia neta: `+{net_cash:.2f}$`\n"
                f"💼 *Tu saldo de banca:* `${current_bankroll:.2f}`"
            )
        else:
            text = (
                f"❌ *NO SE DIO (Apostada)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado final: `{score_text}`\n"
                f"Pérdida registrada: `-{abs(net_cash):.2f}$`\n"
                f"💼 *Tu saldo de banca:* `${current_bankroll:.2f}`"
            )
    else:
        if is_win:
            text = (
                f"✅ *ACERTADA (En observación)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado final: `{score_text}`\n"
                f"🛡️ *Tu banca se mantuvo intacta:* `${current_bankroll:.2f}`\n"
                f"_El modelo confirmó la proyección._"
            )
        else:
            text = (
                f"❌ *NO SE DIO (En observación)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Resultado final: `{score_text}`\n"
                f"🛡️ *Banca protegida:* `${current_bankroll:.2f}`"
            )

    ok = send_telegram_raw(text, reply_to_message_id=telegram_msg_id)
    return bool(ok)
