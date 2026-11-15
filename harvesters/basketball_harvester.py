"""
harvesters/basketball_harvester.py
Stats de NBA via ESPN API pública — sin key, sin límite.
Endpoints no-auth de ESPN son los más confiables sin registro.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
from datetime import datetime, timedelta, date
import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import DATA_DIR

# ESPN API pública — sin autenticación requerida
BASE_URL   = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CACHE_FILE = DATA_DIR / "nba_cache.json"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    CACHE_FILE.write_text(json.dumps(data, default=str), encoding="utf-8")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _get(endpoint: str, params: dict = {}) -> dict:
    url = f"{BASE_URL}/{endpoint}"
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
    return resp.json()


def get_games_today() -> list[dict]:
    """Partidos NBA de hoy y mañana via ESPN."""
    cache = _load_cache()
    cache_key = f"nba_espn_games_{date.today().isoformat()}"

    if cache_key in cache:
        logger.debug("Cache hit: NBA games ESPN")
        return cache[cache_key]

    games = []
    for d in [date.today(), date.today() + timedelta(days=1)]:
        date_str = d.strftime("%Y%m%d")
        try:
            data = _get("scoreboard", {"dates": date_str})
            events = data.get("events", [])
            for ev in events:
                comps = ev.get("competitions", [{}])[0]
                competitors = comps.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), {})

                games.append({
                    "id":             ev.get("id"),
                    "date":           ev.get("date"),
                    "status":         ev.get("status", {}).get("type", {}).get("name", ""),
                    "home_team":      home.get("team", {}).get("displayName", ""),
                    "home_team_id":   home.get("team", {}).get("id", ""),
                    "away_team":      away.get("team", {}).get("displayName", ""),
                    "away_team_id":   away.get("team", {}).get("id", ""),
                    "home_score":     home.get("score", 0),
                    "away_score":     away.get("score", 0),
                    "venue":          comps.get("venue", {}).get("fullName", ""),
                })
            logger.info(f"NBA ESPN [{d}]: {len(events)} juegos")
        except Exception as e:
            logger.error(f"Error NBA ESPN games {d}: {e}")

    cache[cache_key] = games
    _save_cache(cache)
    return games


def get_team_stats_espn(team_id: str) -> dict | None:
    """Stats de un equipo NBA via ESPN."""
    cache = _load_cache()
    cache_key = f"nba_espn_team_{team_id}_{date.today().isoformat()}"

    if cache_key in cache:
        return cache[cache_key]

    try:
        data = _get(f"teams/{team_id}/statistics")
        splits = data.get("splits", {}).get("categories", [])

        stats = {}
        for cat in splits:
            cat_name = cat.get("name", "")
            for stat in cat.get("stats", []):
                key = f"{cat_name}_{stat.get('name', '')}".lower().replace(" ", "_")
                stats[key] = stat.get("value", 0)

        cache[cache_key] = stats
        _save_cache(cache)
        return stats
    except Exception as e:
        logger.error(f"Error ESPN team stats {team_id}: {e}")
        return None


def get_recent_games_espn(team_id: str, last: int = 5) -> list[dict]:
    """Últimos N partidos de un equipo NBA via ESPN."""
    cache = _load_cache()
    cache_key = f"nba_espn_recent_{team_id}_{date.today().isoformat()}"

    if cache_key in cache:
        return cache[cache_key][:last]

    try:
        data = _get(f"teams/{team_id}/schedule")
        events = data.get("events", [])

        # Filtra solo partidos ya terminados
        completed = [
            e for e in events
            if e.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed", False)
        ]
        recent = completed[-last:] if len(completed) >= last else completed

        parsed = []
        for ev in recent:
            comp = ev.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})
            is_home = home.get("team", {}).get("id") == team_id

            parsed.append({
                "date":        ev.get("date"),
                "is_home":     is_home,
                "team_score":  int(home.get("score", 0) if is_home else away.get("score", 0)),
                "opp_score":   int(away.get("score", 0) if is_home else home.get("score", 0)),
                "won":         home.get("winner", False) if is_home else away.get("winner", False),
            })

        cache[cache_key] = parsed
        _save_cache(cache)
        return parsed
    except Exception as e:
        logger.error(f"Error ESPN recent games {team_id}: {e}")
        return []


def extract_nba_features(team_id: str, is_home: bool) -> dict:
    """Features NBA para el modelo ML."""
    recent = get_recent_games_espn(team_id, last=10)

    if not recent:
        return _empty_nba_features(is_home)

    pts_for     = [g["team_score"] for g in recent]
    pts_against = [g["opp_score"]  for g in recent]
    wins        = [g["won"] for g in recent]

    last5  = recent[-5:]
    wins_l5 = sum(1 for g in last5 if g["won"])
    pts_l5  = [g["team_score"] for g in last5]

    return {
        "nba_avg_pts_for":      sum(pts_for) / len(pts_for),
        "nba_avg_pts_against":  sum(pts_against) / len(pts_against),
        "nba_win_rate_last10":  sum(wins) / len(wins),
        "nba_wins_last5":       wins_l5,
        "nba_avg_pts_last5":    sum(pts_l5) / max(len(pts_l5), 1),
        "nba_is_home":          1 if is_home else 0,
        "nba_home_advantage":   3.2 if is_home else 0.0,
    }


def _empty_nba_features(is_home: bool) -> dict:
    return {
        "nba_avg_pts_for": 110.0, "nba_avg_pts_against": 110.0,
        "nba_win_rate_last10": 0.5, "nba_wins_last5": 2,
        "nba_avg_pts_last5": 110.0,
        "nba_is_home": 1 if is_home else 0,
        "nba_home_advantage": 3.2 if is_home else 0.0,
    }


if __name__ == "__main__":
    logger.info("=== Test: NBA Harvester (ESPN) ===")
    games = get_games_today()
    logger.info(f"Juegos NBA hoy/mañana: {len(games)}")
    if games:
        g = games[0]
        logger.info(f"  {g['home_team']} vs {g['away_team']} — {g['date']}")
