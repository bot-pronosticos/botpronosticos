# 🤖 Bot de Pronósticos Deportivos

Bot de predicción deportiva con ML, EV filter y Kelly Criterion.
Output por Telegram. 100% gratuito. Cloud-hosted en Railway.

## Estructura

```
bot-pronosticos/
├── config/          # Variables de entorno y configuración
├── harvesters/      # Scrapers y clientes de API
├── ml/              # Feature engineering, modelos, backtesting
├── bot/             # Telegram bot y formatter de picks
├── scripts/         # Cron jobs y utilidades
├── data/            # Datos locales / caché
├── models/          # Modelos entrenados serializados (.pkl)
└── logs/            # Logs del sistema
```

## Fases
- [x] Fase 1 — Infraestructura & Data Pipeline
- [ ] Fase 2 — Modelo Base & Backtesting
- [ ] Fase 3 — Telegram Bot & EV Filter
- [ ] Fase 4 — Feedback Loop & Auto-retrain
- [ ] Fase 5 — Deploy Cloud & Monitoreo
- [ ] Fase 6 — Optimización Continua

## Setup rápido
```bash
pip install -r requirements.txt
cp config/.env.example config/.env
# Rellena las keys en config/.env
python scripts/setup_db.py
python scripts/run_pipeline.py
```
