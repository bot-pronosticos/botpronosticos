"""
scripts/run_pipeline.py
Pipeline principal — Fase 1 completa.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
from datetime import datetime
from loguru import logger

from harvesters.odds_harvester     import harvest_odds, save_odds_to_db
from harvesters.football_harvester import get_standings
from harvesters.basketball_harvester import get_games_today
from config.settings import FOOTBALL_LEAGUES


def step_odds():
    logger.info("── PASO 1: Odds Harvester ──────────────────")
    start = time.time()
    events = harvest_odds()
    logger.info(f"   Eventos con odds: {len(events)}")
    saved = save_odds_to_db(events)
    logger.info(f"   Guardados en BD:  {saved}  ({time.time()-start:.1f}s)")
    return events


def step_football_stats():
    logger.info("── PASO 2: Football Stats ──────────────────")
    start = time.time()
    for league_id in FOOTBALL_LEAGUES[:3]:
        try:
            s = get_standings(league_id)
            logger.info(f"   Liga {league_id}: {len(s)} grupos OK")
        except Exception as e:
            logger.warning(f"   Liga {league_id}: {e}")
    logger.info(f"   Tiempo: {time.time()-start:.1f}s")


def step_nba():
    logger.info("── PASO 3: NBA Stats ───────────────────────")
    start = time.time()
    games = get_games_today()
    logger.info(f"   Juegos NBA hoy/mañana: {len(games)}  ({time.time()-start:.1f}s)")
    return games


def run_pipeline():
    logger.info("=" * 50)
    logger.info(f"  PIPELINE START — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)
    t0 = time.time()

    events    = step_odds()
    step_football_stats()
    nba_games = step_nba()

    logger.info("=" * 50)
    logger.info(f"  COMPLETO en {time.time()-t0:.1f}s")
    logger.info(f"  Partidos guardados: {len(events)} | NBA: {len(nba_games)}")
    logger.info("=" * 50)


if __name__ == "__main__":
    run_pipeline()
