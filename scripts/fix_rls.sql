-- =============================================
-- FIX RLS: Habilitar operaciones con anon key
-- Supabase → SQL Editor → New query → Run
-- =============================================

ALTER TABLE partidos        ENABLE ROW LEVEL SECURITY;
ALTER TABLE odds            ENABLE ROW LEVEL SECURITY;
ALTER TABLE features        ENABLE ROW LEVEL SECURITY;
ALTER TABLE picks           ENABLE ROW LEVEL SECURITY;
ALTER TABLE modelo_metricas ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_usage       ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_all_partidos"        ON partidos;
DROP POLICY IF EXISTS "anon_all_odds"            ON odds;
DROP POLICY IF EXISTS "anon_all_features"        ON features;
DROP POLICY IF EXISTS "anon_all_picks"           ON picks;
DROP POLICY IF EXISTS "anon_all_modelo_metricas" ON modelo_metricas;
DROP POLICY IF EXISTS "anon_all_api_usage"       ON api_usage;

CREATE POLICY "anon_all_partidos"        ON partidos        FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all_odds"            ON odds            FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all_features"        ON features        FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all_picks"           ON picks           FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all_modelo_metricas" ON modelo_metricas FOR ALL TO anon USING (true) WITH CHECK (true);
CREATE POLICY "anon_all_api_usage"       ON api_usage       FOR ALL TO anon USING (true) WITH CHECK (true);
