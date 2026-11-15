"""
harvesters/odds_harvester.py
Recolecta cuotas de múltiples bookmakers via The Odds API (500 req/mes gratis).
Implementa caché local para minimizar consumo de requests.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import hashlib
from datetime import datetime, timedelta, timezone
import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import ODDS_API_KEY, DATA_DIR
from harvesters.db_client import upsert as db_upsert, select as db_select, track_api_usage

# ── Constantes ────────────────────────────────────────────
BASE_URL   = "https://api.the-odds-api.com/v4"
CACHE_FILE = DATA_DIR / "odds_cache.json"
CACHE_TTL_MINUTES = 120  # Refresca cada 2 horas para ahorrar requests

# Deportes y mercados prioritarios optimizados para el free tier (500 req/mes)
SPORTS_CONFIG = {
    # Fútbol Top
    "soccer_epl":                  {"name": "Premier League",      "deporte": "football"},
    "soccer_spain_la_liga":        {"name": "LaLiga",              "deporte": "football"},
    "soccer_uefa_champs_league":   {"name": "Champions League",    "deporte": "football"},
    "soccer_uefa_nations_league":  {"name": "UEFA Nations League", "deporte": "football"},
    "soccer_italy_serie_a":        {"name": "Serie A",             "deporte": "football"},
    "soccer_germany_bundesliga":   {"name": "Bundesliga",          "deporte": "football"},
    # Baloncesto & Americano (alta frecuencia y fines de semana)
    "basketball_nba":              {"name": "NBA",                 "deporte": "basketball"},
    "americanfootball_nfl":        {"name": "NFL",                 "deporte": "americano"},
}

# Monitoreamos h2h para maximizar la cobertura deportiva dentro del free tier
MARKETS = ["h2h"]


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    CACHE_FILE.write_text(json.dumps(data, default=str), encoding="utf-8")


def _cache_key(sport: str, market: str) -> str:
    return hashlib.md5(f"{sport}:{market}".encode()).hexdigest()


def _is_cache_valid(cache: dict, key: str) -> bool:
    if key not in cache:
        return False
    ts_str = cache[key].get("fetched_at")
    if not ts_str:
        return False
    fetched_at = datetime.fromisoformat(ts_str)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at < timedelta(minutes=CACHE_TTL_MINUTES)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_odds(sport: str, market: str) -> list[dict]:
    """Llama a The Odds API para un deporte y mercado específico."""
    params = {
        "apiKey":  ODDS_API_KEY,
        "regions": "eu,us",        # Soporta Pinnacle, 1xBet, Betfair, DraftKings
        "markets": market,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{BASE_URL}/sports/{sport}/odds", params=params)
        resp.raise_for_status()

    # Log del consumo restante
    remaining = resp.headers.get("x-requests-remaining", "?")
    used      = resp.headers.get("x-requests-used", "?")
    logger.info(f"Odds API [{sport}/{market}] — usados: {used}, restantes: {remaining}")
    track_api_usage("odds_api", 1)

    return resp.json()


def harvest_odds(force_refresh: bool = False) -> list[dict]:
    """
    Recolecta odds de todos los deportes/mercados configurados.
    Usa caché para minimizar consumo. Retorna lista de partidos enriquecidos con odds.
    """
    cache = _load_cache()
    all_events: dict[str, dict] = {}  # source_id → evento

    for sport_key, sport_meta in SPORTS_CONFIG.items():
        for market in MARKETS:
            ck = _cache_key(sport_key, market)

            if not force_refresh and _is_cache_valid(cache, ck):
                logger.debug(f"Cache hit: {sport_key}/{market}")
                events = cache[ck]["data"]
            else:
                logger.info(f"Fetching odds: {sport_key}/{market}")
                try:
                    events = _fetch_odds(sport_key, market)
                    cache[ck] = {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "data": events
                    }
                    _save_cache(cache)
                except Exception as e:
                    logger.error(f"Error fetching {sport_key}/{market}: {e}")
                    events = cache.get(ck, {}).get("data", [])

            # Acumula eventos
            for ev in events:
                sid = ev["id"]
                if sid not in all_events:
                    all_events[sid] = {
                        "source_id":        sid,
                        "sport_key":        sport_key,
                        "deporte":          sport_meta["deporte"],
                        "liga":             sport_meta["name"],
                        "equipo_local":     ev["home_team"],
                        "equipo_visitante": ev["away_team"],
                        "kickoff":          ev["commence_time"],
                        "bookmakers":       [],
                    }
                all_events[sid]["bookmakers"].extend(ev.get("bookmakers", []))

    return list(all_events.values())


def get_best_odds(event: dict, market: str, outcome: str) -> tuple[float, str]:
    """
    De un evento, retorna la mejor cuota disponible para un mercado y outcome.
    Retorna (cuota, nombre_bookmaker).
    """
    best_odds = 0.0
    best_book = ""

    for bm in event.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt["key"] != market:
                continue
            for oc in mkt.get("outcomes", []):
                if oc["name"].lower() == outcome.lower():
                    if oc["price"] > best_odds:
                        best_odds = oc["price"]
                        best_book = bm["title"]

    return best_odds, best_book


def save_odds_to_db(events: list[dict]) -> int:
    """Guarda partidos y odds en Supabase. Retorna cantidad de partidos procesados."""
    saved = 0

    for ev in events:
        try:
            # Upsert partido
            result = db_upsert('partidos', [{
                "source_id":        ev["source_id"],
                "deporte":          ev["deporte"],
                "liga":             ev["liga"],
                "equipo_local":     ev["equipo_local"],
                "equipo_visitante": ev["equipo_visitante"],
                "kickoff":          ev["kickoff"],
                "estado":           "pendiente",
            }], on_conflict="source_id")

            if not result:
                # Partido ya existe, buscar su ID
                existing = db_select("partidos", {"source_id": ev["source_id"]}, limit=1)
                if not existing:
                    continue
                partido_id = existing[0]["id"]
            else:
                partido_id = result[0]["id"]

            # Construir lote de odds deduplicado
            odds_rows = []
            seen_keys = set()
            for bm in ev.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    for oc in mkt.get("outcomes", []):
                        price = oc.get("price", 0)
                        price = min(float(price), 999.0) if price else 0.0
                        if price <= 1.0:
                            continue
                        dedup_key = (bm["title"], mkt["key"], oc["name"])
                        if dedup_key in seen_keys:
                            continue
                        seen_keys.add(dedup_key)
                        odds_rows.append({
                            "partido_id":   partido_id,
                            "bookmaker":    bm["title"],
                            "mercado":      mkt["key"],
                            "outcome":      oc["name"],
                            "cuota_actual": round(price, 3),
                        })

            # Upsert odds en lotes de 50
            for i in range(0, len(odds_rows), 50):
                db_upsert("odds", odds_rows[i:i+50],
                          on_conflict="partido_id,bookmaker,mercado,outcome")

            saved += 1
        except Exception as e:
            logger.error(f"Error guardando partido {ev.get('source_id')}: {e}")

    logger.info(f"Odds: {saved}/{len(events)} partidos guardados en BD")
    return saved


if __name__ == "__main__":
    logger.info("=== Test: Odds Harvester ===")
    events = harvest_odds()
    logger.info(f"Eventos encontrados: {len(events)}")
    if events:
        sample = events[0]
        logger.info(f"Muestra: {sample['equipo_local']} vs {sample['equipo_visitante']} — {sample['liga']}")
        logger.info(f"Bookmakers: {len(sample['bookmakers'])}")
