-- ============================================================================
-- Setup Transfer Destinations
-- Script consolidado: Migration + Seed para v_voice_transfer_destinations
-- 
-- Uso: psql -U fusionpbx -d fusionpbx -f setup_transfer_destinations.sql
-- ============================================================================

-- ============================================================================
-- PARTE 1: CRIAR TABELA (Migration 012)
-- ============================================================================

CREATE TABLE IF NOT EXISTS v_voice_transfer_destinations (
    transfer_destination_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL,
    secretary_uuid UUID,
    
    -- Identificação por voz/texto
    name VARCHAR(100) NOT NULL,
    aliases JSONB DEFAULT '[]'::jsonb,
    
    -- Destino FreeSWITCH
    destination_type VARCHAR(20) NOT NULL DEFAULT 'extension',
    destination_number VARCHAR(50) NOT NULL,
    destination_context VARCHAR(50) DEFAULT 'default',
    
    -- Configurações de transfer
    ring_timeout_seconds INT DEFAULT 30,
    max_retries INT DEFAULT 1,
    retry_delay_seconds INT DEFAULT 5,
    
    -- Fallback
    fallback_action VARCHAR(30) DEFAULT 'offer_ticket',
    
    -- Metadados
    department VARCHAR(100),
    role VARCHAR(100),
    description TEXT,
    working_hours JSONB,
    
    -- Controle
    priority INT DEFAULT 100,
    is_enabled BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,
    
    -- Auditoria
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT chk_destination_type CHECK (destination_type IN (
        'extension', 'ring_group', 'queue', 'external', 'voicemail'
    )),
    CONSTRAINT chk_fallback_action CHECK (fallback_action IN (
        'offer_ticket', 'create_ticket', 'voicemail', 'return_agent', 'hangup'
    ))
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_vtd_domain ON v_voice_transfer_destinations(domain_uuid);
CREATE INDEX IF NOT EXISTS idx_vtd_secretary ON v_voice_transfer_destinations(secretary_uuid);
CREATE INDEX IF NOT EXISTS idx_vtd_enabled ON v_voice_transfer_destinations(domain_uuid, is_enabled) WHERE is_enabled = true;

-- Índice único para garantir apenas um default por domain
DROP INDEX IF EXISTS idx_vtd_default_unique;
CREATE UNIQUE INDEX idx_vtd_default_unique 
    ON v_voice_transfer_destinations(domain_uuid) 
    WHERE is_default = true AND is_enabled = true;

-- Foreign Keys (se tabelas existirem)
DO $$ 
BEGIN
    -- FK para domain
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
    
    -- FK para secretary (opcional)
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
    
    RAISE NOTICE '✅ Tabela v_voice_transfer_destinations criada/verificada';
END $$;

-- ============================================================================
-- PARTE 2: INSERIR DESTINO VENDAS (Seed)
-- ============================================================================

DO $$
DECLARE
    v_domain_uuid UUID;
    v_count INT;
BEGIN
    -- Obter primeiro domain
    SELECT domain_uuid INTO v_domain_uuid FROM v_domains LIMIT 1;
    
    IF v_domain_uuid IS NULL THEN
        RAISE NOTICE '⚠️ Nenhum domain encontrado. Seed pulado.';
        RETURN;
    END IF;
    
    -- Verificar se já existe destino Vendas
    SELECT COUNT(*) INTO v_count 
    FROM v_voice_transfer_destinations 
    WHERE domain_uuid = v_domain_uuid AND name = 'Vendas';
    
    IF v_count > 0 THEN
        RAISE NOTICE '⚠️ Destino "Vendas" já existe. Pulando...';
        RETURN;
    END IF;
    
    -- Inserir destino Vendas
    INSERT INTO v_voice_transfer_destinations (
        domain_uuid,
        name,
        aliases,
        destination_type,
        destination_number,
        destination_context,
        ring_timeout_seconds,
        fallback_action,
        department,
        priority,
        is_enabled,
        is_default
    ) VALUES (
        v_domain_uuid,
        'Vendas',
        '["vendas", "comercial", "comprar", "setor de vendas", "quero comprar"]'::jsonb,
        'extension',
        '1001',
        'default',
        30,
        'offer_ticket',
        'Comercial',
        50,
        true,
        true
    );
    
    RAISE NOTICE '✅ Destino "Vendas" (ramal 1001) criado com sucesso!';
    RAISE NOTICE '   Domain UUID: %', v_domain_uuid;
END $$;

-- ============================================================================
-- VERIFICAÇÃO FINAL
-- ============================================================================

DO $$
DECLARE
    v_count INT;
BEGIN
    SELECT COUNT(*) INTO v_count FROM v_voice_transfer_destinations;
    RAISE NOTICE '';
    RAISE NOTICE '📊 RESUMO:';
    RAISE NOTICE '   Total de destinos configurados: %', v_count;
    RAISE NOTICE '';
    RAISE NOTICE '🔄 Próximo passo: docker restart voice-ai-realtime';
END $$;

-- Listar destinos criados
SELECT name, destination_number, aliases, is_default 
FROM v_voice_transfer_destinations 
ORDER BY priority;
