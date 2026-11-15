-- =============================================
-- SCHEMA: Bot de Pronósticos Deportivos
-- Ejecutar en: Supabase → SQL Editor → New Query
-- =============================================

-- ── 1. PARTIDOS ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS partidos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deporte         TEXT NOT NULL,                    -- 'football', 'basketball', 'americano'
    liga            TEXT NOT NULL,                    -- 'Premier League', 'NBA', etc.
    liga_id         TEXT,                             -- ID de la API source
    equipo_local    TEXT NOT NULL,
    equipo_visitante TEXT NOT NULL,
    kickoff         TIMESTAMPTZ NOT NULL,
    estado          TEXT DEFAULT 'pendiente',         -- 'pendiente', 'en_vivo', 'finalizado'
    goles_local     INTEGER,
    goles_visitante INTEGER,
    source_id       TEXT UNIQUE,                      -- ID del partido en la API de origen
    creado_en       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_partidos_kickoff ON partidos(kickoff);
CREATE INDEX IF NOT EXISTS idx_partidos_estado  ON partidos(estado);

-- ── 2. ODDS ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS odds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partido_id      UUID REFERENCES partidos(id) ON DELETE CASCADE,
    bookmaker       TEXT NOT NULL,
    mercado         TEXT NOT NULL,                    -- 'h2h', 'totals', 'spreads', 'btts'
    outcome         TEXT NOT NULL,                    -- 'home', 'away', 'draw', 'over', 'under'
    cuota_apertura  NUMERIC(6,3),
    cuota_actual    NUMERIC(6,3) NOT NULL,
    actualizado_en  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_odds_partido ON odds(partido_id);
CREATE INDEX IF NOT EXISTS idx_odds_mercado ON odds(mercado);

-- ── 3. FEATURES ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS features (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partido_id      UUID REFERENCES partidos(id) ON DELETE CASCADE UNIQUE,
    vector          JSONB NOT NULL,                   -- vector completo de features
    calculado_en    TIMESTAMPTZ DEFAULT NOW()
);

-- ── 4. PICKS ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS picks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partido_id          UUID REFERENCES partidos(id) ON DELETE CASCADE,
    mercado             TEXT NOT NULL,
    outcome             TEXT NOT NULL,                -- qué lado predijo el modelo
    prob_modelo         NUMERIC(5,4) NOT NULL,        -- ej: 0.6420
    prob_implicita      NUMERIC(5,4) NOT NULL,        -- 1 / cuota
    ev                  NUMERIC(6,4) NOT NULL,        -- expected value
    cuota               NUMERIC(6,3) NOT NULL,
    bookmaker           TEXT NOT NULL,
    kelly_fraccion      NUMERIC(5,4),                 -- fracción recomendada del bankroll
    enviado_en          TIMESTAMPTZ DEFAULT NOW(),
    resultado_correcto  BOOLEAN,                      -- NULL hasta que se verifique
    ganancia_unidades   NUMERIC(8,4),                 -- positivo=ganó, negativo=perdió
    verificado_en       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_picks_partido    ON picks(partido_id);
CREATE INDEX IF NOT EXISTS idx_picks_enviado_en ON picks(enviado_en);

-- ── 5. MODELO_METRICAS ────────────────────────────────────
CREATE TABLE IF NOT EXISTS modelo_metricas (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version             TEXT NOT NULL,
    deporte             TEXT NOT NULL,
    brier_score         NUMERIC(6,4),
    roi_simulado        NUMERIC(6,4),
    accuracy            NUMERIC(5,4),
    picks_evaluados     INTEGER,
    picks_correctos     INTEGER,
    fecha               TIMESTAMPTZ DEFAULT NOW()
);

-- ── 6. RATE_LIMIT_TRACKER ────────────────────────────────
-- Lleva la cuenta de requests a cada API para no quemar el free tier
CREATE TABLE IF NOT EXISTS api_usage (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_name    TEXT NOT NULL,                        -- 'odds_api', 'api_football', 'balldontlie'
    fecha       DATE NOT NULL DEFAULT CURRENT_DATE,
    requests    INTEGER DEFAULT 0,
    limite      INTEGER NOT NULL,                     -- límite diario/mensual
    UNIQUE(api_name, fecha)
);

-- Inserta los límites conocidos
INSERT INTO api_usage (api_name, fecha, requests, limite)
VALUES
    ('odds_api',      CURRENT_DATE, 0, 500),
    ('api_football',  CURRENT_DATE, 0, 100),
    ('balldontlie',   CURRENT_DATE, 0, 9999)
ON CONFLICT (api_name, fecha) DO NOTHING;

-- ── VISTAS ÚTILES ─────────────────────────────────────────

-- Rendimiento histórico de picks
CREATE OR REPLACE VIEW vista_rendimiento AS
SELECT
    DATE_TRUNC('week', enviado_en) AS semana,
    COUNT(*)                        AS total_picks,
    SUM(CASE WHEN resultado_correcto THEN 1 ELSE 0 END) AS correctos,
    ROUND(AVG(CASE WHEN resultado_correcto IS NOT NULL
              THEN CASE WHEN resultado_correcto THEN 1.0 ELSE 0.0 END
              END) * 100, 2)        AS pct_acierto,
    ROUND(SUM(COALESCE(ganancia_unidades, 0)), 4) AS roi_unidades,
    ROUND(AVG(ev) * 100, 2)         AS ev_promedio_pct
FROM picks
GROUP BY semana
ORDER BY semana DESC;

-- Picks pendientes de verificar
CREATE OR REPLACE VIEW vista_picks_pendientes AS
SELECT
    p.id,
    pa.equipo_local,
    pa.equipo_visitante,
    pa.kickoff,
    p.mercado,
    p.outcome,
    p.cuota,
    p.ev,
    p.enviado_en
FROM picks p
JOIN partidos pa ON pa.id = p.partido_id
WHERE p.resultado_correcto IS NULL
  AND pa.kickoff < NOW()
ORDER BY pa.kickoff DESC;
