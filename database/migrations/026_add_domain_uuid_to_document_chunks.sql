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
