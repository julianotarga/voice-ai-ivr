-- ============================================================
-- Migration: Create OmniPlay Integration Tables
-- 
-- Tabelas para integração FusionPBX ↔ OmniPlay:
-- 1. v_voice_omniplay_settings - Configurações por domínio
-- 2. v_voice_omniplay_cache - Cache de dados da API
-- ============================================================

-- Tabela de configurações OmniPlay por domínio
CREATE TABLE IF NOT EXISTS v_voice_omniplay_settings (
    omniplay_setting_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL UNIQUE,
    omniplay_api_url VARCHAR(500),        -- https://api.omniplay.com.br
    omniplay_api_token VARCHAR(255),      -- Token gerado no OmniPlay
    omniplay_company_id INTEGER,          -- ID da empresa no OmniPlay
    auto_sync_enabled BOOLEAN DEFAULT true,
    sync_interval_minutes INTEGER DEFAULT 5,
    last_sync_at TIMESTAMP WITH TIME ZONE,
    last_sync_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Constraint de unicidade para evitar duplicatas
    CONSTRAINT fk_omniplay_settings_domain 
        FOREIGN KEY (domain_uuid) 
        REFERENCES v_domains(domain_uuid) 
        ON DELETE CASCADE
);

COMMENT ON TABLE v_voice_omniplay_settings IS 'Configurações de integração OmniPlay por domínio (tenant)';
COMMENT ON COLUMN v_voice_omniplay_settings.omniplay_api_url IS 'URL base da API do OmniPlay (ex: https://api.omniplay.com.br)';
COMMENT ON COLUMN v_voice_omniplay_settings.omniplay_api_token IS 'Token de autenticação gerado no OmniPlay (voice_xxxxxx)';
COMMENT ON COLUMN v_voice_omniplay_settings.omniplay_company_id IS 'ID da empresa no OmniPlay para referência';

-- Índice para busca rápida por domínio
CREATE INDEX IF NOT EXISTS idx_omniplay_settings_domain 
    ON v_voice_omniplay_settings(domain_uuid);

-- Tabela de cache de dados do OmniPlay
CREATE TABLE IF NOT EXISTS v_voice_omniplay_cache (
    cache_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL,
    cache_key VARCHAR(100) NOT NULL,       -- omniplay_{domain_uuid}_queues, etc
    cache_data JSONB,                       -- Dados cacheados
    cached_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Constraint de unicidade para evitar duplicatas
    CONSTRAINT uq_omniplay_cache_key 
        UNIQUE (domain_uuid, cache_key),
    
    CONSTRAINT fk_omniplay_cache_domain 
        FOREIGN KEY (domain_uuid) 
        REFERENCES v_domains(domain_uuid) 
        ON DELETE CASCADE
);

COMMENT ON TABLE v_voice_omniplay_cache IS 'Cache local de dados buscados da API OmniPlay';
COMMENT ON COLUMN v_voice_omniplay_cache.cache_key IS 'Chave do cache (ex: omniplay_uuid_queues, omniplay_uuid_users)';
COMMENT ON COLUMN v_voice_omniplay_cache.cache_data IS 'Dados cacheados em formato JSON';

-- Índice para busca e limpeza de cache expirado
CREATE INDEX IF NOT EXISTS idx_omniplay_cache_domain_key 
    ON v_voice_omniplay_cache(domain_uuid, cache_key);

CREATE INDEX IF NOT EXISTS idx_omniplay_cache_expire 
    ON v_voice_omniplay_cache(cached_at);

-- Função para limpar cache expirado automaticamente (chamada por cron)
CREATE OR REPLACE FUNCTION voice_omniplay_cache_cleanup()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Remove cache com mais de 1 hora
    DELETE FROM v_voice_omniplay_cache 
    WHERE cached_at < NOW() - INTERVAL '1 hour';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION voice_omniplay_cache_cleanup() IS 'Limpa cache expirado da integração OmniPlay';

-- Trigger para atualizar updated_at automaticamente
CREATE OR REPLACE FUNCTION update_omniplay_settings_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_omniplay_settings_updated ON v_voice_omniplay_settings;
CREATE TRIGGER tr_omniplay_settings_updated
    BEFORE UPDATE ON v_voice_omniplay_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_omniplay_settings_timestamp();

SELECT 'Migration 017_create_omniplay_integration_tables completed successfully' AS status;
