-- =============================================
-- Agregar columna telegram_message_id para respuestas en hilo
-- Ejecutar en Supabase → SQL Editor → New query → Run
-- =============================================

ALTER TABLE picks 
ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;
