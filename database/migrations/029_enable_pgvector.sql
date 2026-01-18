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
