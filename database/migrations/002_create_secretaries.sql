-- Migration 002: v_voice_secretaries
-- IDEMPOTENTE

CREATE TABLE IF NOT EXISTS v_voice_secretaries (
    voice_secretary_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid) ON DELETE CASCADE,
    secretary_name VARCHAR(100) NOT NULL,
    company_name VARCHAR(200),
    extension VARCHAR(20),
    personality_prompt TEXT NOT NULL DEFAULT '',
    greeting_message TEXT,
    farewell_message TEXT,
    fallback_message TEXT DEFAULT 'Desculpe, nao entendi.',
    transfer_message TEXT DEFAULT 'Transferindo...',
    stt_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    tts_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    llm_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    embeddings_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    tts_voice_id VARCHAR(100),
    tts_speed DECIMAL(3,2) DEFAULT 1.0,
    language VARCHAR(10) DEFAULT 'pt-BR',
    llm_model_override VARCHAR(100),
    llm_temperature DECIMAL(3,2) DEFAULT 0.7,
    llm_max_tokens INTEGER DEFAULT 500,
    max_turns INTEGER DEFAULT 20,
    silence_timeout_ms INTEGER DEFAULT 3000,
    max_recording_seconds INTEGER DEFAULT 30,
    transfer_extension VARCHAR(20),
    transfer_on_failure BOOLEAN DEFAULT TRUE,
    create_ticket_on_transfer BOOLEAN DEFAULT FALSE,
    omniplay_webhook_url VARCHAR(500),
    omniplay_queue_id VARCHAR(50),
    is_enabled BOOLEAN DEFAULT TRUE,
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    update_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'processing_mode') 
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN processing_mode VARCHAR(20) DEFAULT 'turn_based';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'realtime_provider_uuid') 
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN realtime_provider_uuid UUID 
            REFERENCES v_voice_ai_providers(voice_ai_provider_uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'vad_threshold') 
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN vad_threshold DECIMAL(3,2) DEFAULT 0.5;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_voice_secretaries_domain ON v_voice_secretaries(domain_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_secretaries_enabled ON v_voice_secretaries(domain_uuid, is_enabled);
CREATE INDEX IF NOT EXISTS idx_voice_secretaries_extension ON v_voice_secretaries(domain_uuid, extension);

COMMENT ON TABLE v_voice_secretaries IS 'Secretarias virtuais com IA';
