"""
ml/backtester.py
Simula picks históricos con EV filter y Kelly Criterion.
Genera curva de bankroll y métricas reales de rendimiento.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from loguru import logger
from config.settings import EV_THRESHOLD, KELLY_FRACTION


def implied_prob(odds: float) -> float:
    """Probabilidad implícita de una cuota decimal."""
    if odds <= 1.0:
        return 1.0
    return 1.0 / odds


def compute_ev(prob_model: float, odds: float) -> float:
    """EV = prob_modelo × (cuota - 1) - (1 - prob_modelo)"""
    return prob_model * (odds - 1) - (1 - prob_model)


def kelly_fraction(prob: float, odds: float, fraction: float = KELLY_FRACTION) -> float:
    """Kelly Criterion fraccionado."""
    b = odds - 1
    if b <= 0 or prob <= 0:
        return 0.0
    k = (prob * b - (1 - prob)) / b
    return max(0.0, k * fraction)


def run_backtest(
    X_test: pd.DataFrame,
    y_test: pd.Series,
    ensemble_proba: np.ndarray,
    df_raw: pd.DataFrame,
    ev_threshold: float = EV_THRESHOLD,
    initial_bankroll: float = 100.0,
) -> dict:
    """
    Simula picks históricos sobre el set de test.
    Usa cuotas reales de Bet365/Pinnacle del CSV histórico.

    Retorna dict con métricas y curva de bankroll.
    """
    result_map = {0: "A", 1: "D", 2: "H"}  # 0=visitante, 1=empate, 2=local
    odds_map = {
        "H": ["B365H", "PSH", "BWH"],
        "D": ["B365D", "PSD", "BWD"],
        "A": ["B365A", "PSA", "BWA"],
    }

    bankroll   = initial_bankroll
    history    = []
    picks_sent = 0
    correct    = 0

    # Alinear índices
    test_dates = X_test["_date"].values if "_date" in X_test.columns else [None] * len(X_test)
    test_ligas = X_test["_liga"].values if "_liga" in X_test.columns else [""] * len(X_test)

    for i, (idx, row) in enumerate(X_test.iterrows()):
        date   = test_dates[i]
        actual = result_map[y_test.iloc[i]]

        proba = ensemble_proba[i]  # [prob_A, prob_D, prob_H]

        # Buscar el partido en df_raw para obtener las cuotas reales
        if date is not None and "_liga" in X_test.columns:
            match_rows = df_raw[
                (df_raw["Date"] == date) &
                (df_raw["liga"] == test_ligas[i])
            ]
        else:
            match_rows = pd.DataFrame()

        # Para cada outcome, calcular EV con las cuotas reales
        best_pick = None
        best_ev   = -999

        for outcome_idx, outcome in [(2, "H"), (1, "D"), (0, "A")]:
            prob_model = proba[outcome_idx]

            # Buscar mejor cuota disponible
            best_odds = 0.0
            if not match_rows.empty:
                for col in odds_map.get(outcome, []):
                    if col in match_rows.columns:
                        val = match_rows[col].iloc[0]
                        if pd.notna(val) and float(val) > best_odds:
                            best_odds = float(val)

            if best_odds <= 1.0:
                # Fallback: usar probabilidad implícita 1/prob con margen del 5%
                best_odds = (1.0 / prob_model) * 0.95 if prob_model > 0.1 else 0.0

            if best_odds <= 1.0:
                continue

            ev = compute_ev(prob_model, best_odds)

            if ev > best_ev and ev > ev_threshold:
                best_ev   = ev
                best_pick = {
                    "outcome":    outcome,
                    "prob_model": prob_model,
                    "odds":       best_odds,
                    "ev":         ev,
                    "kelly":      kelly_fraction(prob_model, best_odds),
                    "actual":     actual,
                    "correct":    actual == outcome,
                    "date":       date,
                    "liga":       test_ligas[i] if "_liga" in X_test.columns else "",
                    "bankroll_before": bankroll,
                }

        if best_pick is None:
            continue

        # Calcular stake y resultado
        stake = bankroll * best_pick["kelly"]
        stake = min(stake, bankroll * 0.05)  # Cap: máximo 5% del bankroll por pick

        if best_pick["correct"]:
            ganancia = stake * (best_pick["odds"] - 1)
            correct += 1
        else:
            ganancia = -stake

        bankroll += ganancia
        bankroll = max(bankroll, 0.01)  # Nunca va a 0 exacto

        best_pick["stake"]    = stake
        best_pick["ganancia"] = ganancia
        best_pick["bankroll_after"] = bankroll

        history.append(best_pick)
        picks_sent += 1

    if picks_sent == 0:
        logger.warning("No se generaron picks en el backtest — ajusta el EV threshold")
        return {"picks": 0, "roi": 0, "accuracy": 0, "brier": 0}

    df_hist = pd.DataFrame(history)

    roi = (bankroll - initial_bankroll) / initial_bankroll * 100
    accuracy = correct / picks_sent
    avg_ev   = df_hist["ev"].mean()
    avg_odds = df_hist["odds"].mean()

    logger.info("\n=== BACKTEST RESULTS ===")
    logger.info(f"Partidos evaluados: {len(X_test)}")
    logger.info(f"Picks generados:    {picks_sent} ({picks_sent/len(X_test)*100:.1f}% de los partidos)")
    logger.info(f"Picks correctos:    {correct}/{picks_sent} ({accuracy*100:.1f}%)")
    logger.info(f"ROI final:          {roi:+.2f}%")
    logger.info(f"Bankroll final:     {bankroll:.2f} (desde {initial_bankroll})")
    logger.info(f"EV promedio:        {avg_ev*100:.2f}%")
    logger.info(f"Cuota promedio:     {avg_odds:.2f}")

    # Distribución por liga
    if "liga" in df_hist.columns:
        logger.info("\nPor liga:")
        for liga, grp in df_hist.groupby("liga"):
            acc_l = grp["correct"].mean()
            roi_l = grp["ganancia"].sum() / grp["stake"].sum() * 100 if grp["stake"].sum() > 0 else 0
            logger.info(f"  {liga:20s} — {len(grp):3d} picks | {acc_l*100:.1f}% acc | {roi_l:+.1f}% ROI")

    return {
        "picks":          picks_sent,
        "correct":        correct,
        "accuracy":       accuracy,
        "roi_pct":        roi,
        "bankroll_final": bankroll,
        "avg_ev":         avg_ev,
        "avg_odds":       avg_odds,
        "history":        df_hist,
    }


if __name__ == "__main__":
    logger.info("El backtester se ejecuta desde scripts/train_and_backtest.py")
