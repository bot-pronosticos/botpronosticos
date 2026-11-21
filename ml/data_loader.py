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

SEASONS_HISTORICAS = ["2021-22", "2022-23", "2023-24"]
LEAGUE_FILES_CSV = {
    "LaLiga":         "es.1.csv",
    "Serie A":        "it.1.csv",
    "Bundesliga":     "de.1.csv",
    "Ligue 1":        "fr.1.csv",
    "Premier League": "eng.1.csv",
}

LEAGUES_CURRENT_2024 = {
    "Premier League": "E0",
    "LaLiga":         "SP1",
    "Serie A":        "I1",
    "Bundesliga":     "D1",
    "Ligue 1":        "F1",
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
    """Descarga e integra 4 temporadas históricas completas (2021-2025) de las 5 Grandes Ligas."""
    frames = []

    with httpx.Client(timeout=25) as client:
        # 1. Temporadas 2021-2024 (footballcsv mirror)
        for season in SEASONS_HISTORICAS:
            for liga_name, fname in LEAGUE_FILES_CSV.items():
                url = f"https://raw.githubusercontent.com/footballcsv/cache.footballdata/master/{season}/{fname}"
                try:
                    r = client.get(url)
                    if r.status_code != 200:
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

                        date_val = pd.to_datetime(row.get("Date"), errors="coerce")
                        if pd.isna(date_val):
                            continue

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
                            "season":   season,
                        })

                    df_l = pd.DataFrame(rows)
                    frames.append(df_l)
                    logger.info(f"Historial {season} {liga_name}: {len(df_l)} partidos descargados.")
                except Exception as e:
                    logger.error(f"Error procesando {season} {liga_name}: {e}")

        # 2. Temporada 2024-2025 fresca (FootballAI master mirror)
        for liga_name, code in LEAGUES_CURRENT_2024.items():
            url = f"https://raw.githubusercontent.com/janmark101/FootballAI/master/Data/{code}%202024_2025.csv"
            try:
                r = client.get(url)
                if r.status_code != 200:
                    continue
                raw_df = pd.read_csv(io.StringIO(r.text))
                rows = []
                for _, row in raw_df.iterrows():
                    raw_h = str(row.get("HomeTeam", "")).strip()
                    raw_a = str(row.get("AwayTeam", "")).strip()
                    h_team = TEAM_NORMALIZATION.get(raw_h, raw_h)
                    a_team = TEAM_NORMALIZATION.get(raw_a, raw_a)
                    fthg = float(row.get("FTHG", 0.0))
                    ftag = float(row.get("FTAG", 0.0))
                    ftr = str(row.get("FTR", "D")).strip()
                    hthg = float(row.get("HTHG", 0.0)) if pd.notna(row.get("HTHG")) else 0.0
                    htag = float(row.get("HTAG", 0.0)) if pd.notna(row.get("HTAG")) else 0.0
                    hs = float(row.get("HS", 0.0)) if pd.notna(row.get("HS")) else 0.0
                    as_shots = float(row.get("AS", 0.0)) if pd.notna(row.get("AS")) else 0.0
                    hst = float(row.get("HST", 0.0)) if pd.notna(row.get("HST")) else 0.0
                    ast = float(row.get("AST", 0.0)) if pd.notna(row.get("AST")) else 0.0

                    date_val = pd.to_datetime(row.get("Date"), format="%d/%m/%Y", errors="coerce")
                    if pd.isna(date_val):
                        date_val = pd.to_datetime(row.get("Date"), errors="coerce")
                    if pd.isna(date_val):
                        continue

                    rows.append({
                        "Date":     date_val.isoformat(),
                        "HomeTeam": h_team,
                        "AwayTeam": a_team,
                        "FTHG":     fthg,
                        "FTAG":     ftag,
                        "FTR":      ftr,
                        "HTHG":     hthg,
                        "HTAG":     htag,
                        "HS":       hs,
                        "AS":       as_shots,
                        "HST":      hst,
                        "AST":      ast,
                        "liga":     liga_name,
                        "season":   "2024-25",
                    })

                df_l = pd.DataFrame(rows)
                frames.append(df_l)
                logger.info(f"Historial 2024-25 {liga_name}: {len(df_l)} partidos descargados.")
            except Exception as e:
                logger.error(f"Error procesando 2024-25 {liga_name}: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["Date"] = pd.to_datetime(combined["Date"], utc=True)
    combined = combined.sort_values("Date").reset_index(drop=True)
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
