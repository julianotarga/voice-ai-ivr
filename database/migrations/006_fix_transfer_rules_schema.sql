-- ============================================
-- Migration 006: Alinhar v_voice_transfer_rules
-- 
-- PROBLEMA: Discrepância entre schema e código PHP
-- - PHP usa: keywords (JSON), is_active, domain_uuid
-- - Migration original usa: intent_keywords (TEXT[]), is_enabled
--
-- SOLUÇÃO: Adicionar colunas alternativas e domain_uuid
-- 
-- ⚠️ MULTI-TENANT: Agora suporta domain_uuid diretamente
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- Adicionar domain_uuid para suporte multi-tenant direto
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' AND column_name = 'domain_uuid') 
    THEN
        -- Adicionar coluna domain_uuid
        ALTER TABLE v_voice_transfer_rules 
            ADD COLUMN domain_uuid UUID;
        
        -- Popular domain_uuid a partir de voice_secretary_uuid
        UPDATE v_voice_transfer_rules r
        SET domain_uuid = s.domain_uuid
        FROM v_voice_secretaries s
        WHERE r.voice_secretary_uuid = s.voice_secretary_uuid;
    END IF;
END $$;

-- Adicionar coluna keywords (JSON compatível com PHP)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' AND column_name = 'keywords') 
    THEN
        ALTER TABLE v_voice_transfer_rules 
            ADD COLUMN keywords JSONB;
        
        -- Copiar intent_keywords para keywords (converter array para JSON)
        UPDATE v_voice_transfer_rules 
        SET keywords = to_jsonb(intent_keywords)
        WHERE intent_keywords IS NOT NULL 
          AND keywords IS NULL;
    END IF;
END $$;

-- Adicionar coluna is_active (compatível com PHP)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' AND column_name = 'is_active') 
    THEN
        ALTER TABLE v_voice_transfer_rules 
            ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
        
        -- Copiar is_enabled para is_active
        UPDATE v_voice_transfer_rules 
        SET is_active = is_enabled
        WHERE is_enabled IS NOT NULL;
    END IF;
END $$;

-- Adicionar transfer_message se não existir
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' AND column_name = 'transfer_message') 
    THEN
        ALTER TABLE v_voice_transfer_rules 
            ADD COLUMN transfer_message TEXT;
    END IF;
END $$;

-- Tornar voice_secretary_uuid opcional (permite regras globais por domain)
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' 
          AND column_name = 'voice_secretary_uuid'
          AND is_nullable = 'NO') 
    THEN
        ALTER TABLE v_voice_transfer_rules 
            ALTER COLUMN voice_secretary_uuid DROP NOT NULL;
    END IF;
END $$;

-- Criar índice para domain_uuid
CREATE INDEX IF NOT EXISTS idx_voice_transfer_rules_domain
    ON v_voice_transfer_rules(domain_uuid);

-- Criar índice composto para busca por domain e ativo
CREATE INDEX IF NOT EXISTS idx_voice_transfer_rules_domain_active
    ON v_voice_transfer_rules(domain_uuid, is_active, priority);

-- Trigger para manter sincronização entre campos
-- Quando atualizar keywords, atualizar intent_keywords também
CREATE OR REPLACE FUNCTION sync_transfer_rules_keywords()
RETURNS TRIGGER AS $$
BEGIN
    -- Sincronizar keywords -> intent_keywords
    IF NEW.keywords IS NOT NULL AND NEW.keywords IS DISTINCT FROM OLD.keywords THEN
        NEW.intent_keywords := ARRAY(SELECT jsonb_array_elements_text(NEW.keywords));
    END IF;
    
    -- Sincronizar intent_keywords -> keywords
    IF NEW.intent_keywords IS NOT NULL AND NEW.intent_keywords IS DISTINCT FROM OLD.intent_keywords THEN
        NEW.keywords := to_jsonb(NEW.intent_keywords);
    END IF;
    
    -- Sincronizar is_active <-> is_enabled
    IF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        NEW.is_enabled := NEW.is_active;
    ELSIF NEW.is_enabled IS DISTINCT FROM OLD.is_enabled THEN
        NEW.is_active := NEW.is_enabled;
    END IF;
    
    -- Atualizar update_date
    NEW.update_date := NOW();
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sync_transfer_rules_keywords_trigger ON v_voice_transfer_rules;
CREATE TRIGGER sync_transfer_rules_keywords_trigger
    BEFORE UPDATE ON v_voice_transfer_rules
    FOR EACH ROW
    EXECUTE FUNCTION sync_transfer_rules_keywords();

-- Trigger para insert também
CREATE OR REPLACE FUNCTION sync_transfer_rules_keywords_insert()
RETURNS TRIGGER AS $$
BEGIN
    -- Sincronizar keywords -> intent_keywords
    IF NEW.keywords IS NOT NULL AND NEW.intent_keywords IS NULL THEN
        NEW.intent_keywords := ARRAY(SELECT jsonb_array_elements_text(NEW.keywords));
    END IF;
    
    -- Sincronizar intent_keywords -> keywords
    IF NEW.intent_keywords IS NOT NULL AND NEW.keywords IS NULL THEN
        NEW.keywords := to_jsonb(NEW.intent_keywords);
    END IF;
    
    -- Sincronizar is_active <-> is_enabled
    IF NEW.is_active IS NULL THEN
        NEW.is_active := COALESCE(NEW.is_enabled, TRUE);
    END IF;
    IF NEW.is_enabled IS NULL THEN
        NEW.is_enabled := COALESCE(NEW.is_active, TRUE);
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sync_transfer_rules_keywords_insert_trigger ON v_voice_transfer_rules;
CREATE TRIGGER sync_transfer_rules_keywords_insert_trigger
    BEFORE INSERT ON v_voice_transfer_rules
    FOR EACH ROW
    EXECUTE FUNCTION sync_transfer_rules_keywords_insert();

-- Comentários
COMMENT ON COLUMN v_voice_transfer_rules.domain_uuid IS 'UUID do tenant (domain) - permite regras globais sem secretária específica';
COMMENT ON COLUMN v_voice_transfer_rules.keywords IS 'Palavras-chave em formato JSON (compatível com PHP)';
COMMENT ON COLUMN v_voice_transfer_rules.is_active IS 'Status ativo (compatível com PHP)';
COMMENT ON COLUMN v_voice_transfer_rules.transfer_message IS 'Mensagem opcional a ser falada antes da transferência';
