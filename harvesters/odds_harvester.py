"""
harvesters/odds_harvester.py
Recolector multideporte autónomo y de alta disponibilidad:
- Fuente primaria: Feed público de cuotas en vivo en tiempo real (Decimales, 1X2, Moneyline).
- Cobertura: Fútbol (Premier League, LaLiga, Serie A, Bundesliga), Baloncesto (NBA), NFL y Tenis (ATP).
- Cero dependencia de cuotas limitadas a 500 requests/mes.
- Persistencia en Supabase con deduplicación y caché local en disco.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import hashlib
from datetime import datetime, timezone, timedelta
import httpx
from loguru import logger

from config.settings import DATA_DIR, ODDS_API_KEY
from harvesters.db_client import upsert as db_upsert, select as db_select

CACHE_FILE = DATA_DIR / "odds_cache.json"
CACHE_TTL_MINUTES = 30  # Cuotas frescas cada 30 minutos

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

SPORTS_FEEDS = [
    {"deporte": "football",   "liga": "Premier League", "url": "https://www.bovada.lv/services/sports/event/v2/events/A/description/soccer/europe/england"},
    {"deporte": "football",   "liga": "LaLiga",         "url": "https://www.bovada.lv/services/sports/event/v2/events/A/description/soccer/europe/spain"},
    {"deporte": "football",   "liga": "Serie A",        "url": "https://www.bovada.lv/services/sports/event/v2/events/A/description/soccer/europe/italy"},
    {"deporte": "football",   "liga": "Bundesliga",     "url": "https://www.bovada.lv/services/sports/event/v2/events/A/description/soccer/europe/germany"},
    {"deporte": "basketball", "liga": "NBA",            "url": "https://www.bovada.lv/services/sports/event/v2/events/A/description/basketball/nba"},
    {"deporte": "americano",  "liga": "NFL",            "url": "https://www.bovada.lv/services/sports/event/v2/events/A/description/football/nfl"},
    {"deporte": "tennis",     "liga": "ATP Tennis",     "url": "https://www.bovada.lv/services/sports/event/v2/events/A/description/tennis/atp"},
]


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Error guardando cache de odds: {e}")


def _is_cache_valid(cache: dict) -> bool:
    ts_str = cache.get("fetched_at")
    if not ts_str:
        return False
    try:
        fetched_at = datetime.fromisoformat(ts_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched_at < timedelta(minutes=CACHE_TTL_MINUTES)
    except Exception:
        return False


def fetch_live_feed() -> list[dict]:
    """Descarga cuotas frescas de todas las ligas desde el feed público."""
    all_events = []
    now = datetime.now(timezone.utc)

    with httpx.Client(headers=HEADERS, timeout=12) as client:
        for s in SPORTS_FEEDS:
            try:
                r = client.get(s["url"])
                if r.status_code != 200:
                    continue
                data = r.json()
                for cat in data:
                    cat_desc = cat.get("description", "")
                    for ev in cat.get("events", []):
                        desc = ev.get("description", "")
                        if " vs " not in desc and " @ " not in desc:
                            continue

                        st_ms = ev.get("startTime", 0)
                        if not st_ms:
                            continue
                        dt = datetime.fromtimestamp(st_ms / 1000, tz=timezone.utc)

                        if " vs " in desc:
                            teams = desc.split(" vs ", 1)
                            h_team, a_team = teams[0].strip(), teams[1].strip()
                        else:
                            teams = desc.split(" @ ", 1)
                            a_team, h_team = teams[0].strip(), teams[1].strip()

                        # Cuotas de Ganador (Moneyline / 1X2)
                        odds_h, odds_d, odds_a = 0.0, 0.0, 0.0
                        for dg in ev.get("displayGroups", []):
                            for m in dg.get("markets", []):
                                mkey = m.get("key", "")
                                if mkey in ["3W-1X2", "2W-12", "2W-Moneyline", "3W-Match-Result"]:
                                    for o in m.get("outcomes", []):
                                        p_dec = float(o.get("price", {}).get("decimal") or 0.0)
                                        otype = o.get("type", "").upper()
                                        odesc = o.get("description", "")
                                        if otype == "H" or odesc == h_team or h_team in odesc:
                                            odds_h = max(odds_h, p_dec)
                                        elif otype == "A" or odesc == a_team or a_team in odesc:
                                            odds_a = max(odds_a, p_dec)
                                        elif otype == "D" or "draw" in odesc.lower() or "tie" in odesc.lower():
                                            odds_d = max(odds_d, p_dec)

                        if odds_h > 1.0 and odds_a > 1.0:
                            hrs = round((dt - now).total_seconds() / 3600, 1)
                            all_events.append({
                                "source_id":        str(ev.get("id")),
                                "deporte":          s["deporte"],
                                "liga":             s["liga"],
                                "sub_liga":         cat_desc,
                                "equipo_local":     h_team,
                                "equipo_visitante": a_team,
                                "kickoff":          dt.isoformat(),
                                "hours_to_kickoff": hrs,
                                "odds_h":           round(odds_h, 3),
                                "odds_d":           round(odds_d, 3),
                                "odds_a":           round(odds_a, 3),
                                "bookmaker":        "Bovada / Market Consensus",
                            })
            except Exception as e:
                logger.warning(f"Error consultando {s['liga']}: {e}")

    logger.info(f"Feed en vivo: {len(all_events)} eventos con cuotas capturados.")
    return all_events


def sync_partidos_db(events: list[dict]) -> dict[str, str]:
    """
    Sincroniza eventos en Supabase (partidos) en lotes de alta velocidad.
    Retorna un diccionario source_id -> UUID partido_id, y actualiza ev['partido_id'].
    """
    if not events:
        return {}

    source_to_uuid = {}
    partido_rows = []

    for ev in events:
        # Asegurar campos requeridos
        ko = ev.get("kickoff")
        if not ko:
            continue
        partido_rows.append({
            "source_id":        ev["source_id"],
            "deporte":          ev["deporte"],
            "liga":             ev["liga"],
            "equipo_local":     ev["equipo_local"],
            "equipo_visitante": ev["equipo_visitante"],
            "kickoff":          ko,
            "estado":           "pendiente",
        })

    # Upsert en lotes de 50
    chunk_size = 50
    for i in range(0, len(partido_rows), chunk_size):
        chunk = partido_rows[i:i + chunk_size]
        try:
            res = db_upsert("partidos", chunk, on_conflict="source_id")
            if res:
                for r in res:
                    source_to_uuid[r["source_id"]] = r["id"]
        except Exception as e:
            logger.debug(f"Error en batch upsert partidos: {e}")

    # Fallback para los que ya existían y no retornaron id en el upsert
    missing_sources = [ev["source_id"] for ev in events if ev["source_id"] not in source_to_uuid]
    if missing_sources:
        try:
            existing = db_select("partidos", limit=1000)
            for r in existing:
                if r.get("source_id"):
                    source_to_uuid[r["source_id"]] = r["id"]
        except Exception as e:
            logger.debug(f"Error consultando existentes partidos: {e}")

    # Asignar UUID a cada evento en memoria
    for ev in events:
        sid = ev.get("source_id")
        if sid in source_to_uuid:
            ev["partido_id"] = source_to_uuid[sid]

    return source_to_uuid


def save_odds_to_db(events: list[dict]) -> int:
    """Guarda partidos y cuotas en Supabase en lotes optimizados."""
    if not events:
        return 0

    # Filtrar solo partidos de los próximos 7 días
    relevant = [e for e in events if 0 <= e.get("hours_to_kickoff", 999) <= 168]
    if not relevant:
        return 0

    source_to_uuid = sync_partidos_db(relevant)

    odds_rows = []
    for ev in relevant:
        partido_id = ev.get("partido_id") or source_to_uuid.get(ev.get("source_id"))
        if not partido_id:
            continue

        bm = ev.get("bookmaker", "Consensus")
        if ev.get("odds_h", 0) > 1.0:
            odds_rows.append({"partido_id": partido_id, "bookmaker": bm, "mercado": "h2h", "outcome": ev["equipo_local"], "cuota_actual": ev["odds_h"]})
        if ev.get("odds_a", 0) > 1.0:
            odds_rows.append({"partido_id": partido_id, "bookmaker": bm, "mercado": "h2h", "outcome": ev["equipo_visitante"], "cuota_actual": ev["odds_a"]})
        if ev.get("odds_d", 0) > 1.0:
            odds_rows.append({"partido_id": partido_id, "bookmaker": bm, "mercado": "h2h", "outcome": "Draw", "cuota_actual": ev["odds_d"]})

    # Batch upsert odds en lotes de 100
    chunk_size = 100
    saved_odds = 0
    for i in range(0, len(odds_rows), chunk_size):
        chunk = odds_rows[i:i + chunk_size]
        try:
            res = db_upsert("odds", chunk, on_conflict="partido_id,bookmaker,mercado,outcome")
            saved_odds += len(chunk)
        except Exception as e:
            logger.debug(f"Error en batch upsert odds: {e}")

    logger.info(f"Guardados en Supabase: {len(source_to_uuid)} partidos sincronizados y {saved_odds} cuotas.")
    return len(source_to_uuid)


def harvest_odds(force_refresh: bool = False) -> list[dict]:
    """
    Obtiene cuotas frescas (con caché de 30m) y sincroniza UUIDs en Supabase.
    Retorna la lista de eventos con cuotas en vivo y UUIDs listos.
    """
    cache = _load_cache()
    if not force_refresh and _is_cache_valid(cache) and "events" in cache:
        logger.info(f"Usando caché de cuotas en vivo ({len(cache['events'])} eventos).")
        events = cache["events"]
    else:
        events = fetch_live_feed()
        if events:
            _save_cache({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "events": events
            })
        elif "events" in cache:
            logger.warning("Fallo el feed en vivo, usando eventos de caché previo.")
            events = cache["events"]

    # Sincronizar UUIDs para partidos de las próximas 48h (rápido y suficiente)
    near_events = [e for e in events if 0 <= e.get("hours_to_kickoff", 999) <= 48 and not e.get("partido_id")]
    if near_events:
        sync_partidos_db(near_events)

    return events


if __name__ == "__main__":
    evs = harvest_odds(force_refresh=True)
    print(f"Total eventos: {len(evs)}")
    if evs:
        print("Muestra:", evs[0])
