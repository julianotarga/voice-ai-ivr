-- Migration 006: Adicionar colunas extras a v_voice_messages
-- IDEMPOTENTE: Apenas adiciona colunas se nao existirem

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'stt_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN stt_provider VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'stt_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN stt_latency_ms INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'tts_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN tts_provider VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'tts_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN tts_latency_ms INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'llm_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN llm_provider VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'llm_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN llm_latency_ms INTEGER;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_voice_messages_role ON v_voice_messages(role);
CREATE INDEX IF NOT EXISTS idx_voice_messages_intent ON v_voice_messages(detected_intent) WHERE detected_intent IS NOT NULL;

COMMENT ON TABLE v_voice_messages IS 'Mensagens individuais das conversas';
