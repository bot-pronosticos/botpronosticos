# 🚀 Guía de Despliegue en la Nube (100% Gratuito)

Tu bot ya cuenta con:
- **Botones interactivos en Telegram:** `[ ✅ Aposté a esta ]` y `[ ⏭️ No aposté ]` en cada pick.
- **Gestión selectiva de banca:** Solo actualiza tu bankroll si pulsaste que hiciste la apuesta.
- **Seguimiento completo:** Si no apostaste, igualmente responde en hilo mostrando si acertó o no, manteniendo tu saldo intacto.
- **Modelo Calibrado:** Brier Score 0.2016 y cuotas moderadas (1.45 - 2.45) de **Pinnacle / Bet365**.

---

## Opción 1: Railway (Recomendada — 3 Minutos)

Railway ofrece 500 horas de ejecución gratuita al mes y ejecuta tanto el bot interactivo como el scheduler en un solo servicio.

### Paso 1: Subir código a tu GitHub
Abre una terminal dentro de `C:\Users\Santiago\Desktop\bot-pronosticos` y ejecuta:
```bash
git init
git add .
git commit -m "feat: bot de pronosticos con botones y gestion de banca"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

### Paso 2: Conectar en Railway
1. Entra a [railway.app](https://railway.app/) e inicia sesión con GitHub.
2. Clic en **+ New Project** → **Deploy from GitHub repo**.
3. Selecciona tu repositorio `bot-pronosticos`.

### Paso 3: Pegar Variables de Entorno
En el panel del proyecto en Railway, entra a la pestaña **Variables** y agrega:

| Variable | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `9128575947:AAHg5bFa_keV9uc-0rgZ0gyZsxg8U28PBzM` |
| `TELEGRAM_CHAT_ID` | `5241095844` |
| `SUPABASE_URL` | `https://rvzmdpvjgactqokepxsv.supabase.co` |
| `SUPABASE_KEY` | *(tu anon key de Supabase)* |
| `ODDS_API_KEY` | `1cc8138c10617d6ee7375552a40b6535` |
| `RAPIDAPI_KEY` | `7aaa7822195e7c940c7359f7cbd08077` |
| `EV_THRESHOLD` | `0.04` |
| `KELLY_FRACTION` | `0.25` |
| `BANKROLL` | `100` |
| `FOOTBALL_LEAGUES` | `39,140,135,78,61,2` |

¡Listo! El servicio arrancará solo. Te llegará una notificación a Telegram diciendo:
`🚀 Sistema de Pronósticos Iniciado en la Nube — 24/7 Activo`.

---

## 📱 Cómo interactúas con tu Bot desde el Celular

1. **Cuando llegue un pronóstico:**
   - Verás la tarjeta minimalista con el argumento deportivo.
   - Debajo tendrás dos botones:
     - `[ ✅ Aposté a esta ]` → Si la hiciste, púlsalo. Se marcará como registrada.
     - `[ ⏭️ No aposté ]` → Si decidiste pasar, púlsalo.
2. **Cuando termine el partido:**
   - El bot responderá en hilo a ese mismo mensaje:
     - Si la apostaste: `✅ ACERTADA (+X.XX$) | Tu banca actual: $YY.YY` (o pérdida si no se dio).
     - Si no la apostaste: `✅ ACERTADA (No apostada) | Tu banca no varió: $YY.YY`.
3. **Comandos en cualquier momento:**
   - `/picks` — Consulta los mejores pronósticos del día al instante.
   - `/bankroll` — Muestra tu saldo actual y la gestión de riesgo.
   - `/stats` — Muestra el resumen de aciertos acumulados.
