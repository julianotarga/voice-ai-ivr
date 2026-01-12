-- Migration 008: Add realtime fields to voice tables
-- 
-- Referências:
-- - .context/docs/architecture.md: Database Schema (processing_mode, realtime_provider_uuid)
-- - openspec/changes/voice-ai-realtime/proposal.md: Coexistência v1/v2
--
-- IDEMPOTENTE: Pode ser executada múltiplas vezes sem erro

-- Add processing_mode to secretaries
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'processing_mode'
    ) THEN
        ALTER TABLE v_voice_secretaries 
        ADD COLUMN processing_mode VARCHAR(20) DEFAULT 'turn_based'
        CHECK (processing_mode IN ('turn_based', 'realtime', 'auto'));
        
        COMMENT ON COLUMN v_voice_secretaries.processing_mode IS 
            'Modo de processamento: turn_based (v1), realtime (v2), auto (escolha automática)';
    END IF;
END $$;

-- Add realtime_provider_uuid to secretaries
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'realtime_provider_uuid'
    ) THEN
        ALTER TABLE v_voice_secretaries 
        ADD COLUMN realtime_provider_uuid UUID 
        REFERENCES v_voice_ai_providers(provider_uuid);
        
        COMMENT ON COLUMN v_voice_secretaries.realtime_provider_uuid IS 
            'Provider para modo realtime (OpenAI Realtime, ElevenLabs, etc)';
    END IF;
END $$;

-- Add 'realtime' as valid provider_type
DO $$
BEGIN
    -- Check current constraint
    IF EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'v_voice_ai_providers_provider_type_check'
    ) THEN
        -- Drop old constraint
        ALTER TABLE v_voice_ai_providers 
        DROP CONSTRAINT v_voice_ai_providers_provider_type_check;
    END IF;
    
    -- Add new constraint with 'realtime' type
    ALTER TABLE v_voice_ai_providers 
    ADD CONSTRAINT v_voice_ai_providers_provider_type_check 
    CHECK (provider_type IN ('stt', 'tts', 'llm', 'embeddings', 'realtime'));
END $$;

-- Add is_enabled field if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_ai_providers' 
        AND column_name = 'is_enabled'
    ) THEN
        ALTER TABLE v_voice_ai_providers 
        ADD COLUMN is_enabled BOOLEAN DEFAULT true;
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'is_enabled'
    ) THEN
        ALTER TABLE v_voice_secretaries 
        ADD COLUMN is_enabled BOOLEAN DEFAULT true;
    END IF;
END $$;

-- Add processing_mode to conversations for tracking
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_conversations' 
        AND column_name = 'processing_mode'
    ) THEN
        ALTER TABLE v_voice_conversations 
        ADD COLUMN processing_mode VARCHAR(20) DEFAULT 'turn_based';
    END IF;
END $$;

-- Create index for realtime queries
CREATE INDEX IF NOT EXISTS idx_secretaries_realtime 
ON v_voice_secretaries(domain_uuid, processing_mode) 
WHERE processing_mode IN ('realtime', 'auto') AND is_enabled = true;

CREATE INDEX IF NOT EXISTS idx_providers_realtime 
ON v_voice_ai_providers(domain_uuid, provider_type) 
WHERE provider_type = 'realtime' AND is_enabled = true;

-- Insert default OpenAI Realtime provider for existing domains (optional)
-- This is commented out - run manually if needed
/*
INSERT INTO v_voice_ai_providers (
    provider_uuid,
    domain_uuid,
    provider_type,
    provider_name,
    config,
    is_default,
    is_enabled,
    created_at,
    updated_at
)
SELECT 
    gen_random_uuid(),
    d.domain_uuid,
    'realtime',
    'openai',
    '{"api_key": "", "model": "gpt-4o-realtime-preview"}'::jsonb,
    true,
    true,
    NOW(),
    NOW()
FROM v_domains d
WHERE NOT EXISTS (
    SELECT 1 FROM v_voice_ai_providers p
    WHERE p.domain_uuid = d.domain_uuid
    AND p.provider_type = 'realtime'
);
*/

COMMENT ON TABLE v_voice_secretaries IS 
    'Secretárias virtuais com suporte a turn_based (v1) e realtime (v2)';
