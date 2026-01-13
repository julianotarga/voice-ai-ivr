-- ============================================
-- Migration 001: v_voice_ai_providers
-- Voice AI IVR - Provedores de IA Multi-Provider
-- 
-- ⚠️ MULTI-TENANT: domain_uuid é OBRIGATÓRIO
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- Criar tabela
CREATE TABLE IF NOT EXISTS v_voice_ai_providers (
    voice_ai_provider_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid) ON DELETE CASCADE,
    
    -- Tipo e Provider
    provider_type VARCHAR(20) NOT NULL,
    provider_name VARCHAR(50) NOT NULL,
    display_name VARCHAR(100),
    
    -- Configuração (JSON flexível)
    config JSONB NOT NULL DEFAULT '{}',
    
    -- Controle
    is_default BOOLEAN DEFAULT FALSE,
    is_enabled BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 0,
    
    -- Limites e custos
    rate_limit_rpm INTEGER,
    cost_per_unit DECIMAL(10,6),
    cost_unit VARCHAR(20),
    
    -- Timestamps
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    update_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Constraints
    UNIQUE(domain_uuid, provider_type, provider_name)
);

-- Adicionar colunas se não existirem
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_ai_providers' AND column_name = 'display_name') 
    THEN
        ALTER TABLE v_voice_ai_providers ADD COLUMN display_name VARCHAR(100);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_ai_providers' AND column_name = 'is_enabled') 
    THEN
        ALTER TABLE v_voice_ai_providers ADD COLUMN is_enabled BOOLEAN DEFAULT TRUE;
    END IF;
END $$;

-- Drop constraints antigas se existirem e criar novas
DO $$ BEGIN
    -- Drop constraint antiga de tipo
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_provider_type') THEN
        ALTER TABLE v_voice_ai_providers DROP CONSTRAINT chk_provider_type;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'v_voice_ai_providers_provider_type_check') THEN
        ALTER TABLE v_voice_ai_providers DROP CONSTRAINT v_voice_ai_providers_provider_type_check;
    END IF;
    
    -- Criar constraint de tipo incluindo 'realtime'
    ALTER TABLE v_voice_ai_providers ADD CONSTRAINT chk_provider_type 
        CHECK (provider_type IN ('stt', 'tts', 'llm', 'embeddings', 'realtime'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Índices
CREATE INDEX IF NOT EXISTS idx_voice_ai_providers_domain 
    ON v_voice_ai_providers(domain_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_ai_providers_type_enabled 
    ON v_voice_ai_providers(provider_type, is_enabled, priority);
CREATE INDEX IF NOT EXISTS idx_voice_ai_providers_default 
    ON v_voice_ai_providers(domain_uuid, provider_type) 
    WHERE is_default = TRUE;

-- Comentários
COMMENT ON TABLE v_voice_ai_providers IS 'Configuração de provedores de IA por tenant (STT, TTS, LLM, Embeddings, Realtime)';
COMMENT ON COLUMN v_voice_ai_providers.domain_uuid IS 'OBRIGATÓRIO: UUID do domínio para isolamento multi-tenant';
COMMENT ON COLUMN v_voice_ai_providers.config IS 'Configuração JSON do provider (API keys, modelos, etc)';
