-- Migration: Add Audio Configuration fields to v_voice_secretaries
-- Permite configurar buffer de áudio e jitter por secretária para evitar picotamento

-- =============================================================================
-- AUDIO BUFFER CONFIGURATION
-- =============================================================================

-- Warmup chunks (número de chunks de 20ms antes de iniciar playback)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'audio_warmup_chunks'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN audio_warmup_chunks INTEGER DEFAULT 15;
        COMMENT ON COLUMN v_voice_secretaries.audio_warmup_chunks IS 'Number of 20ms chunks to buffer before playback (default: 15 = 300ms)';
    END IF;
END $$;

-- Warmup milliseconds (buffer de warmup em milissegundos)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'audio_warmup_ms'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN audio_warmup_ms INTEGER DEFAULT 400;
        COMMENT ON COLUMN v_voice_secretaries.audio_warmup_ms IS 'Audio warmup buffer in milliseconds (default: 400ms)';
    END IF;
END $$;

-- =============================================================================
-- JITTER BUFFER CONFIGURATION (FreeSWITCH)
-- =============================================================================

-- Jitter buffer min (mínimo em ms)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'jitter_buffer_min'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN jitter_buffer_min INTEGER DEFAULT 100;
        COMMENT ON COLUMN v_voice_secretaries.jitter_buffer_min IS 'FreeSWITCH jitter buffer minimum in ms (default: 100)';
    END IF;
END $$;

-- Jitter buffer max (máximo em ms)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'jitter_buffer_max'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN jitter_buffer_max INTEGER DEFAULT 300;
        COMMENT ON COLUMN v_voice_secretaries.jitter_buffer_max IS 'FreeSWITCH jitter buffer maximum in ms (default: 300)';
    END IF;
END $$;

-- Jitter buffer step (passo de ajuste em ms)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'jitter_buffer_step'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN jitter_buffer_step INTEGER DEFAULT 40;
        COMMENT ON COLUMN v_voice_secretaries.jitter_buffer_step IS 'FreeSWITCH jitter buffer step in ms (default: 40)';
    END IF;
END $$;

-- =============================================================================
-- STREAM BUFFER CONFIGURATION (mod_audio_stream)
-- =============================================================================

-- Stream buffer size (tamanho do buffer do mod_audio_stream em samples)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'stream_buffer_size'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN stream_buffer_size INTEGER DEFAULT 320;
        COMMENT ON COLUMN v_voice_secretaries.stream_buffer_size IS 'mod_audio_stream buffer size in samples (default: 320 = 20ms @ 16kHz)';
    END IF;
END $$;

-- =============================================================================
-- ADAPTIVE SETTINGS
-- =============================================================================

-- Adaptive warmup enabled (ajusta automaticamente o warmup)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'audio_adaptive_warmup'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN audio_adaptive_warmup BOOLEAN DEFAULT true;
        COMMENT ON COLUMN v_voice_secretaries.audio_adaptive_warmup IS 'Enable adaptive warmup adjustment (default: true)';
    END IF;
END $$;

SELECT 'Migration 010_add_audio_config_fields completed successfully' AS status;
