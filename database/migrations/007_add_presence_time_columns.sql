-- ============================================
-- Migration 007: Adicionar colunas para Presence Check e Time Conditions
-- 
-- Ref: openspec/changes/add-realtime-handoff-omni/tasks.md
-- - Seção 10: Extension Presence Check (ESL)
-- - Seção 11: Time Conditions Check
--
-- ⚠️ MULTI-TENANT: Colunas são por secretária (isolamento mantido)
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- Adicionar coluna presence_check_enabled (default: true)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'presence_check_enabled') 
    THEN
        ALTER TABLE v_voice_secretaries 
            ADD COLUMN presence_check_enabled BOOLEAN DEFAULT TRUE;
        
        COMMENT ON COLUMN v_voice_secretaries.presence_check_enabled IS 
            'Se true, verifica se ramal está online via ESL antes de transferir';
    END IF;
END $$;

-- Adicionar coluna handoff_timeout (default: 30 segundos)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'handoff_timeout') 
    THEN
        ALTER TABLE v_voice_secretaries 
            ADD COLUMN handoff_timeout INTEGER DEFAULT 30;
        
        COMMENT ON COLUMN v_voice_secretaries.handoff_timeout IS 
            'Timeout em segundos para transferência (default: 30)';
    END IF;
END $$;

-- Adicionar coluna time_condition_uuid (FK para v_time_conditions)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'time_condition_uuid') 
    THEN
        ALTER TABLE v_voice_secretaries 
            ADD COLUMN time_condition_uuid UUID;
        
        COMMENT ON COLUMN v_voice_secretaries.time_condition_uuid IS 
            'Referência para time condition do FusionPBX (horário de atendimento)';
    END IF;
END $$;

-- Adicionar coluna webhook_url para integração com OmniPlay
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'webhook_url') 
    THEN
        ALTER TABLE v_voice_secretaries 
            ADD COLUMN webhook_url VARCHAR(500);
        
        COMMENT ON COLUMN v_voice_secretaries.webhook_url IS 
            'URL de webhook para notificar OmniPlay sobre eventos de chamada';
    END IF;
END $$;

-- Adicionar FK para time_conditions (se a tabela existir)
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'v_time_conditions')
       AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints 
           WHERE constraint_name = 'fk_voice_secretary_time_condition')
    THEN
        ALTER TABLE v_voice_secretaries
            ADD CONSTRAINT fk_voice_secretary_time_condition
            FOREIGN KEY (time_condition_uuid) 
            REFERENCES v_time_conditions(time_condition_uuid)
            ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN OTHERS THEN
    -- Ignorar se FK não puder ser criada (tabela não existe)
    RAISE NOTICE 'Could not create FK to v_time_conditions: %', SQLERRM;
END $$;

-- Criar índice para time_condition_uuid
CREATE INDEX IF NOT EXISTS idx_voice_secretaries_time_condition
    ON v_voice_secretaries(time_condition_uuid)
    WHERE time_condition_uuid IS NOT NULL;

-- ============================================
-- Adicionar colunas na tabela v_voice_transfer_rules também
-- ============================================

-- Adicionar coluna transfer_timeout_seconds por regra (override do global)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' AND column_name = 'transfer_timeout_seconds') 
    THEN
        ALTER TABLE v_voice_transfer_rules 
            ADD COLUMN transfer_timeout_seconds INTEGER;
        
        COMMENT ON COLUMN v_voice_transfer_rules.transfer_timeout_seconds IS 
            'Timeout específico para esta regra (override do timeout da secretária)';
    END IF;
END $$;

-- Adicionar coluna skip_presence_check por regra
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_transfer_rules' AND column_name = 'skip_presence_check') 
    THEN
        ALTER TABLE v_voice_transfer_rules 
            ADD COLUMN skip_presence_check BOOLEAN DEFAULT FALSE;
        
        COMMENT ON COLUMN v_voice_transfer_rules.skip_presence_check IS 
            'Se true, pula verificação de presença para esta regra específica';
    END IF;
END $$;

-- ============================================
-- Log de migração
-- ============================================
DO $$ BEGIN
    RAISE NOTICE 'Migration 007 completed: Added presence_check_enabled, handoff_timeout, time_condition_uuid, webhook_url columns';
END $$;
