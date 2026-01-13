-- Migration 008: Campos extras para realtime
-- IDEMPOTENTE

-- Adicionar realtime ao provider_type
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_provider_type') THEN
        ALTER TABLE v_voice_ai_providers DROP CONSTRAINT chk_provider_type;
    END IF;
    ALTER TABLE v_voice_ai_providers ADD CONSTRAINT chk_provider_type 
        CHECK (provider_type IN ('stt', 'tts', 'llm', 'embeddings', 'realtime'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Indices para realtime
CREATE INDEX IF NOT EXISTS idx_providers_realtime 
    ON v_voice_ai_providers(domain_uuid, provider_type) 
    WHERE provider_type = 'realtime' AND is_enabled = true;

CREATE INDEX IF NOT EXISTS idx_secretaries_realtime 
    ON v_voice_secretaries(domain_uuid, processing_mode);

COMMENT ON TABLE v_voice_secretaries IS 'Secretarias virtuais com suporte turn_based (v1) e realtime (v2)';
