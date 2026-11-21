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


def _get_val(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if not val:
        return default
    return val


def _optional(key: str, default=None):
    return os.getenv(key, default)


# ── Telegram ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = _get_val("TELEGRAM_BOT_TOKEN", "9128575947:AAHg5bFa_keV9uc-0rgZ0gyZsxg8U28PBzM")
TELEGRAM_CHAT_ID   = _get_val("TELEGRAM_CHAT_ID", "5241095844")

# ── Supabase ──────────────────────────────────────────────
SUPABASE_URL = _get_val("SUPABASE_URL", "https://rvzmdpvjgactqokepxsv.supabase.co")
SUPABASE_KEY = _get_val("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ2em1kcHZqZ2FjdHFva2VweHN2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ3NjI0NzEsImV4cCI6MjExMDMzODQ3MX0.3jlVqitiKlu2lt3N1vwNOsLiOovc9zVToNqvAe_DGQY")

# ── APIs externas ─────────────────────────────────────────
ODDS_API_KEY   = _get_val("ODDS_API_KEY", "1cc8138c10617d6ee7375552a40b6535")
RAPIDAPI_KEY   = _get_val("RAPIDAPI_KEY", "7aaa7822195e7c940c7359f7cbd08077")

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
