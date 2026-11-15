"""
scripts/setup_db.py
Verifica la conexión a Supabase y muestra el estado de las tablas.
Ejecutar una vez después de correr el schema.sql en Supabase.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from harvesters.db_client import get_db

REQUIRED_TABLES = ["partidos", "odds", "features", "picks", "modelo_metricas", "api_usage"]


def verify_tables():
    db = get_db()
    logger.info("Verificando tablas en Supabase...")

    all_ok = True
    for table in REQUIRED_TABLES:
        try:
            result = db.table(table).select("*").limit(1).execute()
            logger.success(f"  ✅ {table} — OK")
        except Exception as e:
            logger.error(f"  ❌ {table} — ERROR: {e}")
            all_ok = False

    return all_ok


def verify_api_keys():
    """Testea que las API keys respondan."""
    import httpx
    from config.settings import ODDS_API_KEY, RAPIDAPI_KEY

    logger.info("Verificando API keys...")

    # Test The Odds API
    try:
        resp = httpx.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": ODDS_API_KEY},
            timeout=10
        )
        if resp.status_code == 200:
            remaining = resp.headers.get("x-requests-remaining", "?")
            logger.success(f"  ✅ The Odds API — OK | Requests restantes: {remaining}")
        else:
            logger.error(f"  ❌ The Odds API — Status {resp.status_code}")
    except Exception as e:
        logger.error(f"  ❌ The Odds API — {e}")

    # Test API-Football
    try:
        resp = httpx.get(
            "https://v3.football.api-sports.io/status",
            headers={"x-apisports-key": RAPIDAPI_KEY},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            account = data.get("response", {}).get("account", {})
            requests = data.get("response", {}).get("requests", {})
            logger.success(
                f"  ✅ API-Football — OK | "
                f"Plan: {account.get('plan', '?')} | "
                f"Requests hoy: {requests.get('current', '?')}/{requests.get('limit_day', '?')}"
            )
        else:
            logger.error(f"  ❌ API-Football — Status {resp.status_code}")
    except Exception as e:
        logger.error(f"  ❌ API-Football — {e}")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("   SETUP VERIFICACIÓN — Bot de Pronósticos")
    logger.info("=" * 50)

    verify_api_keys()
    print()
    ok = verify_tables()

    if ok:
        logger.success("\n✅ Todo OK — El sistema está listo para correr")
    else:
        logger.error("\n❌ Hay tablas faltantes. Ejecuta scripts/schema.sql en Supabase primero.")
        logger.info("   Supabase → SQL Editor → pega el contenido de scripts/schema.sql → Run")
