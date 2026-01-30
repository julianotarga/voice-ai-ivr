-- =============================================================================
-- Voice AI IVR - Consolidated Migrations
-- =============================================================================
-- Este arquivo contém todas as migrations consolidadas para instalação limpa.
-- Gerado automaticamente a partir de database/migrations/
-- 
-- IMPORTANTE: Todas as operações são IDEMPOTENTES
-- Pode ser executado múltiplas vezes sem causar erros
-- =============================================================================

-- Verificar se extensão uuid está disponível
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- =============================================================================
-- Migration: 001_create_providers.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 002_create_secretaries.sql
-- =============================================================================

-- Migration 002: v_voice_secretaries
-- IDEMPOTENTE

CREATE TABLE IF NOT EXISTS v_voice_secretaries (
    voice_secretary_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid) ON DELETE CASCADE,
    secretary_name VARCHAR(100) NOT NULL,
    company_name VARCHAR(200),
    extension VARCHAR(20),
    personality_prompt TEXT NOT NULL DEFAULT '',
    greeting_message TEXT,
    farewell_message TEXT,
    fallback_message TEXT DEFAULT 'Desculpe, nao entendi.',
    transfer_message TEXT DEFAULT 'Transferindo...',
    stt_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    tts_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    llm_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    embeddings_provider_uuid UUID REFERENCES v_voice_ai_providers(voice_ai_provider_uuid),
    tts_voice_id VARCHAR(100),
    tts_speed DECIMAL(3,2) DEFAULT 1.0,
    language VARCHAR(10) DEFAULT 'pt-BR',
    llm_model_override VARCHAR(100),
    llm_temperature DECIMAL(3,2) DEFAULT 0.7,
    llm_max_tokens INTEGER DEFAULT 500,
    max_turns INTEGER DEFAULT 20,
    silence_timeout_ms INTEGER DEFAULT 3000,
    max_recording_seconds INTEGER DEFAULT 30,
    transfer_extension VARCHAR(20),
    transfer_on_failure BOOLEAN DEFAULT TRUE,
    create_ticket_on_transfer BOOLEAN DEFAULT FALSE,
    omniplay_webhook_url VARCHAR(500),
    omniplay_queue_id VARCHAR(50),
    is_enabled BOOLEAN DEFAULT TRUE,
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    update_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'processing_mode') 
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN processing_mode VARCHAR(20) DEFAULT 'turn_based';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'realtime_provider_uuid') 
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN realtime_provider_uuid UUID 
            REFERENCES v_voice_ai_providers(voice_ai_provider_uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'vad_threshold') 
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN vad_threshold DECIMAL(3,2) DEFAULT 0.5;
    END IF;

    -- FusionPBX padrão: auditoria usada pelo database->save()
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'insert_user')
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN insert_user UUID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'update_user')
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN update_user UUID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'update_date')
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN update_date TIMESTAMP WITH TIME ZONE DEFAULT NOW();
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_voice_secretaries_domain ON v_voice_secretaries(domain_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_secretaries_enabled ON v_voice_secretaries(domain_uuid, is_enabled);
CREATE INDEX IF NOT EXISTS idx_voice_secretaries_extension ON v_voice_secretaries(domain_uuid, extension);

COMMENT ON TABLE v_voice_secretaries IS 'Secretarias virtuais com IA';


-- =============================================================================
-- Migration: 003_create_documents.sql
-- =============================================================================

-- ============================================
-- Migration 003: v_voice_documents e v_voice_document_chunks
-- Voice AI IVR - Base de Conhecimento (RAG)
-- 
-- ⚠️ MULTI-TENANT: domain_uuid é OBRIGATÓRIO
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- Documentos
CREATE TABLE IF NOT EXISTS v_voice_documents (
    voice_document_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid) ON DELETE CASCADE,
    voice_secretary_uuid UUID REFERENCES v_voice_secretaries(voice_secretary_uuid) ON DELETE SET NULL,
    
    -- Metadados
    document_name VARCHAR(255) NOT NULL,
    document_type VARCHAR(50),
    file_path VARCHAR(500),
    file_size INTEGER,
    mime_type VARCHAR(100),
    
    -- Conteúdo extraído
    content TEXT,
    
    -- Status de processamento
    chunk_count INTEGER DEFAULT 0,
    processing_status VARCHAR(50) DEFAULT 'pending',
    processing_error TEXT,
    processed_at TIMESTAMP WITH TIME ZONE,
    
    -- Controle
    is_enabled BOOLEAN DEFAULT TRUE,
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    update_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Chunks vetorizados (para RAG)
CREATE TABLE IF NOT EXISTS v_voice_document_chunks (
    chunk_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    voice_document_uuid UUID NOT NULL REFERENCES v_voice_documents(voice_document_uuid) ON DELETE CASCADE,
    
    -- Conteúdo
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    
    -- Embedding (usar pgvector se disponível, senão armazenar como JSONB)
    embedding JSONB,
    embedding_model VARCHAR(100),
    embedding_dimensions INTEGER,
    
    -- Metadados
    token_count INTEGER,
    
    -- Timestamps
    insert_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Adicionar colunas se não existirem
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_documents' AND column_name = 'is_enabled') 
    THEN
        ALTER TABLE v_voice_documents ADD COLUMN is_enabled BOOLEAN DEFAULT TRUE;
    END IF;
END $$;

-- Índices
CREATE INDEX IF NOT EXISTS idx_voice_documents_domain 
    ON v_voice_documents(domain_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_documents_secretary 
    ON v_voice_documents(voice_secretary_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_documents_enabled 
    ON v_voice_documents(domain_uuid, is_enabled);
CREATE INDEX IF NOT EXISTS idx_voice_documents_status 
    ON v_voice_documents(processing_status);

CREATE INDEX IF NOT EXISTS idx_voice_document_chunks_document 
    ON v_voice_document_chunks(voice_document_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_document_chunks_index 
    ON v_voice_document_chunks(voice_document_uuid, chunk_index);

-- Comentários
COMMENT ON TABLE v_voice_documents IS 'Documentos da base de conhecimento para RAG';
COMMENT ON COLUMN v_voice_documents.domain_uuid IS 'OBRIGATÓRIO: UUID do domínio para isolamento multi-tenant';
COMMENT ON COLUMN v_voice_documents.voice_secretary_uuid IS 'Secretária específica (NULL = disponível para todas do domínio)';
COMMENT ON TABLE v_voice_document_chunks IS 'Fragmentos dos documentos com embeddings para busca vetorial';


-- =============================================================================
-- Migration: 004_create_conversations.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 005_create_transfer_rules.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 006_create_messages.sql
-- =============================================================================

-- Migration 006: Adicionar colunas extras a v_voice_messages
-- IDEMPOTENTE: Apenas adiciona colunas se nao existirem

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'stt_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN stt_provider VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'stt_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN stt_latency_ms INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'tts_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN tts_provider VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'tts_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN tts_latency_ms INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'llm_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN llm_provider VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'llm_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN llm_latency_ms INTEGER;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_voice_messages_role ON v_voice_messages(role);
CREATE INDEX IF NOT EXISTS idx_voice_messages_intent ON v_voice_messages(detected_intent) WHERE detected_intent IS NOT NULL;

COMMENT ON TABLE v_voice_messages IS 'Mensagens individuais das conversas';


-- =============================================================================
-- Migration: 006_fix_transfer_rules_schema.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 007_add_presence_time_columns.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 007_insert_default_providers.sql
-- =============================================================================

-- Migration: Insert default AI providers
-- Version: 007
-- Description: Insere providers padrão para cada tipo (STT, TTS, LLM, Embeddings)
-- 
-- NOTA: Esta migration usa INSERT ON CONFLICT para ser idempotente.
-- Os providers são inseridos apenas se não existirem para o domain_uuid.
-- 
-- Para usar em produção, execute para cada domain:
-- UPDATE esta migration SET domain_uuid = '<seu-domain-uuid>' e execute.

-- =====================================================================
-- FUNÇÃO: Criar providers padrão para um domínio
-- =====================================================================

CREATE OR REPLACE FUNCTION create_default_voice_ai_providers(target_domain_uuid UUID)
RETURNS void AS $$
BEGIN
    -- =====================================================================
    -- STT PROVIDERS
    -- =====================================================================
    
    -- Whisper Local (padrão)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name, 
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'stt', 'whisper_local', 'Whisper Local',
        '{"model": "base", "language": "pt"}'::jsonb,
        true, true
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- OpenAI Whisper API
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'stt', 'whisper_api', 'OpenAI Whisper API',
        '{"model": "whisper-1", "language": "pt"}'::jsonb,
        false, false
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- =====================================================================
    -- TTS PROVIDERS
    -- =====================================================================
    
    -- Piper Local (padrão)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'tts', 'piper_local', 'Piper TTS Local',
        '{"voice": "pt_BR-faber-medium", "output_format": "wav"}'::jsonb,
        true, true
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- OpenAI TTS
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'tts', 'openai_tts', 'OpenAI TTS',
        '{"model": "tts-1", "voice": "nova"}'::jsonb,
        false, false
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- ElevenLabs
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'tts', 'elevenlabs', 'ElevenLabs',
        '{"voice_id": "", "model_id": "eleven_multilingual_v2"}'::jsonb,
        false, false
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- =====================================================================
    -- LLM PROVIDERS
    -- =====================================================================
    
    -- OpenAI GPT-4o-mini (padrão)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'llm', 'openai', 'OpenAI GPT-4o-mini',
        '{"model": "gpt-4o-mini", "temperature": 0.7, "max_tokens": 500}'::jsonb,
        true, false
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- Anthropic Claude
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'llm', 'anthropic', 'Anthropic Claude',
        '{"model": "claude-3-haiku-20240307", "temperature": 0.7, "max_tokens": 500}'::jsonb,
        false, false
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- Groq (Llama rápido)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'llm', 'groq', 'Groq Llama',
        '{"model": "llama-3.1-8b-instant", "temperature": 0.7, "max_tokens": 500}'::jsonb,
        false, false
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- Ollama Local
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'llm', 'ollama_local', 'Ollama Local',
        '{"model": "llama3.2", "base_url": "http://localhost:11434"}'::jsonb,
        false, true
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- =====================================================================
    -- EMBEDDINGS PROVIDERS
    -- =====================================================================
    
    -- Local sentence-transformers (padrão)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'embeddings', 'local', 'Local Embeddings',
        '{"model": "all-MiniLM-L6-v2", "dimension": 384}'::jsonb,
        true, true
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- OpenAI Embeddings
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 'embeddings', 'openai', 'OpenAI Embeddings',
        '{"model": "text-embedding-3-small", "dimension": 1536}'::jsonb,
        false, false
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;

END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- COMENTÁRIO: Para criar providers para um domínio específico, execute:
-- SELECT create_default_voice_ai_providers('seu-domain-uuid-aqui');
-- =====================================================================

COMMENT ON FUNCTION create_default_voice_ai_providers(UUID) IS 
'Cria providers de IA padrão para um domínio. Idempotente - não duplica se já existir.';


-- =============================================================================
-- Migration: 008_add_realtime_fields.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 009_add_handoff_fields.sql
-- =============================================================================

-- Migration: Add Handoff OmniPlay fields to v_voice_secretaries
-- Ref: openspec/changes/add-realtime-handoff-omni/proposal.md

-- OmniPlay Company ID (mapping FusionPBX domain_uuid → OmniPlay companyId)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'omniplay_company_id'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN omniplay_company_id INTEGER;
        COMMENT ON COLUMN v_voice_secretaries.omniplay_company_id IS 'OmniPlay companyId for API integration';
    END IF;
END $$;

-- Handoff enabled flag
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'handoff_enabled'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN handoff_enabled BOOLEAN DEFAULT true;
        COMMENT ON COLUMN v_voice_secretaries.handoff_enabled IS 'Enable handoff to human agents or ticket creation';
    END IF;
END $$;

-- Handoff keywords (comma-separated)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'handoff_keywords'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN handoff_keywords VARCHAR(500) DEFAULT 'atendente,humano,pessoa,operador';
        COMMENT ON COLUMN v_voice_secretaries.handoff_keywords IS 'Comma-separated keywords that trigger handoff';
    END IF;
END $$;

-- Fallback ticket enabled
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'fallback_ticket_enabled'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN fallback_ticket_enabled BOOLEAN DEFAULT true;
        COMMENT ON COLUMN v_voice_secretaries.fallback_ticket_enabled IS 'Create pending ticket when no agents available';
    END IF;
END $$;

-- Handoff queue ID (OmniPlay queue for ticket assignment)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'handoff_queue_id'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN handoff_queue_id INTEGER;
        COMMENT ON COLUMN v_voice_secretaries.handoff_queue_id IS 'OmniPlay queue ID for ticket assignment';
    END IF;
END $$;

-- Handoff timeout (already exists, but ensure it's there)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'handoff_timeout'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN handoff_timeout INTEGER DEFAULT 30;
        COMMENT ON COLUMN v_voice_secretaries.handoff_timeout IS 'Timeout in seconds before fallback';
    END IF;
END $$;

-- Presence check enabled (already exists, but ensure it's there)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'presence_check_enabled'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN presence_check_enabled BOOLEAN DEFAULT true;
        COMMENT ON COLUMN v_voice_secretaries.presence_check_enabled IS 'Check extension presence before transfer';
    END IF;
END $$;

-- Time condition UUID (business hours restriction)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'time_condition_uuid'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN time_condition_uuid UUID;
        COMMENT ON COLUMN v_voice_secretaries.time_condition_uuid IS 'Time condition UUID for business hours';
    END IF;
END $$;

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_voice_secretaries_handoff 
ON v_voice_secretaries (domain_uuid, handoff_enabled) 
WHERE handoff_enabled = true;

SELECT 'Migration 009_add_handoff_fields completed successfully' AS status;


-- =============================================================================
-- Migration: 010_add_audio_config_fields.sql
-- =============================================================================

-- Migration: Add Audio Configuration fields to v_voice_secretaries
-- Permite configurar buffer de áudio e jitter por secretária para evitar picotamento

-- =============================================================================
-- AUDIO BUFFER CONFIGURATION
-- =============================================================================

-- Warmup chunks (número de chunks de 20ms antes de iniciar playback)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'audio_warmup_chunks'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN audio_warmup_chunks INTEGER DEFAULT 15;
        COMMENT ON COLUMN v_voice_secretaries.audio_warmup_chunks IS 'Number of 20ms chunks to buffer before playback (default: 15 = 300ms)';
    END IF;
END $$;

-- Warmup milliseconds (buffer de warmup em milissegundos)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'audio_warmup_ms'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN audio_warmup_ms INTEGER DEFAULT 400;
        COMMENT ON COLUMN v_voice_secretaries.audio_warmup_ms IS 'Audio warmup buffer in milliseconds (default: 400ms)';
    END IF;
END $$;

-- =============================================================================
-- JITTER BUFFER CONFIGURATION (FreeSWITCH)
-- =============================================================================

-- Jitter buffer min (mínimo em ms)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'jitter_buffer_min'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN jitter_buffer_min INTEGER DEFAULT 100;
        COMMENT ON COLUMN v_voice_secretaries.jitter_buffer_min IS 'FreeSWITCH jitter buffer minimum in ms (default: 100)';
    END IF;
END $$;

-- Jitter buffer max (máximo em ms)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'jitter_buffer_max'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN jitter_buffer_max INTEGER DEFAULT 300;
        COMMENT ON COLUMN v_voice_secretaries.jitter_buffer_max IS 'FreeSWITCH jitter buffer maximum in ms (default: 300)';
    END IF;
END $$;

-- Jitter buffer step (passo de ajuste em ms)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'jitter_buffer_step'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN jitter_buffer_step INTEGER DEFAULT 40;
        COMMENT ON COLUMN v_voice_secretaries.jitter_buffer_step IS 'FreeSWITCH jitter buffer step in ms (default: 40)';
    END IF;
END $$;

-- =============================================================================
-- STREAM BUFFER CONFIGURATION (mod_audio_stream)
-- =============================================================================

-- Stream buffer size (tamanho do buffer do mod_audio_stream em samples)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'stream_buffer_size'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN stream_buffer_size INTEGER DEFAULT 320;
        COMMENT ON COLUMN v_voice_secretaries.stream_buffer_size IS 'mod_audio_stream buffer size in samples (default: 320 = 20ms @ 16kHz)';
    END IF;
END $$;

-- =============================================================================
-- ADAPTIVE SETTINGS
-- =============================================================================

-- Adaptive warmup enabled (ajusta automaticamente o warmup)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'audio_adaptive_warmup'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN audio_adaptive_warmup BOOLEAN DEFAULT true;
        COMMENT ON COLUMN v_voice_secretaries.audio_adaptive_warmup IS 'Enable adaptive warmup adjustment (default: true)';
    END IF;
END $$;

SELECT 'Migration 010_add_audio_config_fields completed successfully' AS status;


-- =============================================================================
-- Migration: 011_fix_stream_buffer_size_unit.sql
-- =============================================================================

-- =============================================================================
-- CORREÇÃO: STREAM_BUFFER_SIZE É EM MILISSEGUNDOS, NÃO SAMPLES!
-- =============================================================================
-- 
-- Descoberta (16/Jan/2026):
-- O mod_audio_stream README documenta claramente:
--   "STREAM_BUFFER_SIZE | buffer duration in MILLISECONDS, divisible by 20 | 20"
--
-- Estava configurado como 320 (pensando ser 320 samples = 20ms @ 16kHz)
-- Mas na verdade era interpretado como 320ms de buffer!
-- Isso causava chunks de áudio chegando a cada 320ms ao invés de 20ms.
--
-- Correção: 320 → 20 (20ms = valor padrão recomendado)
-- =============================================================================

-- Atualizar default da coluna
ALTER TABLE v_voice_secretaries 
ALTER COLUMN stream_buffer_size SET DEFAULT 20;

-- Corrigir valores existentes que usam o valor errado (320)
UPDATE v_voice_secretaries 
SET stream_buffer_size = 20 
WHERE stream_buffer_size = 320;

-- Atualizar comentário
COMMENT ON COLUMN v_voice_secretaries.stream_buffer_size IS 
'mod_audio_stream buffer duration in MILLISECONDS (not samples!). Default: 20ms. Higher = more stable but higher latency.';

-- Verificar correção
SELECT 
    voice_secretary_uuid,
    secretary_name,
    stream_buffer_size,
    CASE 
        WHEN stream_buffer_size = 20 THEN 'OK (20ms)'
        WHEN stream_buffer_size < 20 THEN 'WARNING: too low'
        WHEN stream_buffer_size > 100 THEN 'WARNING: too high latency'
        ELSE 'OK'
    END as status
FROM v_voice_secretaries;


-- =============================================================================
-- Migration: 012_create_voice_transfer_destinations.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 013_add_transfer_fields_to_secretaries.sql
-- =============================================================================

-- ============================================================================
-- Migration: 013_add_transfer_fields_to_secretaries.sql
-- Description: Adiciona campos de configuração de transfer na tabela v_voice_secretaries
-- Author: Claude AI + Juliano Targa
-- Created: 2026-01-16
-- Status: IDEMPOTENT - Seguro para rodar múltiplas vezes
-- ============================================================================

-- ============================================================================
-- NOVOS CAMPOS EM v_voice_secretaries
-- ============================================================================

-- transfer_enabled: Habilitar/desabilitar funcionalidade de transfer
ALTER TABLE v_voice_secretaries 
ADD COLUMN IF NOT EXISTS transfer_enabled BOOLEAN DEFAULT true;

COMMENT ON COLUMN v_voice_secretaries.transfer_enabled IS 
    'Habilita ou desabilita a funcionalidade de transferência de chamadas';

-- transfer_default_timeout: Timeout padrão para tentativas de transfer (segundos)
ALTER TABLE v_voice_secretaries 
ADD COLUMN IF NOT EXISTS transfer_default_timeout INT DEFAULT 30;

COMMENT ON COLUMN v_voice_secretaries.transfer_default_timeout IS 
    'Tempo máximo em segundos para aguardar atendimento do ramal de destino';

-- transfer_announce_enabled: Anunciar transferência antes de iniciar
ALTER TABLE v_voice_secretaries 
ADD COLUMN IF NOT EXISTS transfer_announce_enabled BOOLEAN DEFAULT true;

COMMENT ON COLUMN v_voice_secretaries.transfer_announce_enabled IS 
    'Se true, o agente anuncia "Transferindo para X..." antes de iniciar';

-- transfer_max_retries: Número máximo de tentativas de retry
ALTER TABLE v_voice_secretaries 
ADD COLUMN IF NOT EXISTS transfer_max_retries INT DEFAULT 1;

COMMENT ON COLUMN v_voice_secretaries.transfer_max_retries IS 
    'Número máximo de tentativas de retry quando transfer falha';

-- transfer_music_on_hold: Caminho para música de espera durante transfer
ALTER TABLE v_voice_secretaries 
ADD COLUMN IF NOT EXISTS transfer_music_on_hold VARCHAR(255) DEFAULT 'local_stream://moh';

COMMENT ON COLUMN v_voice_secretaries.transfer_music_on_hold IS 
    'Caminho para stream de música de espera durante transferência';

-- callback_enabled: Habilitar sistema de callback
ALTER TABLE v_voice_secretaries 
ADD COLUMN IF NOT EXISTS callback_enabled BOOLEAN DEFAULT true;

COMMENT ON COLUMN v_voice_secretaries.callback_enabled IS 
    'Habilita oferecimento de callback quando destino indisponível';

-- callback_ask_whatsapp: Perguntar se quer receber confirmação por WhatsApp
ALTER TABLE v_voice_secretaries 
ADD COLUMN IF NOT EXISTS callback_ask_whatsapp BOOLEAN DEFAULT false;

COMMENT ON COLUMN v_voice_secretaries.callback_ask_whatsapp IS 
    'Se true, oferece envio de confirmação via WhatsApp';

-- ============================================================================
-- VALORES DEFAULT PARA REGISTROS EXISTENTES
-- ============================================================================

UPDATE v_voice_secretaries 
SET 
    transfer_enabled = COALESCE(transfer_enabled, true),
    transfer_default_timeout = COALESCE(transfer_default_timeout, 30),
    transfer_announce_enabled = COALESCE(transfer_announce_enabled, true),
    transfer_max_retries = COALESCE(transfer_max_retries, 1),
    transfer_music_on_hold = COALESCE(transfer_music_on_hold, 'local_stream://moh'),
    callback_enabled = COALESCE(callback_enabled, true),
    callback_ask_whatsapp = COALESCE(callback_ask_whatsapp, false)
WHERE 1=1;

-- ============================================================================
-- LOG DE EXECUÇÃO
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration 013_add_transfer_fields_to_secretaries.sql executada com sucesso em %', NOW();
END $$;


-- =============================================================================
-- Migration: 016_add_fallback_options.sql
-- =============================================================================

-- Migration: Add Fallback Options to v_voice_secretaries
-- Ref: voice-ai-ivr/docs/TRANSFER_SETTINGS_VS_RULES.md
-- 
-- Adiciona opções de fallback quando a transferência falha:
-- - fallback_action: O que fazer (ticket, callback, voicemail, none)
-- - fallback_user_id: Usuário padrão para atribuir tickets (opcional)
-- - Mantém handoff_queue_id existente para a fila

-- Fallback action type
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'fallback_action'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN fallback_action VARCHAR(20) DEFAULT 'ticket';
        COMMENT ON COLUMN v_voice_secretaries.fallback_action IS 'Fallback action: ticket, callback, voicemail, none';
    END IF;
END $$;

-- Fallback user ID (OmniPlay user to assign tickets)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'fallback_user_id'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN fallback_user_id INTEGER;
        COMMENT ON COLUMN v_voice_secretaries.fallback_user_id IS 'OmniPlay user ID to assign tickets (optional, for specific routing)';
    END IF;
END $$;

-- Fallback priority (ticket priority: low, medium, high, urgent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'fallback_priority'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN fallback_priority VARCHAR(10) DEFAULT 'medium';
        COMMENT ON COLUMN v_voice_secretaries.fallback_priority IS 'Ticket priority: low, medium, high, urgent';
    END IF;
END $$;

-- Fallback notification enabled (notify via WhatsApp/Email when ticket created)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'fallback_notify_enabled'
    ) THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN fallback_notify_enabled BOOLEAN DEFAULT true;
        COMMENT ON COLUMN v_voice_secretaries.fallback_notify_enabled IS 'Send notification when fallback ticket is created';
    END IF;
END $$;

SELECT 'Migration 016_add_fallback_options completed successfully' AS status;


-- =============================================================================
-- Migration: 017_create_omniplay_integration_tables.sql
-- =============================================================================

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


-- =============================================================================
-- Migration: 021_insert_realtime_providers.sql
-- =============================================================================

-- Migration: Insert Realtime AI providers
-- Version: 021
-- Description: Insere providers realtime (OpenAI Realtime, ElevenLabs Conversational, Gemini Live)
-- 
-- NOTA: Esta migration cria providers de tipo 'realtime' que são usados pelo Voice AI IVR
-- para conversação em tempo real via WebSocket.
--
-- Providers disponíveis:
-- - elevenlabs_conversational: ElevenLabs Conversational AI (16kHz, sem resampling)
-- - openai_realtime: OpenAI Realtime API (24kHz, requer resampling)
-- - gemini_live: Google Gemini Live (experimental)

-- =====================================================================
-- FUNÇÃO: Criar providers realtime para um domínio
-- =====================================================================

CREATE OR REPLACE FUNCTION create_realtime_voice_ai_providers(target_domain_uuid UUID)
RETURNS void AS $$
BEGIN
    -- =====================================================================
    -- REALTIME PROVIDERS (Voice AI IVR)
    -- =====================================================================
    
    -- ElevenLabs Conversational AI (recomendado para pt-BR)
    -- Vantagem: 16kHz nativo (mesmo que FreeSWITCH), vozes premium
    -- Configurar agent_id e api_key no config JSON
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid, 
        'realtime', 
        'elevenlabs_conversational', 
        'ElevenLabs Conversational AI',
        jsonb_build_object(
            'agent_id', '',
            'api_key', '',
            'use_agent_config', true,
            'allow_voice_id_override', false,
            'allow_prompt_override', false
        ),
        true,  -- Default para produção
        true
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        update_date = NOW();
    
    -- OpenAI Realtime API (GPT-Realtime)
    -- Vantagem: GPT-4o integrado, function calling nativo
    -- Desvantagem: 24kHz (precisa resampling de/para 16kHz)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid,
        'realtime',
        'openai_realtime',
        'OpenAI Realtime (GPT-Realtime)',
        jsonb_build_object(
            'api_key', '',
            'model', 'gpt-realtime',
            'voice', 'marin',
            'vad_threshold', 0.5,
            'silence_duration_ms', 500,
            'prefix_padding_ms', 300
        ),
        false,  -- Não é default (precisa configurar API key)
        true    -- Habilitado para seleção
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        update_date = NOW();
    
    -- Gemini Live (Google)
    -- Vantagem: Preço mais baixo
    -- Desvantagem: Experimental, menos vozes
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain_uuid,
        'realtime',
        'gemini_live',
        'Google Gemini Live',
        jsonb_build_object(
            'api_key', '',
            'model', 'gemini-2.0-flash-exp'
        ),
        false,
        false   -- Desabilitado por padrão (experimental)
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        update_date = NOW();

END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- PARA APLICAR: Execute em cada domínio que precisa de realtime providers
-- =====================================================================

-- Exemplo:
-- SELECT create_realtime_voice_ai_providers('seu-domain-uuid');

-- Ou para TODOS os domínios ativos:
-- DO $$
-- DECLARE
--     d RECORD;
-- BEGIN
--     FOR d IN SELECT domain_uuid FROM v_domains WHERE domain_enabled = true LOOP
--         PERFORM create_realtime_voice_ai_providers(d.domain_uuid);
--     END LOOP;
-- END $$;

COMMENT ON FUNCTION create_realtime_voice_ai_providers(UUID) IS 
'Cria providers realtime (ElevenLabs, OpenAI, Gemini) para Voice AI IVR. Idempotente.';


-- =============================================================================
-- Migration: 022_add_transfer_realtime_mode.sql
-- =============================================================================

-- Migration: Add transfer realtime mode configuration
-- Version: 022
-- Description: Adiciona configuração para usar OpenAI Realtime na transferência com humano
--
-- Quando ativado, o agente IA conversa por voz com o humano durante a transferência
-- em vez de apenas tocar um TTS. Isso permite:
-- - Humano responder por voz ("pode passar", "estou ocupado")
-- - Dar instruções ao agente ("diz que ligo em 5 minutos")
-- - Experiência mais natural e premium

-- Adicionar coluna para modo realtime na transferência
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS transfer_realtime_enabled BOOLEAN DEFAULT false;

-- Adicionar coluna para prompt do agente ao falar com humano
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS transfer_realtime_prompt TEXT;

-- Adicionar coluna para timeout de resposta do humano (segundos)
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS transfer_realtime_timeout INTEGER DEFAULT 15;

-- Comentários
COMMENT ON COLUMN v_voice_secretaries.transfer_realtime_enabled IS 
    'Quando true, usa OpenAI Realtime para conversar com humano durante transferência. Mais natural mas mais caro.';

COMMENT ON COLUMN v_voice_secretaries.transfer_realtime_prompt IS 
    'Prompt de sistema para o agente ao conversar com humano. Ex: "Você está anunciando uma ligação para um atendente..."';

COMMENT ON COLUMN v_voice_secretaries.transfer_realtime_timeout IS 
    'Timeout em segundos para o humano responder. Após esse tempo, assume aceite.';

-- Valor padrão para o prompt
UPDATE v_voice_secretaries
SET transfer_realtime_prompt = 'Você está anunciando uma ligação para um atendente humano.
Informe quem está ligando e o motivo.
Se o humano disser que pode atender, diga "conectando" e encerre.
Se disser que não pode, pergunte se quer deixar recado.
Seja breve e objetivo.'
WHERE transfer_realtime_prompt IS NULL;


-- =============================================================================
-- Migration: 023_add_vad_guardrails_fields.sql
-- =============================================================================

-- Migration: Add VAD and Guardrails configuration fields
-- Version: 023
-- Description: Adiciona campos para configuração de VAD (semantic_vad vs server_vad) e Guardrails
--
-- IDEMPOTENTE: Usa IF NOT EXISTS para todas as alterações

-- =====================================================
-- VAD (Voice Activity Detection) Configuration
-- =====================================================

-- Tipo de VAD: 'server_vad' (silêncio) ou 'semantic_vad' (semântico/inteligente)
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS vad_type VARCHAR(20) DEFAULT 'semantic_vad';

-- Eagerness para semantic_vad: 'low', 'medium', 'high'
-- - low: Paciente, espera pausas longas
-- - medium: Balanceado (recomendado para pt-BR)
-- - high: Responde rápido, pode interromper
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS vad_eagerness VARCHAR(10) DEFAULT 'medium';

-- =====================================================
-- Guardrails Configuration
-- =====================================================

-- Habilitar guardrails (regras de segurança no prompt)
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS guardrails_enabled BOOLEAN DEFAULT true;

-- Tópicos proibidos customizados (texto livre, um por linha)
-- Ex: "política\nreligião\nconcorrentes"
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS guardrails_topics TEXT;

-- =====================================================
-- Announcement TTS Configuration
-- =====================================================

-- Provider TTS para anúncios de transferência: 'elevenlabs' ou 'openai'
-- OpenAI é mais barato mas ElevenLabs tem melhor qualidade
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS announcement_tts_provider VARCHAR(20) DEFAULT 'elevenlabs';

-- =====================================================
-- Comentários
-- =====================================================

COMMENT ON COLUMN v_voice_secretaries.vad_type IS 
    'Tipo de VAD: server_vad (silêncio) ou semantic_vad (semântico). semantic_vad é mais inteligente e recomendado.';

COMMENT ON COLUMN v_voice_secretaries.vad_eagerness IS 
    'Eagerness para semantic_vad: low (paciente), medium (balanceado), high (rápido). Afeta quando o agente responde.';

COMMENT ON COLUMN v_voice_secretaries.guardrails_enabled IS 
    'Quando true, adiciona regras de segurança ao prompt (não revelar instruções, manter escopo, etc).';

COMMENT ON COLUMN v_voice_secretaries.guardrails_topics IS 
    'Tópicos proibidos customizados (um por linha). Ex: política, religião, concorrentes.';

COMMENT ON COLUMN v_voice_secretaries.announcement_tts_provider IS 
    'Provider TTS para anúncios de transferência: elevenlabs (melhor qualidade) ou openai (mais barato).';

-- =====================================================
-- Valores padrão para registros existentes
-- =====================================================

-- Atualizar registros que ainda não têm vad_type definido
UPDATE v_voice_secretaries
SET vad_type = 'semantic_vad'
WHERE vad_type IS NULL;

UPDATE v_voice_secretaries
SET vad_eagerness = 'medium'
WHERE vad_eagerness IS NULL;

UPDATE v_voice_secretaries
SET guardrails_enabled = true
WHERE guardrails_enabled IS NULL;

UPDATE v_voice_secretaries
SET announcement_tts_provider = 'elevenlabs'
WHERE announcement_tts_provider IS NULL;


-- =============================================================================
-- Migration: 024_add_callstate_audio_input_fields.sql
-- =============================================================================

-- Migration: Add CallState, Input Normalization, and Silence Fallback fields
-- Version: 024
-- Description: Configurações por secretária para state machine, normalização e fallback de silêncio
--
-- IDEMPOTENTE: Usa IF NOT EXISTS

-- =====================================================
-- Input Audio Normalization
-- =====================================================
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_normalize_enabled BOOLEAN DEFAULT false;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_target_rms INTEGER DEFAULT 2000;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_min_rms INTEGER DEFAULT 300;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS input_max_gain DECIMAL(4,2) DEFAULT 3.0;

-- =====================================================
-- Call State Logging / Metrics
-- =====================================================
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS call_state_log_enabled BOOLEAN DEFAULT true;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS call_state_metrics_enabled BOOLEAN DEFAULT true;

-- =====================================================
-- Silence Fallback (State Machine)
-- =====================================================
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_enabled BOOLEAN DEFAULT false;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_seconds INTEGER DEFAULT 10;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_action VARCHAR(20) DEFAULT 'reprompt';

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_prompt TEXT;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS silence_fallback_max_retries INTEGER DEFAULT 2;

-- =====================================================
-- Comentários
-- =====================================================
COMMENT ON COLUMN v_voice_secretaries.input_normalize_enabled IS 'Habilita normalização de áudio de entrada (ganho limitado).';
COMMENT ON COLUMN v_voice_secretaries.input_target_rms IS 'RMS alvo para normalização do áudio de entrada.';
COMMENT ON COLUMN v_voice_secretaries.input_min_rms IS 'RMS mínimo para aplicar normalização.';
COMMENT ON COLUMN v_voice_secretaries.input_max_gain IS 'Ganho máximo permitido para normalização.';

COMMENT ON COLUMN v_voice_secretaries.call_state_log_enabled IS 'Habilita logs de transição de CallState.';
COMMENT ON COLUMN v_voice_secretaries.call_state_metrics_enabled IS 'Habilita métricas de transição de CallState.';

COMMENT ON COLUMN v_voice_secretaries.silence_fallback_enabled IS 'Habilita fallback de silêncio no state machine.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_seconds IS 'Tempo de silêncio (segundos) para acionar fallback.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_action IS 'Ação no fallback de silêncio: reprompt ou hangup.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_prompt IS 'Mensagem para reprompt no fallback de silêncio.';
COMMENT ON COLUMN v_voice_secretaries.silence_fallback_max_retries IS 'Número máximo de reprompts antes de encerrar.';

-- =====================================================
-- Valores padrão para registros existentes
-- =====================================================
UPDATE v_voice_secretaries
SET input_normalize_enabled = false
WHERE input_normalize_enabled IS NULL;

UPDATE v_voice_secretaries
SET input_target_rms = 2000
WHERE input_target_rms IS NULL;

UPDATE v_voice_secretaries
SET input_min_rms = 300
WHERE input_min_rms IS NULL;

UPDATE v_voice_secretaries
SET input_max_gain = 3.0
WHERE input_max_gain IS NULL;

UPDATE v_voice_secretaries
SET call_state_log_enabled = true
WHERE call_state_log_enabled IS NULL;

UPDATE v_voice_secretaries
SET call_state_metrics_enabled = true
WHERE call_state_metrics_enabled IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_enabled = false
WHERE silence_fallback_enabled IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_seconds = 10
WHERE silence_fallback_seconds IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_action = 'reprompt'
WHERE silence_fallback_action IS NULL;

UPDATE v_voice_secretaries
SET silence_fallback_max_retries = 2
WHERE silence_fallback_max_retries IS NULL;


-- =============================================================================
-- Migration: 025_add_handoff_tool_fallback.sql
-- =============================================================================

-- Migration: Add handoff tool fallback fields
-- Version: 025
-- Description: Configura fallback para request_handoff quando LLM não chama a tool
--
-- IDEMPOTENTE

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS handoff_tool_fallback_enabled BOOLEAN DEFAULT true;

ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS handoff_tool_timeout_seconds INTEGER DEFAULT 3;

COMMENT ON COLUMN v_voice_secretaries.handoff_tool_fallback_enabled IS
    'Habilita fallback automático se LLM não chamar request_handoff.';

COMMENT ON COLUMN v_voice_secretaries.handoff_tool_timeout_seconds IS
    'Tempo em segundos para esperar o tool request_handoff antes de forçar transferência.';

UPDATE v_voice_secretaries
SET handoff_tool_fallback_enabled = true
WHERE handoff_tool_fallback_enabled IS NULL;

UPDATE v_voice_secretaries
SET handoff_tool_timeout_seconds = 3
WHERE handoff_tool_timeout_seconds IS NULL;


-- =============================================================================
-- Migration: 026_add_domain_uuid_to_document_chunks.sql
-- =============================================================================

-- ============================================
-- Migration 026: Adicionar domain_uuid em v_voice_document_chunks
-- Voice AI IVR - Multi-tenant compliance
--
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- 1) Adicionar coluna domain_uuid se não existir
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'v_voice_document_chunks'
          AND column_name = 'domain_uuid'
    ) THEN
        ALTER TABLE v_voice_document_chunks
            ADD COLUMN domain_uuid UUID;
    END IF;
END $$;

-- 1.1) Adicionar coluna metadata se não existir (compatibilidade pgvector store)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'v_voice_document_chunks'
          AND column_name = 'metadata'
    ) THEN
        ALTER TABLE v_voice_document_chunks
            ADD COLUMN metadata JSONB;
    END IF;
END $$;

-- 2) Backfill domain_uuid a partir dos documentos
UPDATE v_voice_document_chunks c
SET domain_uuid = d.domain_uuid
FROM v_voice_documents d
WHERE c.voice_document_uuid = d.voice_document_uuid
  AND c.domain_uuid IS NULL;

-- 3) Adicionar FK para domains (se não existir)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'v_voice_document_chunks'
          AND constraint_name = 'fk_voice_document_chunks_domain'
    ) THEN
        ALTER TABLE v_voice_document_chunks
            ADD CONSTRAINT fk_voice_document_chunks_domain
            FOREIGN KEY (domain_uuid)
            REFERENCES v_domains(domain_uuid)
            ON DELETE CASCADE;
    END IF;
END $$;

-- 4) Tornar domain_uuid NOT NULL se não houver nulos
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM v_voice_document_chunks WHERE domain_uuid IS NULL LIMIT 1
    ) THEN
        ALTER TABLE v_voice_document_chunks
            ALTER COLUMN domain_uuid SET NOT NULL;
    END IF;
END $$;

-- 5) Índices para performance
CREATE INDEX IF NOT EXISTS idx_voice_document_chunks_domain
    ON v_voice_document_chunks(domain_uuid);
CREATE INDEX IF NOT EXISTS idx_voice_document_chunks_domain_document
    ON v_voice_document_chunks(domain_uuid, voice_document_uuid);


-- =============================================================================
-- Migration: 027_add_unbridge_behavior_fields.sql
-- =============================================================================

-- ============================================
-- Migration 027: Campos de comportamento pós-unbridge
-- Voice AI IVR - Multi-tenant safe
-- ============================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'unbridge_behavior') 
    THEN
        ALTER TABLE v_voice_secretaries
            ADD COLUMN unbridge_behavior VARCHAR(20) DEFAULT 'hangup';
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'unbridge_resume_message') 
    THEN
        ALTER TABLE v_voice_secretaries
            ADD COLUMN unbridge_resume_message TEXT;
    END IF;
END $$;


-- =============================================================================
-- Migration: 028_add_ptt_fields.sql
-- =============================================================================

-- ============================================
-- Migration 028: Campos de Push-to-Talk (VAD disabled)
-- Voice AI IVR - Multi-tenant safe
-- ============================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'ptt_rms_threshold') 
    THEN
        ALTER TABLE v_voice_secretaries
            ADD COLUMN ptt_rms_threshold INTEGER;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'ptt_hits') 
    THEN
        ALTER TABLE v_voice_secretaries
            ADD COLUMN ptt_hits INTEGER;
    END IF;
END $$;


-- =============================================================================
-- Migration: 029_enable_pgvector.sql
-- =============================================================================

-- ============================================
-- Migration 029: Habilitar pgvector e preparar coluna embedding
-- Voice AI IVR - RAG
-- ============================================

-- 1) Habilitar extensão pgvector (se disponível)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2) Converter coluna embedding para vector se estiver vazia
DO $$
DECLARE
    col_udt TEXT;
    has_data BOOLEAN;
BEGIN
    SELECT udt_name INTO col_udt
    FROM information_schema.columns
    WHERE table_name = 'v_voice_document_chunks'
      AND column_name = 'embedding'
    LIMIT 1;
    
    IF col_udt IS NOT NULL AND col_udt <> 'vector' THEN
        SELECT EXISTS(
            SELECT 1 FROM v_voice_document_chunks WHERE embedding IS NOT NULL LIMIT 1
        ) INTO has_data;
        
        IF has_data = false THEN
            -- Sem dados: converter coluna para vector
            ALTER TABLE v_voice_document_chunks
                ALTER COLUMN embedding TYPE vector USING NULL::vector;
        END IF;
    END IF;
END $$;


-- =============================================================================
-- Migration: 030_fix_and_enable_gemini_live.sql
-- =============================================================================

-- ============================================
-- Migration 030: Fix and Enable Gemini Live
-- 
-- Corrige a coluna enabled -> is_enabled na migration anterior
-- e habilita o provider Gemini Live para todos os domains.
--
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- =====================================================================
-- FUNÇÃO: Criar/Atualizar provider Gemini Live para um domínio
-- =====================================================================

CREATE OR REPLACE FUNCTION create_or_enable_gemini_live(target_domain_uuid UUID)
RETURNS void AS $$
BEGIN
    -- Gemini Live (Google)
    -- Agora usando is_enabled (nome correto da coluna)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, is_enabled
    )
    VALUES (
        target_domain_uuid,
        'realtime',
        'gemini_live',
        'Google Gemini Live',
        jsonb_build_object(
            'api_key', COALESCE(current_setting('app.google_api_key', true), ''),
            'model', 'gemini-2.0-flash-exp'
        ),
        false,
        true   -- ✅ HABILITADO
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO UPDATE
    SET is_enabled = true,  -- Habilitar se já existe
        display_name = EXCLUDED.display_name,
        update_date = NOW();
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- Habilitar Gemini Live para TODOS os domínios que já têm o provider
-- =====================================================================

DO $$
DECLARE
    d RECORD;
    count_updated INTEGER := 0;
    secretaries_exist BOOLEAN := false;
BEGIN
    -- Atualizar para domínios que já têm o provider (apenas habilitar)
    UPDATE v_voice_ai_providers 
    SET is_enabled = true, update_date = NOW()
    WHERE provider_name = 'gemini_live' 
      AND provider_type = 'realtime'
      AND is_enabled = false;
    
    GET DIAGNOSTICS count_updated = ROW_COUNT;
    RAISE NOTICE 'Gemini Live habilitado para % domínios existentes', count_updated;
    
    -- Verificar se tabela de secretárias existe antes de tentar usá-la
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'v_voice_ai_secretaries'
    ) INTO secretaries_exist;
    
    IF secretaries_exist THEN
        -- Criar para domínios que têm secretárias mas não têm o provider
        FOR d IN 
            SELECT DISTINCT s.domain_uuid 
            FROM v_voice_ai_secretaries s
            WHERE NOT EXISTS (
                SELECT 1 FROM v_voice_ai_providers p 
                WHERE p.domain_uuid = s.domain_uuid 
                  AND p.provider_name = 'gemini_live'
                  AND p.provider_type = 'realtime'
            )
        LOOP
            PERFORM create_or_enable_gemini_live(d.domain_uuid);
            count_updated := count_updated + 1;
        END LOOP;
    ELSE
        RAISE NOTICE 'Tabela v_voice_ai_secretaries não existe, pulando criação para novos domínios';
    END IF;
    
    RAISE NOTICE 'Total: Gemini Live configurado para % domínios', count_updated;
END $$;

-- =====================================================================
-- ALTERNATIVA: Para configurar com API Key específica
-- =====================================================================

-- Se você quiser configurar a API Key do Google para um domínio específico:
--
-- UPDATE v_voice_ai_providers 
-- SET config = jsonb_set(config, '{api_key}', '"SUA_GOOGLE_API_KEY_AQUI"')
-- WHERE domain_uuid = 'SEU-DOMAIN-UUID'
--   AND provider_name = 'gemini_live'
--   AND provider_type = 'realtime';
--
-- Ou via variável de ambiente:
-- SET app.google_api_key = 'SUA_GOOGLE_API_KEY';
-- SELECT create_or_enable_gemini_live('SEU-DOMAIN-UUID');

COMMENT ON FUNCTION create_or_enable_gemini_live(UUID) IS 
'Cria ou habilita provider Gemini Live para Voice AI IVR. Idempotente.';


-- =============================================================================
-- Migration: 031_add_business_info_field.sql
-- =============================================================================

-- ============================================
-- Migration 031: Adicionar campo business_info
-- 
-- Permite configurar informações da empresa
-- que a IA usa para responder perguntas.
--
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- Adicionar coluna business_info (JSONB) para informações da empresa
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'business_info'
    ) THEN
        ALTER TABLE v_voice_secretaries 
        ADD COLUMN business_info JSONB DEFAULT '{}'::jsonb;
        
        COMMENT ON COLUMN v_voice_secretaries.business_info IS 
            'Informações da empresa em JSON: servicos, horarios, localizacao, contato, precos, etc.';
    END IF;
END $$;

-- Exemplo de estrutura do business_info:
-- {
--   "servicos": "Internet fibra 100MB, 200MB, 500MB...",
--   "horarios": "Segunda a sexta, 8h às 18h",
--   "localizacao": "Rua Example, 123 - São Paulo",
--   "contato": "WhatsApp: (11) 99999-9999, Email: contato@empresa.com",
--   "precos": "100MB: R$99, 200MB: R$149, 500MB: R$199",
--   "promocoes": "Primeira mensalidade grátis para novos clientes",
--   "sobre": "Empresa fundada em 2010, líder em telecomunicações..."
-- }

-- Criar índice para busca no JSONB (opcional, melhora performance)
CREATE INDEX IF NOT EXISTS idx_secretaries_business_info 
    ON v_voice_secretaries USING gin (business_info);


-- =============================================================================
-- Migration: 032_add_hold_return_message.sql
-- =============================================================================

-- ============================================
-- Migration 032: Adicionar campo hold_return_message
-- 
-- Mensagem que a IA fala ao retirar cliente do hold
-- Ex: "Obrigado por aguardar."
--
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'hold_return_message'
    ) THEN
        ALTER TABLE v_voice_secretaries 
        ADD COLUMN hold_return_message TEXT DEFAULT 'Obrigado por aguardar.';
        
        COMMENT ON COLUMN v_voice_secretaries.hold_return_message IS 
            'Mensagem que a IA fala ao retirar o cliente do hold. Ex: Obrigado por aguardar.';
    END IF;
END $$;

-- ==============================================================================
-- FIM DAS MIGRATIONS
-- ==============================================================================
