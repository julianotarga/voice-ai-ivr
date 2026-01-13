-- ============================================
-- Migration 004: v_voice_conversations e v_voice_messages
-- Voice AI IVR - Histórico de Conversas
-- 
-- ⚠️ MULTI-TENANT: domain_uuid é OBRIGATÓRIO
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- Conversas
CREATE TABLE IF NOT EXISTS v_voice_conversations (
    voice_conversation_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid) ON DELETE CASCADE,
    voice_secretary_uuid UUID REFERENCES v_voice_secretaries(voice_secretary_uuid) ON DELETE SET NULL,
    
    -- Identificação da chamada
    call_uuid UUID,
    caller_id_number VARCHAR(50),
    caller_id_name VARCHAR(255),
    
    -- Tempo
    start_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    end_time TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    
    -- Modo de processamento (v1 ou v2)
    processing_mode VARCHAR(20) DEFAULT 'turn_based',
    
    -- Estatísticas
    total_turns INTEGER DEFAULT 0,
    
    -- Resultado
    final_action VARCHAR(50),
    transfer_extension VARCHAR(20),
    transfer_department VARCHAR(100),
    
    -- Integração OmniPlay
    ticket_created BOOLEAN DEFAULT FALSE,
    ticket_id VARCHAR(50),
    
    -- Controle
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Mensagens da conversa
CREATE TABLE IF NOT EXISTS v_voice_messages (
    voice_message_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    voice_conversation_uuid UUID NOT NULL REFERENCES v_voice_conversations(voice_conversation_uuid) ON DELETE CASCADE,
    
    -- Ordem
    turn_number INTEGER NOT NULL,
    
    -- Conteúdo
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    
    -- Metadados de áudio
    audio_duration_ms INTEGER,
    audio_file_path VARCHAR(500),
    
    -- Metadados de IA
    provider_used VARCHAR(50),
    tokens_used INTEGER,
    rag_sources TEXT[],
    detected_intent VARCHAR(100),
    
    -- Timestamps
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Adicionar colunas se não existirem
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_conversations' AND column_name = 'processing_mode') 
    THEN
        ALTER TABLE v_voice_conversations ADD COLUMN processing_mode VARCHAR(20) DEFAULT 'turn_based';
    END IF;
END $$;

-- Índices para v_voice_conversations
CREATE INDEX IF NOT EXISTS idx_voice_conversations_domain 
    ON v_voice_conversations(domain_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_conversations_secretary 
    ON v_voice_conversations(voice_secretary_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_conversations_caller 
    ON v_voice_conversations(domain_uuid, caller_id_number);
CREATE INDEX IF NOT EXISTS idx_voice_conversations_date 
    ON v_voice_conversations(domain_uuid, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_voice_conversations_call 
    ON v_voice_conversations(call_uuid);

-- Índices para v_voice_messages
CREATE INDEX IF NOT EXISTS idx_voice_messages_conversation 
    ON v_voice_messages(voice_conversation_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_messages_turn 
    ON v_voice_messages(voice_conversation_uuid, turn_number);
CREATE INDEX IF NOT EXISTS idx_voice_messages_role 
    ON v_voice_messages(role);
CREATE INDEX IF NOT EXISTS idx_voice_messages_intent 
    ON v_voice_messages(detected_intent) 
    WHERE detected_intent IS NOT NULL;

-- Comentários
COMMENT ON TABLE v_voice_conversations IS 'Histórico de conversas da secretária virtual';
COMMENT ON COLUMN v_voice_conversations.domain_uuid IS 'OBRIGATÓRIO: UUID do domínio para isolamento multi-tenant';
COMMENT ON COLUMN v_voice_conversations.final_action IS 'Ação final: resolved, transferred, timeout, error';
COMMENT ON COLUMN v_voice_conversations.processing_mode IS 'Modo usado: turn_based (v1) ou realtime (v2)';

COMMENT ON TABLE v_voice_messages IS 'Mensagens individuais das conversas (transcrições)';
COMMENT ON COLUMN v_voice_messages.role IS 'Papel: user ou assistant';
