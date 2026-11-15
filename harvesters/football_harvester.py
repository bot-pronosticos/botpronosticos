"""
harvesters/football_harvester.py
Recolecta stats de fútbol via API-Football (api-sports.io) — 100 req/día gratis.
Gestiona el rate limit agresivamente con caché local.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
from datetime import datetime, timedelta, date
import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import RAPIDAPI_KEY, DATA_DIR, FOOTBALL_LEAGUES
from harvesters.db_client import get_db, track_api_usage, get_api_usage

BASE_URL   = "https://v3.football.api-sports.io"
CACHE_FILE = DATA_DIR / "football_cache.json"
CACHE_TTL_HOURS = 23  # Stats cambian principalmente 1x/día

HEADERS = {
    "x-apisports-key": RAPIDAPI_KEY,
}


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    CACHE_FILE.write_text(json.dumps(data, default=str), encoding="utf-8")


def _can_make_request(needed: int = 1) -> bool:
    """Verifica si quedan requests disponibles hoy."""
    used = get_api_usage("api_football")
    limit = 100
    if used + needed > limit:
        logger.warning(f"API-Football rate limit alcanzado: {used}/{limit} hoy")
        return False
    return True


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=3, max=15))
def _api_get(endpoint: str, params: dict) -> dict:
    """Hace una request a API-Football con headers correctos."""
    if not _can_make_request():
        raise RuntimeError("Rate limit de API-Football alcanzado por hoy")

    with httpx.Client(timeout=20) as client:
        resp = client.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params)
        resp.raise_for_status()

    track_api_usage("api_football", 1)
    data = resp.json()

    if data.get("errors"):
        raise ValueError(f"API-Football error: {data['errors']}")

    return data


def get_fixtures_today(league_id: int, season: int = 2024) -> list[dict]:
    """Obtiene partidos de hoy y mañana para una liga."""
    cache = _load_cache()
    cache_key = f"fixtures_{league_id}_{date.today().isoformat()}"

    if cache_key in cache:
        logger.debug(f"Cache hit: fixtures liga {league_id}")
        return cache[cache_key]

    today    = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    all_fixtures = []
    for d in [today, tomorrow]:
        try:
            data = _api_get("fixtures", {
                "league":  league_id,
                "season":  season,
                "date":    d,
                "status":  "NS",  # Not Started
            })
            all_fixtures.extend(data.get("response", []))
        except Exception as e:
            logger.error(f"Error obteniendo fixtures liga {league_id} para {d}: {e}")

    cache[cache_key] = all_fixtures
    _save_cache(cache)
    return all_fixtures


def get_team_stats(team_id: int, league_id: int, season: int = 2024) -> dict | None:
    """Estadísticas de un equipo en una liga/temporada."""
    cache = _load_cache()
    cache_key = f"team_stats_{team_id}_{league_id}_{season}"

    # Stats de temporada: cachear 23h
    if cache_key in cache:
        cached_at = datetime.fromisoformat(cache[cache_key].get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at < timedelta(hours=CACHE_TTL_HOURS):
            return cache[cache_key].get("data")

    try:
        data = _api_get("teams/statistics", {
            "team":   team_id,
            "league": league_id,
            "season": season,
        })
        stats = data.get("response")
        if stats:
            cache[cache_key] = {
                "_cached_at": datetime.now().isoformat(),
                "data": stats
            }
            _save_cache(cache)
        return stats
    except Exception as e:
        logger.error(f"Error stats equipo {team_id}: {e}")
        return None


def get_head_to_head(team1_id: int, team2_id: int, last: int = 5) -> list[dict]:
    """Histórico de enfrentamientos directos."""
    cache = _load_cache()
    cache_key = f"h2h_{min(team1_id,team2_id)}_{max(team1_id,team2_id)}"

    if cache_key in cache:
        cached_at = datetime.fromisoformat(cache[cache_key].get("_cached_at", "2000-01-01"))
        if datetime.now() - cached_at < timedelta(hours=CACHE_TTL_HOURS):
            return cache[cache_key].get("data", [])

    try:
        data = _api_get("fixtures/headtohead", {
            "h2h":  f"{team1_id}-{team2_id}",
            "last": last,
        })
        h2h = data.get("response", [])
        cache[cache_key] = {
            "_cached_at": datetime.now().isoformat(),
            "data": h2h
        }
        _save_cache(cache)
        return h2h
    except Exception as e:
        logger.error(f"Error H2H {team1_id} vs {team2_id}: {e}")
        return []


def get_injuries(team_id: int, fixture_id: int) -> list[dict]:
    """Lesionados para un partido específico."""
    cache = _load_cache()
    cache_key = f"injuries_{fixture_id}_{team_id}"

    if cache_key in cache:
        return cache[cache_key]

    try:
        data = _api_get("injuries", {
            "fixture": fixture_id,
            "team":    team_id,
        })
        injuries = data.get("response", [])
        cache[cache_key] = injuries
        _save_cache(cache)
        return injuries
    except Exception as e:
        logger.error(f"Error lesiones equipo {team_id} fixture {fixture_id}: {e}")
        return []


def get_standings(league_id: int, season: int = 2024) -> list[dict]:
    """Tabla de posiciones de una liga."""
    cache = _load_cache()
    cache_key = f"standings_{league_id}_{season}_{date.today().isoformat()}"

    if cache_key in cache:
        return cache[cache_key]

    try:
        data = _api_get("standings", {
            "league": league_id,
            "season": season,
        })
        standings = data.get("response", [])
        cache[cache_key] = standings
        _save_cache(cache)
        return standings
    except Exception as e:
        logger.error(f"Error standings liga {league_id}: {e}")
        return []


def extract_team_features(team_stats: dict | None) -> dict:
    """
    Extrae features clave de las stats de un equipo.
    Retorna dict con valores normalizados listos para el modelo.
    """
    if not team_stats:
        return _empty_team_features()

    fixtures   = team_stats.get("fixtures", {})
    goals      = team_stats.get("goals", {})
    form_str   = team_stats.get("form", "") or ""

    played_home = fixtures.get("played", {}).get("home", 0) or 0
    played_away = fixtures.get("played", {}).get("away", 0) or 0
    played_total= fixtures.get("played", {}).get("total", 1) or 1

    wins_total  = fixtures.get("wins", {}).get("total", 0) or 0
    draws_total = fixtures.get("draws", {}).get("total", 0) or 0
    loses_total = fixtures.get("loses", {}).get("total", 0) or 0

    gf_home  = goals.get("for", {}).get("average", {}).get("home", 0) or 0
    gf_away  = goals.get("for", {}).get("average", {}).get("away", 0) or 0
    ga_home  = goals.get("against", {}).get("average", {}).get("home", 0) or 0
    ga_away  = goals.get("against", {}).get("average", {}).get("away", 0) or 0

    # Forma reciente: W=1, D=0.5, L=0 de los últimos 5
    recent_form = form_str[-5:] if len(form_str) >= 5 else form_str
    form_score  = sum(1 if c == "W" else 0.5 if c == "D" else 0 for c in recent_form)
    form_pct    = form_score / max(len(recent_form), 1)

    return {
        "win_rate":     wins_total / played_total,
        "draw_rate":    draws_total / played_total,
        "loss_rate":    loses_total / played_total,
        "gf_home_avg":  float(gf_home),
        "gf_away_avg":  float(gf_away),
        "ga_home_avg":  float(ga_home),
        "ga_away_avg":  float(ga_away),
        "form_pct":     form_pct,
        "form_raw":     recent_form,
        "played_total": played_total,
    }


def _empty_team_features() -> dict:
    return {
        "win_rate": 0.33, "draw_rate": 0.33, "loss_rate": 0.33,
        "gf_home_avg": 1.2, "gf_away_avg": 1.0,
        "ga_home_avg": 1.2, "ga_away_avg": 1.4,
        "form_pct": 0.5, "form_raw": "", "played_total": 0,
    }


if __name__ == "__main__":
    logger.info("=== Test: Football Harvester ===")
    # Test con Premier League (ID 39)
    fixtures = get_fixtures_today(39)
    logger.info(f"Fixtures Premier League hoy/mañana: {len(fixtures)}")
    if fixtures:
        f = fixtures[0]
        logger.info(f"  {f['teams']['home']['name']} vs {f['teams']['away']['name']}")
