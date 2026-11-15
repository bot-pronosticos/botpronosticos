"""
ml/feature_engineer.py
Construye el vector de features por partido usando solo datos previos al partido.
Sin leakage — cada partido solo ve datos de partidos anteriores.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from loguru import logger


def compute_rolling_team_stats(df: pd.DataFrame, team: str,
                               before_date: pd.Timestamp, n: int = 10) -> dict:
    """
    Calcula stats rolling de un equipo antes de una fecha específica.
    Usa tanto partidos de local como visitante.
    """
    home_mask = (df["HomeTeam"] == team) & (df["Date"] < before_date)
    away_mask = (df["AwayTeam"] == team) & (df["Date"] < before_date)

    home_games = df[home_mask].tail(n)
    away_games = df[away_mask].tail(n)

    # Últimos N partidos combinados
    home_games = home_games.copy()
    away_games = away_games.copy()

    home_games["gf"] = home_games.get("FTHG", pd.Series(dtype=float))
    home_games["ga"] = home_games.get("FTAG", pd.Series(dtype=float))
    home_games["won"] = (home_games.get("FTR", "") == "H").astype(int)
    home_games["drew"] = (home_games.get("FTR", "") == "D").astype(int)

    away_games["gf"] = away_games.get("FTAG", pd.Series(dtype=float))
    away_games["ga"] = away_games.get("FTHG", pd.Series(dtype=float))
    away_games["won"] = (away_games.get("FTR", "") == "A").astype(int)
    away_games["drew"] = (away_games.get("FTR", "") == "D").astype(int)

    all_games = pd.concat([home_games, away_games]).sort_values("Date").tail(n)

    if len(all_games) == 0:
        return _empty_team_stats()

    n_played = len(all_games)
    gf = all_games["gf"].fillna(0)
    ga = all_games["ga"].fillna(0)
    won  = all_games["won"].fillna(0)
    drew = all_games["drew"].fillna(0)

    # Shots stats (si disponibles)
    if "HS" in df.columns and "AS" in df.columns:
        sh_for = pd.concat([
            home_games["HS"].fillna(0) if "HS" in home_games.columns else pd.Series(dtype=float),
            away_games["AS"].fillna(0) if "AS" in away_games.columns else pd.Series(dtype=float),
        ]).tail(n).mean()
        sh_ag = pd.concat([
            home_games["AS"].fillna(0) if "AS" in home_games.columns else pd.Series(dtype=float),
            away_games["HS"].fillna(0) if "HS" in away_games.columns else pd.Series(dtype=float),
        ]).tail(n).mean()
    else:
        sh_for, sh_ag = 0.0, 0.0

    return {
        "gf_avg":    float(gf.mean()),
        "ga_avg":    float(ga.mean()),
        "gd_avg":    float((gf - ga).mean()),
        "win_rate":  float(won.mean()),
        "draw_rate": float(drew.mean()),
        "loss_rate": float(1 - won.mean() - drew.mean()),
        "form5":     float(won.tail(5).mean()),       # Forma últimos 5
        "gf_last5":  float(gf.tail(5).mean()),
        "ga_last5":  float(ga.tail(5).mean()),
        "sh_for_avg": float(sh_for),
        "sh_ag_avg":  float(sh_ag),
        "n_played":  n_played,
    }


def _empty_team_stats() -> dict:
    return {
        "gf_avg": 1.3, "ga_avg": 1.3, "gd_avg": 0.0,
        "win_rate": 0.33, "draw_rate": 0.28, "loss_rate": 0.39,
        "form5": 0.33, "gf_last5": 1.3, "ga_last5": 1.3,
        "sh_for_avg": 12.0, "sh_ag_avg": 12.0, "n_played": 0,
    }


def build_match_features(row: pd.Series, df: pd.DataFrame) -> dict:
    """
    Construye el vector de features para un partido dado.
    df debe contener SOLO partidos anteriores a row['Date'] para evitar leakage.
    """
    home = row["HomeTeam"]
    away = row["AwayTeam"]
    date = row["Date"]

    h_stats = compute_rolling_team_stats(df, home, date, n=10)
    a_stats = compute_rolling_team_stats(df, away, date, n=10)

    # H2H últimos 5 enfrentamientos
    h2h_mask = (
        ((df["HomeTeam"] == home) & (df["AwayTeam"] == away)) |
        ((df["HomeTeam"] == away) & (df["AwayTeam"] == home))
    ) & (df["Date"] < date)
    h2h = df[h2h_mask].tail(5)
    h2h_home_wins = 0
    if len(h2h) > 0:
        h2h_home_wins = sum(
            1 for _, r in h2h.iterrows()
            if (r["HomeTeam"] == home and r["FTR"] == "H") or
               (r["HomeTeam"] == away and r["FTR"] == "A")
        ) / len(h2h)

    features = {
        # Features del local
        "h_gf_avg":    h_stats["gf_avg"],
        "h_ga_avg":    h_stats["ga_avg"],
        "h_gd_avg":    h_stats["gd_avg"],
        "h_win_rate":  h_stats["win_rate"],
        "h_draw_rate": h_stats["draw_rate"],
        "h_form5":     h_stats["form5"],
        "h_gf_last5":  h_stats["gf_last5"],
        "h_ga_last5":  h_stats["ga_last5"],
        "h_sh_for":    h_stats["sh_for_avg"],
        "h_sh_ag":     h_stats["sh_ag_avg"],

        # Features del visitante
        "a_gf_avg":    a_stats["gf_avg"],
        "a_ga_avg":    a_stats["ga_avg"],
        "a_gd_avg":    a_stats["gd_avg"],
        "a_win_rate":  a_stats["win_rate"],
        "a_draw_rate": a_stats["draw_rate"],
        "a_form5":     a_stats["form5"],
        "a_gf_last5":  a_stats["gf_last5"],
        "a_ga_last5":  a_stats["ga_last5"],
        "a_sh_for":    a_stats["sh_for_avg"],
        "a_sh_ag":     a_stats["sh_ag_avg"],

        # Diferencias relativas (captura el gap entre equipos)
        "gf_diff":     h_stats["gf_avg"]   - a_stats["gf_avg"],
        "ga_diff":     h_stats["ga_avg"]   - a_stats["ga_avg"],
        "win_diff":    h_stats["win_rate"] - a_stats["win_rate"],
        "form_diff":   h_stats["form5"]    - a_stats["form5"],
        "gd_diff":     h_stats["gd_avg"]   - a_stats["gd_avg"],

        # H2H
        "h2h_home_win_rate": h2h_home_wins,
        "h2h_n":             len(h2h),

        # Ventaja de local (feature fija — históricamente ~45% gana el local)
        "home_advantage": 1.0,
    }

    return features


from config.settings import DATA_DIR
CACHE_FEATURES = DATA_DIR / "features_dataset.pkl"


def build_dataset(df: pd.DataFrame, force_rebuild: bool = False) -> tuple[pd.DataFrame, pd.Series]:
    """
    Construye el dataset completo de features + targets.
    Aplica walk-forward: cada partido solo ve datos de partidos anteriores.
    """
    if not force_rebuild and CACHE_FEATURES.exists():
        try:
            import joblib
            cached = joblib.load(CACHE_FEATURES)
            logger.info(f"Cache hit: cargando {len(cached['X'])} partidos con features precalculadas.")
            return cached["X"], cached["y"]
        except Exception:
            pass

    logger.info(f"Construyendo features para {len(df)} partidos...")

    # Necesitamos mínimo 10 partidos de historia por equipo
    # Saltamos los primeros partidos de cada temporada
    df = df.sort_values("Date").reset_index(drop=True)

    feature_rows = []
    targets      = []
    dates        = []
    ligas        = []

    for i, row in df.iterrows():
        # Solo usar partidos anteriores para las features (walk-forward)
        history = df.iloc[:i]

        if len(history) < 20:
            continue

        # Verificar que el equipo tiene suficiente historia
        home_hist = len(history[
            (history["HomeTeam"] == row["HomeTeam"]) |
            (history["AwayTeam"] == row["HomeTeam"])
        ])
        away_hist = len(history[
            (history["HomeTeam"] == row["AwayTeam"]) |
            (history["AwayTeam"] == row["AwayTeam"])
        ])
        if home_hist < 5 or away_hist < 5:
            continue

        try:
            feat = build_match_features(row, history)
            feature_rows.append(feat)

            # Target: 0=visitante gana, 1=empate, 2=local gana
            result_map = {"A": 0, "D": 1, "H": 2}
            targets.append(result_map[row["FTR"]])
            dates.append(row["Date"])
            ligas.append(row.get("liga", ""))
        except Exception as e:
            logger.debug(f"Skip partido {i}: {e}")
            continue

        if i % 500 == 0 and i > 0:
            logger.info(f"  Procesados: {i}/{len(df)}")

    X = pd.DataFrame(feature_rows)
    y = pd.Series(targets, name="resultado")

    # Agregar metadata
    X["_date"] = dates
    X["_liga"] = ligas

    # Guardar en cache
    try:
        import joblib
        joblib.dump({"X": X, "y": y}, CACHE_FEATURES)
    except Exception as e:
        logger.warning(f"No se pudo guardar cache de features: {e}")

    logger.info(f"Dataset construido: {len(X)} partidos con features completas")
    logger.info(f"Distribución: Local={sum(y==2)} | Empate={sum(y==1)} | Visitante={sum(y==0)}")
    return X, y


if __name__ == "__main__":
    from ml.data_loader import load_all_history
    df = load_all_history(leagues=["E0"], seasons=["2324", "2223"])
    X, y = build_dataset(df)
    logger.info(f"\nFeatures shape: {X.shape}")
    logger.info(f"\nPrimeras features:\n{X.drop(columns=['_date','_liga']).head(2).to_string()}")
