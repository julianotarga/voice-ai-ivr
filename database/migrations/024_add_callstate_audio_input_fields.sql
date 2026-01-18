-- Migration: Add CallState, Input Normalization, and Silence Fallback fields
-- Version: 024
-- Description: Configurações por secretária para state machine, normalização e fallback de silêncio
--
-- IDEMPOTENTE: Usa IF NOT EXISTS

-- =====================================================
-- Input Audio Normalization
-- =====================================================
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_normalize_enabled BOOLEAN DEFAULT false;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_target_rms INTEGER DEFAULT 2000;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_min_rms INTEGER DEFAULT 300;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_max_gain DECIMAL(4,2) DEFAULT 3.0;

-- =====================================================
-- Call State Logging / Metrics
-- =====================================================
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS call_state_log_enabled BOOLEAN DEFAULT true;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS call_state_metrics_enabled BOOLEAN DEFAULT true;

-- =====================================================
-- Silence Fallback (State Machine)
-- =====================================================
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_enabled BOOLEAN DEFAULT false;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_seconds INTEGER DEFAULT 10;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_action VARCHAR(20) DEFAULT 'reprompt';

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_prompt TEXT;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_max_retries INTEGER DEFAULT 2;

-- =====================================================
-- Comentários
-- =====================================================
COMMENT ON COLUMN v_voice_secretaries.input_normalize_enabled IS 'Habilita normalização de áudio de entrada (ganho limitado).';
COMMENT ON COLUMN v_voice_secretaries.input_target_rms IS 'RMS alvo para normalização do áudio de entrada.';
COMMENT ON COLUMN v_voice_secretaries.input_min_rms IS 'RMS mínimo para aplicar normalização.';
COMMENT ON COLUMN v_voice_secretaries.input_max_gain IS 'Ganho máximo permitido para normalização.';

COMMENT ON COLUMN v_voice_secretaries.call_state_log_enabled IS 'Habilita logs de transição de CallState.';
COMMENT ON COLUMN v_voice_secretaries.call_state_metrics_enabled IS 'Habilita métricas de transição de CallState.';

COMMENT ON COLUMN v_voice_secretaries.silence_fallback_enabled IS 'Habilita fallback de silêncio no state machine.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_seconds IS 'Tempo de silêncio (segundos) para acionar fallback.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_action IS 'Ação no fallback de silêncio: reprompt ou hangup.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_prompt IS 'Mensagem para reprompt no fallback de silêncio.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_max_retries IS 'Número máximo de reprompts antes de encerrar.';

-- =====================================================
-- Valores padrão para registros existentes
-- =====================================================
UPDATE v_voice_secretaries
SET input_normalize_enabled = false
WHERE input_normalize_enabled IS NULL;

UPDATE v_voice_secretaries
SET input_target_rms = 2000
WHERE input_target_rms IS NULL;

UPDATE v_voice_secretaries
SET input_min_rms = 300
WHERE input_min_rms IS NULL;

UPDATE v_voice_secretaries
SET input_max_gain = 3.0
WHERE input_max_gain IS NULL;

UPDATE v_voice_secretaries
SET call_state_log_enabled = true
WHERE call_state_log_enabled IS NULL;

UPDATE v_voice_secretaries
SET call_state_metrics_enabled = true
WHERE call_state_metrics_enabled IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_enabled = false
WHERE silence_fallback_enabled IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_seconds = 10
WHERE silence_fallback_seconds IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_action = 'reprompt'
WHERE silence_fallback_action IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_max_retries = 2
WHERE silence_fallback_max_retries IS NULL;
