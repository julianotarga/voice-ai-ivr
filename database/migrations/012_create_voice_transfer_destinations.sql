-- ============================================================================
-- Migration: 012_create_voice_transfer_destinations.sql
-- Description: Cria tabela de destinos de transferência para handoff inteligente
-- Author: Claude AI + Juliano Targa
-- Created: 2026-01-16
-- Status: IDEMPOTENT - Seguro para rodar múltiplas vezes
-- ============================================================================

-- ============================================================================
-- TABELA PRINCIPAL: v_voice_transfer_destinations
-- Armazena destinos configuráveis para transferência de chamadas
-- ============================================================================

CREATE TABLE IF NOT EXISTS v_voice_transfer_destinations (
    -- Identificadores
    transfer_destination_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL,
    secretary_uuid UUID,
    
    -- Identificação por voz/texto (para o LLM entender)
    name VARCHAR(100) NOT NULL,
    -- Array de aliases para fuzzy matching: ["jeni", "jennifer", "financeiro", "boleto"]
    aliases JSONB DEFAULT '[]'::jsonb,
    
    -- Destino FreeSWITCH
    destination_type VARCHAR(20) NOT NULL DEFAULT 'extension',
    destination_number VARCHAR(50) NOT NULL,
    destination_context VARCHAR(50) DEFAULT 'default',
    
    -- Configurações de transfer
    ring_timeout_seconds INT DEFAULT 30,
    max_retries INT DEFAULT 1,
    retry_delay_seconds INT DEFAULT 5,
    
    -- Fallback quando não atende
    -- offer_ticket: Pergunta se quer deixar recado
    -- create_ticket: Cria ticket automaticamente
    -- voicemail: Transfere para voicemail
    -- return_agent: Volta ao agente IA
    -- hangup: Desliga
    fallback_action VARCHAR(30) DEFAULT 'offer_ticket',
    
    -- Metadados para contexto do agente
    department VARCHAR(100),
    role VARCHAR(100),
    description TEXT,
    -- Horário de funcionamento: {"start": "08:00", "end": "18:00", "days": [1,2,3,4,5], "timezone": "America/Sao_Paulo"}
    working_hours JSONB,
    
    -- Controle
    priority INT DEFAULT 100,
    is_enabled BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,
    
    -- Auditoria
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT chk_destination_type CHECK (destination_type IN (
        'extension',      -- Ramal individual
        'ring_group',     -- Grupo de toque
        'queue',          -- Fila de callcenter
        'external',       -- Número externo
        'voicemail'       -- Caixa postal
    )),
    CONSTRAINT chk_fallback_action CHECK (fallback_action IN (
        'offer_ticket',
        'create_ticket',
        'voicemail',
        'return_agent',
        'hangup'
    ))
);

-- Comentário da tabela
COMMENT ON TABLE v_voice_transfer_destinations IS 
    'Destinos de transferência configuráveis para o sistema de handoff inteligente de voz';

-- ============================================================================
-- ÍNDICES
-- ============================================================================

-- Índice para buscar destinos por domain
CREATE INDEX IF NOT EXISTS idx_vtd_domain 
    ON v_voice_transfer_destinations(domain_uuid);

-- Índice para buscar destinos por secretária
CREATE INDEX IF NOT EXISTS idx_vtd_secretary 
    ON v_voice_transfer_destinations(secretary_uuid);

-- Índice para destinos habilitados (mais usado)
CREATE INDEX IF NOT EXISTS idx_vtd_enabled 
    ON v_voice_transfer_destinations(domain_uuid, is_enabled) 
    WHERE is_enabled = true;

-- Índice para ordenação por prioridade
CREATE INDEX IF NOT EXISTS idx_vtd_priority 
    ON v_voice_transfer_destinations(domain_uuid, priority ASC) 
    WHERE is_enabled = true;

-- Índice único para garantir apenas um default por domain
-- Removemos e recriamos para garantir idempotência
DROP INDEX IF EXISTS idx_vtd_default_unique;
CREATE UNIQUE INDEX idx_vtd_default_unique 
    ON v_voice_transfer_destinations(domain_uuid) 
    WHERE is_default = true AND is_enabled = true;

-- ============================================================================
-- FOREIGN KEYS (condicionais - podem não existir em todos os ambientes)
-- ============================================================================

-- Adicionar FK para domain se a tabela existir
DO $$ 
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'v_domains') THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints 
            WHERE constraint_name = 'fk_vtd_domain' 
            AND table_name = 'v_voice_transfer_destinations'
        ) THEN
            ALTER TABLE v_voice_transfer_destinations 
            ADD CONSTRAINT fk_vtd_domain 
            FOREIGN KEY (domain_uuid) 
            REFERENCES v_domains(domain_uuid) 
            ON DELETE CASCADE;
        END IF;
    END IF;
END $$;

-- Adicionar FK para secretary se a tabela existir
DO $$ 
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'v_voice_secretaries') THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints 
            WHERE constraint_name = 'fk_vtd_secretary' 
            AND table_name = 'v_voice_transfer_destinations'
        ) THEN
            ALTER TABLE v_voice_transfer_destinations 
            ADD CONSTRAINT fk_vtd_secretary 
            FOREIGN KEY (secretary_uuid) 
            REFERENCES v_voice_secretaries(voice_secretary_uuid) 
            ON DELETE SET NULL;
        END IF;
    END IF;
END $$;

-- ============================================================================
-- TRIGGER PARA UPDATED_AT
-- ============================================================================

-- Função para atualizar updated_at
CREATE OR REPLACE FUNCTION update_vtd_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop trigger se existir e recriar
DROP TRIGGER IF EXISTS trg_vtd_updated_at ON v_voice_transfer_destinations;
CREATE TRIGGER trg_vtd_updated_at
    BEFORE UPDATE ON v_voice_transfer_destinations
    FOR EACH ROW
    EXECUTE FUNCTION update_vtd_updated_at();

-- ============================================================================
-- LOG DE EXECUÇÃO
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration 012_create_voice_transfer_destinations.sql executada com sucesso em %', NOW();
END $$;
