import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from datetime import timezone

from ml.data_loader import load_all_history
from ml.predictor import (
    get_all_odds_map, get_upcoming_fixtures, load_latest,
    OUTCOME_KEYS, OUTCOME_LABELS, compute_ev, generate_reasoning
)
from ml.feature_engineer import build_match_features

df_history = load_all_history()
fixtures = get_upcoming_fixtures("football")
odds_cache = get_all_odds_map()
model_data = load_latest()
models = model_data["models"]
feat_cols = model_data["feat_cols"]
now_utc = pd.Timestamp.now(tz=timezone.utc)

# Casa de referencia principal
TARGET_HOUSES = ["Pinnacle", "1xBet", "Betfair"]
found = []

for fix in fixtures:
    local     = fix.get("equipo_local", "")
    visitante = fix.get("equipo_visitante", "")
    pid       = fix.get("id", "")
    liga      = fix.get("liga", "")
    
    match_odds = odds_cache.get(pid, [])
    # Filtrar por casas de alta liquidez
    pref_rows = [r for r in match_odds if any(h.lower() in (r.get("bookmaker") or "").lower() for h in TARGET_HOUSES)]
    if not pref_rows:
        continue
        
    h_odds, d_odds, a_odds = 0.0, 0.0, 0.0
    book_used = ""
    ht_clean = local.lower().strip()
    at_clean = visitante.lower().strip()
    
    for r in pref_rows:
        if r.get("mercado") != "h2h":
            continue
        oc = (r.get("outcome") or "").lower().strip()
        c = float(r.get("cuota_actual") or 0)
        book_used = r.get("bookmaker")
        if oc in ["draw", "empate"]:
            d_odds = max(d_odds, c)
        elif oc == ht_clean or oc in ht_clean or ht_clean in oc:
            h_odds = max(h_odds, c)
        elif oc == at_clean or oc in at_clean or at_clean in oc:
            a_odds = max(a_odds, c)
            
    try:
        dummy = pd.Series({"HomeTeam": local, "AwayTeam": visitante, "Date": now_utc, "FTR": "H"})
        feat = build_match_features(dummy, df_history)
        X = np.array([[feat.get(c, 0.0) for c in feat_cols]])
        proba = np.mean([m.predict_proba(X)[0] for m in models.values()], axis=0)
    except Exception:
        continue
        
    odds_dict = {"H": h_odds, "D": d_odds, "A": a_odds}
    for idx, key in OUTCOME_KEYS.items():
        c = odds_dict[key]
        p = float(proba[idx])
        # Rango óptimo de efectividad y alta probabilidad
        if 1.45 <= c <= 2.35 and p >= 0.44:
            ev = compute_ev(p, c)
            if ev > 0.02:
                stats_l, ctx_l = generate_reasoning(key, feat, local, visitante)
                found.append({
                    "liga": liga,
                    "partido": f"{local} vs {visitante}",
                    "pick": OUTCOME_LABELS[idx],
                    "cuota": c,
                    "casa": book_used,
                    "prob": p,
                    "ev": ev,
                    "stats": stats_l,
                    "ctx": ctx_l
                })

print(f"Total picks sólidos encontrados: {len(found)}")
for f in found[:5]:
    print("---")
    print(f"{f['liga']} | {f['partido']}")
    print(f"👉 {f['pick']} | Cuota {f['cuota']:.2f} ({f['casa']}) | Prob: {f['prob']*100:.1f}% | EV: +{f['ev']*100:.1f}%")
    print(f"📊 {f['stats']}")
    print(f"🎯 {f['ctx']}")
