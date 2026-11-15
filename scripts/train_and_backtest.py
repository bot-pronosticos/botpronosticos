"""
scripts/train_and_backtest.py
Script maestro de Fase 2:
1. Descarga datos históricos
2. Construye features
3. Entrena ensemble
4. Corre backtest con EV filter
5. Muestra curva de bankroll
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from loguru import logger
from ml.data_loader import load_all_history
from ml.feature_engineer import build_dataset
from ml.trainer import train, temporal_split, get_feature_matrix
from ml.backtester import run_backtest


def main():
    logger.info("=" * 60)
    logger.info("   FASE 2 — ENTRENAMIENTO Y BACKTESTING")
    logger.info("=" * 60)

    # 1. Cargar datos históricos (descarga si no están en caché)
    logger.info("\n[1/4] Cargando datos históricos...")
    df = load_all_history()
    if df.empty:
        logger.error("No se pudieron cargar datos. Verifica conexión.")
        return

    # 2. Construir features (walk-forward, sin leakage)
    logger.info("\n[2/4] Construyendo features...")
    X, y = build_dataset(df)
    if len(X) < 100:
        logger.error(f"Muy pocos partidos con features: {len(X)}")
        return

    # 3. Entrenar ensemble
    logger.info("\n[3/4] Entrenando ensemble...")
    model_data = train(X, y)

    # 4. Backtest sobre el set de test
    logger.info("\n[4/4] Corriendo backtest con EV filter...")
    X_train, X_test, y_train, y_test = temporal_split(X, y)

    # Calcular probabilidades del ensemble sobre test set
    X_te_mat, feat_cols = get_feature_matrix(X_test)
    models = model_data["models"]

    ensemble_proba = np.mean([
        m.predict_proba(X_te_mat) for m in models.values()
    ], axis=0)

    results = run_backtest(
        X_test=X_test,
        y_test=y_test,
        ensemble_proba=ensemble_proba,
        df_raw=df,
        ev_threshold=0.05,
        initial_bankroll=100.0,
    )

    # Resumen final
    logger.info("\n" + "=" * 60)
    logger.info("   RESUMEN FASE 2")
    logger.info("=" * 60)
    for name, m in model_data["metrics"].items():
        logger.info(f"  {name:12s} Accuracy: {m['accuracy']:.3f} | Brier: {m['brier']:.4f}")

    logger.info(f"\n  BACKTEST (EV > 5%):")
    logger.info(f"  Picks:     {results.get('picks', 0)}")
    logger.info(f"  Aciertos:  {results.get('correct', 0)} ({results.get('accuracy',0)*100:.1f}%)")
    logger.info(f"  ROI:       {results.get('roi_pct', 0):+.2f}%")
    logger.info(f"  Bankroll:  100 → {results.get('bankroll_final', 100):.2f}")

    brier_ens = model_data["metrics"]["ensemble"]["brier"]
    roi = results.get("roi_pct", 0)

    logger.info("\n  EVALUACIÓN DEL MODELO:")
    if brier_ens < 0.22:
        logger.success(f"  ✅ Brier Score: {brier_ens:.4f} (objetivo < 0.22)")
    else:
        logger.warning(f"  ⚠️  Brier Score: {brier_ens:.4f} (objetivo < 0.22 — necesita más datos)")

    if roi > 5:
        logger.success(f"  ✅ ROI simulado: {roi:+.2f}% (objetivo > 5%)")
    elif roi > 0:
        logger.warning(f"  ⚠️  ROI simulado: {roi:+.2f}% (positivo pero bajo — afina el umbral EV)")
    else:
        logger.warning(f"  ❌ ROI simulado: {roi:+.2f}% (negativo — ajusta features o threshold)")

    logger.info("=" * 60)
    logger.info("  Modelo guardado en: models/ensemble_latest.pkl")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
