"""
ml/data_loader.py
Carga datos históricos desde API-Football (ya funciona) + caché local.
Gestiona requests con cuidado del límite de 100/día.
Alternativa: genera datos bootstrap desde las odds actuales de Supabase.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import time
import pandas as pd
import httpx
from datetime import date, timedelta
from loguru import logger
from config.settings import RAPIDAPI_KEY, DATA_DIR
from harvesters.db_client import select, get_api_usage

RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(exist_ok=True)

HEADERS = {"x-apisports-key": RAPIDAPI_KEY}

# Ligas con sus IDs de API-Football
LEAGUES = {
    39:  "Premier League",
    140: "LaLiga",
    135: "Serie A",
    78:  "Bundesliga",
    61:  "Ligue 1",
}

SEASON = 2024  # Temporada actual completa con resultados


def _api_get(endpoint: str, params: dict) -> dict | None:
    """Request a API-Football con control de rate limit."""
    used = get_api_usage("api_football")
    if used >= 95:
        logger.warning(f"Rate limit API-Football casi alcanzado ({used}/100). Usando caché.")
        return None
    try:
        resp = httpx.get(
            f"https://v3.football.api-sports.io/{endpoint}",
            headers=HEADERS, params=params, timeout=20
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"API-Football {endpoint}: {e}")
        return None


def download_league_history(league_id: int, season: int = SEASON) -> pd.DataFrame:
    """
    Descarga el historial de partidos terminados de una liga/temporada.
    Cachea en disco para no repetir requests.
    """
    cache_path = RAW_DIR / f"apifootball_{league_id}_{season}.json"

    if cache_path.exists():
        logger.debug(f"Cache hit: liga {league_id} temporada {season}")
        with open(cache_path) as f:
            fixtures = json.load(f)
    else:
        logger.info(f"Descargando fixtures: liga {league_id} temporada {season}...")
        data = _api_get("fixtures", {
            "league":  league_id,
            "season":  season,
            "status":  "FT",   # Solo partidos terminados
        })
        if not data:
            return pd.DataFrame()

        fixtures = data.get("response", [])
        logger.info(f"  {LEAGUES.get(league_id, league_id)}: {len(fixtures)} partidos")

        with open(cache_path, "w") as f:
            json.dump(fixtures, f)

        time.sleep(1)  # Pausa entre requests

    return _parse_fixtures(fixtures, league_id)


def _parse_fixtures(fixtures: list, league_id: int) -> pd.DataFrame:
    """Convierte respuesta de API-Football a DataFrame estandarizado."""
    rows = []
    for f in fixtures:
        fix   = f.get("fixture", {})
        teams = f.get("teams", {})
        goals = f.get("goals", {})
        score = f.get("score", {})

        home_goals = goals.get("home")
        away_goals = goals.get("away")

        if home_goals is None or away_goals is None:
            continue

        # Resultado
        if home_goals > away_goals:
            ftr = "H"
        elif away_goals > home_goals:
            ftr = "A"
        else:
            ftr = "D"

        # Stats de tiro (si disponibles)
        stats = {s["team"]["id"]: s["statistics"] for s in f.get("statistics", [])} if "statistics" in f else {}

        def get_stat(team_id, stat_name):
            for s in stats.get(team_id, []):
                if s.get("type") == stat_name:
                    val = s.get("value")
                    return float(val) if val is not None else 0.0
            return 0.0

        home_id = teams.get("home", {}).get("id")
        away_id = teams.get("away", {}).get("id")

        rows.append({
            "Date":     pd.to_datetime(fix.get("date")),
            "HomeTeam": teams.get("home", {}).get("name", ""),
            "AwayTeam": teams.get("away", {}).get("name", ""),
            "FTHG":     float(home_goals),
            "FTAG":     float(away_goals),
            "FTR":      ftr,
            "HTHG":     float(score.get("halftime", {}).get("home") or 0),
            "HTAG":     float(score.get("halftime", {}).get("away") or 0),
            "HS":       get_stat(home_id, "Shots on Goal"),
            "AS":       get_stat(away_id, "Shots on Goal"),
            "HST":      get_stat(home_id, "Shots on Target"),
            "AST":      get_stat(away_id, "Shots on Target"),
            "liga":     LEAGUES.get(league_id, str(league_id)),
            "league_id": league_id,
            "season":   SEASON,
        })

    return pd.DataFrame(rows)


def load_all_history(league_ids: list[int] = None, season: int = SEASON) -> pd.DataFrame:
    """
    Carga todos los datos históricos. Usa caché agresivo para no quemar API.
    Con el límite de 100 req/día, descargamos de a 1-2 ligas por día.
    """
    league_ids = league_ids or list(LEAGUES.keys())

    frames = []
    for lid in league_ids:
        df = download_league_history(lid, season)
        if not df.empty:
            frames.append(df)

    if not frames:
        logger.error("No se pudieron cargar datos históricos")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("Date").reset_index(drop=True)
    combined = combined[combined["FTR"].isin(["H", "D", "A"])]

    logger.info(f"\nTotal partidos históricos: {len(combined)}")
    logger.info(f"Ligas: {combined['liga'].unique().tolist()}")
    logger.info(f"Rango: {combined['Date'].min().date()} → {combined['Date'].max().date()}")
    return combined


def get_odds_cols(df: pd.DataFrame) -> list[str]:
    """Columnas de odds disponibles."""
    all_odds = ["B365H","B365D","B365A","BWH","BWD","BWA","PSH","PSD","PSA"]
    return [c for c in all_odds if c in df.columns]


if __name__ == "__main__":
    # Descarga solo Premier League para testear (1 request)
    df = load_all_history(league_ids=[39], season=2024)
    if not df.empty:
        logger.info(f"\nMuestra:\n{df[['Date','HomeTeam','AwayTeam','FTR','FTHG','FTAG']].head(5).to_string()}")
        logger.info(f"\nDistribución FTR:\n{df['FTR'].value_counts().to_string()}")
