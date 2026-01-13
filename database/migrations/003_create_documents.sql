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
