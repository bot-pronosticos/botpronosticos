"""
config/settings.py
Carga y valida todas las variables de entorno del proyecto.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Busca el .env en la carpeta config/
ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Variable de entorno requerida no encontrada: {key}")
    return val


def _optional(key: str, default=None):
    return os.getenv(key, default)


# ── Telegram ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = _require("TELEGRAM_CHAT_ID")

# ── Supabase ──────────────────────────────────────────────
SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_KEY = _require("SUPABASE_KEY")

# ── APIs externas ─────────────────────────────────────────
ODDS_API_KEY   = _require("ODDS_API_KEY")
RAPIDAPI_KEY   = _require("RAPIDAPI_KEY")

# ── Parámetros del modelo ─────────────────────────────────
EV_THRESHOLD   = float(_optional("EV_THRESHOLD", "0.05"))
KELLY_FRACTION = float(_optional("KELLY_FRACTION", "0.25"))
BANKROLL       = float(_optional("BANKROLL", "100"))

# ── Ligas de fútbol a monitorear ─────────────────────────
FOOTBALL_LEAGUES = [
    int(x) for x in _optional("FOOTBALL_LEAGUES", "39,140,135,78,61").split(",")
]

# ── Entorno ───────────────────────────────────────────────
ENV       = _optional("ENV", "development")
LOG_LEVEL = _optional("LOG_LEVEL", "INFO")

# ── Rutas del proyecto ────────────────────────────────────
ROOT_DIR   = Path(__file__).parent.parent
DATA_DIR   = ROOT_DIR / "data"
MODELS_DIR = ROOT_DIR / "models"
LOGS_DIR   = ROOT_DIR / "logs"

# Crear directorios si no existen
for d in [DATA_DIR, MODELS_DIR, LOGS_DIR]:
    d.mkdir(exist_ok=True)
