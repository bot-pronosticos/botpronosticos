"""
ml/trainer.py
Entrena el ensemble: XGBoost + Logistic Regression + Random Forest.
Calibra probabilidades con Platt Scaling (CalibratedClassifierCV).
Guarda el modelo serializado en models/
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from loguru import logger
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import brier_score_loss, accuracy_score
from xgboost import XGBClassifier

from config.settings import MODELS_DIR


FEATURE_COLS = [
    "h_gf_avg","h_ga_avg","h_gd_avg","h_win_rate","h_draw_rate","h_form5",
    "h_gf_last5","h_ga_last5","h_sh_for","h_sh_ag",
    "a_gf_avg","a_ga_avg","a_gd_avg","a_win_rate","a_draw_rate","a_form5",
    "a_gf_last5","a_ga_last5","a_sh_for","a_sh_ag",
    "gf_diff","ga_diff","win_diff","form_diff","gd_diff",
    "h2h_home_win_rate","h2h_n","home_advantage",
]


def temporal_split(X: pd.DataFrame, y: pd.Series,
                   test_ratio: float = 0.20) -> tuple:
    """
    Split temporal — el test siempre es el futuro del train.
    NUNCA random split en datos de deportes (hay leakage de forma/racha).
    """
    n = len(X)
    split_idx = int(n * (1 - test_ratio))

    X_train = X.iloc[:split_idx]
    X_test  = X.iloc[split_idx:]
    y_train = y.iloc[:split_idx]
    y_test  = y.iloc[split_idx:]

    dates = X["_date"] if "_date" in X.columns else pd.Series(index=X.index)

    logger.info(f"Train: {len(X_train)} partidos")
    logger.info(f"Test:  {len(X_test)}  partidos")
    if "_date" in X.columns:
        logger.info(f"Train hasta: {X_train['_date'].max()}")
        logger.info(f"Test desde:  {X_test['_date'].min()}")

    return X_train, X_test, y_train, y_test


def get_feature_matrix(X: pd.DataFrame) -> np.ndarray:
    """Extrae solo las columnas de features (sin metadata)."""
    available = [c for c in FEATURE_COLS if c in X.columns]
    return X[available].fillna(0).values, available


def build_ensemble() -> dict:
    """Construye los 3 modelos del ensemble."""
    xgb = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )

    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=6,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=-1,
    )

    lr = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            C=0.5,
            max_iter=1000,
            random_state=42,
        )),
    ])

    return {"xgb": xgb, "rf": rf, "lr": lr}


def train(X: pd.DataFrame, y: pd.Series,
          version: str = None) -> dict:
    """
    Entrena el ensemble completo con split temporal.
    Retorna dict con modelos, métricas y metadata.
    """
    version = version or datetime.now().strftime("%Y%m%d_%H%M")
    logger.info(f"=== ENTRENAMIENTO v{version} ===")

    # Split temporal
    X_train, X_test, y_train, y_test = temporal_split(X, y)

    X_tr, feat_cols = get_feature_matrix(X_train)
    X_te, _         = get_feature_matrix(X_test)
    y_tr = y_train.values
    y_te = y_test.values

    models    = build_ensemble()
    trained   = {}
    metrics   = {}

    for name, model in models.items():
        logger.info(f"Entrenando y calibrando {name}...")
        cal = CalibratedClassifierCV(model, cv=3, method="isotonic")
        cal.fit(X_tr, y_tr)

        proba_te = cal.predict_proba(X_te)
        preds_te = np.argmax(proba_te, axis=1)

        acc = accuracy_score(y_te, preds_te)
        # Brier Score multiclase — promedio de los 3 outcomes
        brier = np.mean([
            brier_score_loss((y_te == cls).astype(int), proba_te[:, cls])
            for cls in range(3)
        ])

        logger.info(f"  {name} — Accuracy: {acc:.3f} | Brier: {brier:.4f}")
        trained[name]  = cal
        metrics[name]  = {"accuracy": acc, "brier": brier}

    # Ensemble: promedio de probabilidades (soft voting)
    logger.info("Calculando métricas del ensemble...")
    ensemble_proba = np.mean([
        trained["xgb"].predict_proba(X_te),
        trained["rf"].predict_proba(X_te),
        trained["lr"].predict_proba(X_te),
    ], axis=0)

    ensemble_preds = np.argmax(ensemble_proba, axis=1)
    ens_acc    = accuracy_score(y_te, ensemble_preds)
    ens_brier  = np.mean([
        brier_score_loss((y_te == cls).astype(int), ensemble_proba[:, cls])
        for cls in range(3)
    ])

    logger.info(f"  ENSEMBLE — Accuracy: {ens_acc:.3f} | Brier: {ens_brier:.4f}")
    metrics["ensemble"] = {"accuracy": ens_acc, "brier": ens_brier}

    # Guardar modelos
    model_data = {
        "version":   version,
        "models":    trained,
        "feat_cols": feat_cols,
        "metrics":   metrics,
        "trained_at": datetime.now().isoformat(),
        "n_train":   len(X_train),
        "n_test":    len(X_test),
    }
    save_path = MODELS_DIR / f"ensemble_{version}.pkl"
    joblib.dump(model_data, save_path)
    logger.info(f"Modelo guardado: {save_path}")

    # También guardar como 'latest'
    latest_path = MODELS_DIR / "ensemble_latest.pkl"
    joblib.dump(model_data, latest_path)

    return model_data


def load_latest() -> dict | None:
    """Carga el modelo más reciente."""
    latest = MODELS_DIR / "ensemble_latest.pkl"
    if not latest.exists():
        logger.error("No hay modelo entrenado. Ejecuta trainer.py primero.")
        return None
    return joblib.load(latest)


if __name__ == "__main__":
    from ml.data_loader import load_all_history
    from ml.feature_engineer import build_dataset

    logger.info("Cargando datos históricos...")
    df = load_all_history()

    logger.info("Construyendo features...")
    X, y = build_dataset(df)

    logger.info("Entrenando ensemble...")
    result = train(X, y)

    logger.info("\n=== RESUMEN FINAL ===")
    for name, m in result["metrics"].items():
        logger.info(f"  {name:10s} — Acc: {m['accuracy']:.3f} | Brier: {m['brier']:.4f}")
