-- =============================================
-- FIX constraints -- versión compatible PostgreSQL
-- Supabase → SQL Editor → New query → Run
-- =============================================

-- Fix tabla odds
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'odds_unique_key'
    ) THEN
        ALTER TABLE odds 
        ADD CONSTRAINT odds_unique_key 
        UNIQUE (partido_id, bookmaker, mercado, outcome);
    END IF;
END$$;

-- Fix tabla partidos
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'partidos_source_id_key'
    ) THEN
        ALTER TABLE partidos 
        ADD CONSTRAINT partidos_source_id_key 
        UNIQUE (source_id);
    END IF;
END$$;
