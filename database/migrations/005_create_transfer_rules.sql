-- ============================================
-- Migration 005: v_voice_transfer_rules
-- Voice AI IVR - Regras de Transferência
-- 
-- ⚠️ MULTI-TENANT: Vinculado via voice_secretary_uuid
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

CREATE TABLE IF NOT EXISTS v_voice_transfer_rules (
    transfer_rule_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    voice_secretary_uuid UUID NOT NULL REFERENCES v_voice_secretaries(voice_secretary_uuid) ON DELETE CASCADE,
    
    -- Detecção de intenção
    intent_keywords TEXT[],
    intent_patterns TEXT[],
    
    -- Destino
    department_name VARCHAR(100) NOT NULL,
    transfer_extension VARCHAR(20) NOT NULL,
    
    -- Mensagem antes de transferir
    transfer_message TEXT,
    
    -- Prioridade (menor = maior prioridade)
    priority INTEGER DEFAULT 0,
    
    -- Controle
    is_enabled BOOLEAN DEFAULT TRUE,
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    update_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Adicionar colunas se não existirem
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' AND column_name = 'is_enabled') 
    THEN
        ALTER TABLE v_voice_transfer_rules ADD COLUMN is_enabled BOOLEAN DEFAULT TRUE;
    END IF;
END $$;

-- Índices
CREATE INDEX IF NOT EXISTS idx_voice_transfer_rules_secretary 
    ON v_voice_transfer_rules(voice_secretary_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_transfer_rules_enabled 
    ON v_voice_transfer_rules(voice_secretary_uuid, is_enabled, priority);

-- Comentários
COMMENT ON TABLE v_voice_transfer_rules IS 'Regras de transferência por departamento';
COMMENT ON COLUMN v_voice_transfer_rules.intent_keywords IS 'Palavras-chave para detectar intenção (ex: {financeiro, boleto, pagamento})';
COMMENT ON COLUMN v_voice_transfer_rules.intent_patterns IS 'Padrões regex para detecção mais avançada';
