"""
ml/data_loader.py
Carga datos históricos estandarizados de las 5 Grandes Ligas europeas:
- Fuente: Espejo público de football-data / footballcsv (GitHub raw, 100% libre de rate limits).
- Cachea localmente en data/raw/football_history_top5.json.
- Normaliza nombres de clubes para que coincidan con The Odds API y Pinnacle.
- Retorna DataFrame compatible con feature_engineer.py y predictor.py.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import io
import pandas as pd
import httpx
from loguru import logger
from config.settings import DATA_DIR

RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(exist_ok=True)
CACHE_FILE = RAW_DIR / "football_history_top5.json"

GITHUB_BASE = "https://raw.githubusercontent.com/footballcsv/cache.footballdata/master/2023-24"

LEAGUE_FILES = {
    "LaLiga":         "es.1.csv",
    "Serie A":        "it.1.csv",
    "Bundesliga":     "de.1.csv",
    "Ligue 1":        "fr.1.csv",
    "Premier League": "eng.1.csv",
}

# Diccionario de normalización de nombres para coincidir con bookmakers (Pinnacle, DraftKings, 1xBet)
TEAM_NORMALIZATION = {
    # España
    "Betis":          "Real Betis",
    "Ath Bilbao":     "Athletic Bilbao",
    "Ath Madrid":     "Atletico Madrid",
    "Sociedad":       "Real Sociedad",
    "Vallecano":      "Rayo Vallecano",
    "Celta":          "Celta Vigo",
    "Alaves":         "Deportivo Alavés",
    # Inglaterra
    "Man City":       "Manchester City",
    "Man United":     "Manchester United",
    "Nott'm Forest":  "Nottingham Forest",
    "Wolves":         "Wolverhampton Wanderers",
    "Newcastle":      "Newcastle United",
    "Brighton":       "Brighton and Hove Albion",
    "West Ham":       "West Ham United",
    # Italia
    "Inter":          "Inter Milan",
    "Milan":          "AC Milan",
    "Roma":           "AS Roma",
    # Alemania
    "Bayern Munich":  "Bayern Munich",
    "Leverkusen":     "Bayer Leverkusen",
    "Dortmund":       "Borussia Dortmund",
    "M'gladbach":     "Borussia Monchengladbach",
    "Frankfurt":      "Eintracht Frankfurt",
    # Francia
    "Paris SG":       "Paris Saint Germain",
    "St Etienne":     "Saint-Etienne",
}


def _download_and_parse() -> pd.DataFrame:
    """Descarga los CSVs históricos desde el mirror de GitHub y los estandariza."""
    frames = []

    for liga_name, fname in LEAGUE_FILES.items():
        url = f"{GITHUB_BASE}/{fname}"
        try:
            r = httpx.get(url, timeout=20)
            if r.status_code != 200:
                logger.warning(f"No se pudo descargar {liga_name} ({url}) status {r.status_code}")
                continue

            raw_df = pd.read_csv(io.StringIO(r.text))
            rows = []

            for _, row in raw_df.iterrows():
                ft = str(row.get("FT", "")).strip()
                if not ft or "-" not in ft:
                    continue
                try:
                    gh_str, ga_str = ft.split("-")
                    gh, ga = float(gh_str), float(ga_str)
                except Exception:
                    continue

                ftr = "H" if gh > ga else ("A" if ga > gh else "D")

                # Marcador medio tiempo
                ht = str(row.get("HT", "")).strip()
                hth, hta = 0.0, 0.0
                if ht and "-" in ht:
                    try:
                        h1, h2 = ht.split("-")
                        hth, hta = float(h1), float(h2)
                    except Exception:
                        pass

                raw_h = str(row.get("Team 1", "")).strip()
                raw_a = str(row.get("Team 2", "")).strip()
                h_team = TEAM_NORMALIZATION.get(raw_h, raw_h)
                a_team = TEAM_NORMALIZATION.get(raw_a, raw_a)

                date_val = pd.to_datetime(row.get("Date"))

                rows.append({
                    "Date":     date_val.isoformat(),
                    "HomeTeam": h_team,
                    "AwayTeam": a_team,
                    "FTHG":     gh,
                    "FTAG":     ga,
                    "FTR":      ftr,
                    "HTHG":     hth,
                    "HTAG":     hta,
                    "HS":       0.0,
                    "AS":       0.0,
                    "HST":      0.0,
                    "AST":      0.0,
                    "liga":     liga_name,
                    "season":   2024,
                })

            df_l = pd.DataFrame(rows)
            frames.append(df_l)
            logger.info(f"Historial {liga_name}: {len(df_l)} partidos descargados.")
        except Exception as e:
            logger.error(f"Error procesando {liga_name}: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    return combined


def load_all_history(force_refresh: bool = False) -> pd.DataFrame:
    """
    Carga todos los datos históricos con caché en disco local.
    """
    if not force_refresh and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
            df = pd.DataFrame(records)
            df["Date"] = pd.to_datetime(df["Date"], utc=True)
            logger.info(f"Historial cargado desde caché: {len(df)} partidos ({df['liga'].unique().tolist()})")
            return df
        except Exception as e:
            logger.warning(f"Error leyendo caché {CACHE_FILE}: {e}")

    logger.info("Descargando historial de las 5 Grandes Ligas...")
    df = _download_and_parse()
    if df.empty:
        logger.error("No se pudieron cargar datos históricos de fútbol.")
        return pd.DataFrame()

    # Guardar en caché
    try:
        records = df.to_dict(orient="records")
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, default=str)
        logger.info(f"Caché guardada en {CACHE_FILE}")
    except Exception as e:
        logger.warning(f"No se pudo guardar caché: {e}")

    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    logger.info(f"Total partidos históricos: {len(df)}")
    return df


if __name__ == "__main__":
    df = load_all_history(force_refresh=True)
    print("Muestra:\n", df[['Date', 'HomeTeam', 'AwayTeam', 'FTR', 'FTHG', 'FTAG', 'liga']].head(5))
