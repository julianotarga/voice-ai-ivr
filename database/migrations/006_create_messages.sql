-- Migration 006: Add additional columns and indexes to v_voice_messages
-- 
-- NOTE: v_voice_messages was created in 004_create_conversations.sql
-- This migration adds optional columns for detailed tracking
-- IDEMPOTENTE: Pode ser executada múltiplas vezes

-- =============================================
-- ADD OPTIONAL COLUMNS (if not exist)
-- =============================================

-- Transcription metadata
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'transcription_confidence') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN transcription_confidence DECIMAL(5,4);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'stt_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN stt_provider VARCHAR(50);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'stt_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN stt_latency_ms INTEGER;
    END IF;
END $$;

-- TTS metadata
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'tts_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN tts_provider VARCHAR(50);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'tts_voice') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN tts_voice VARCHAR(100);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'tts_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN tts_latency_ms INTEGER;
    END IF;
END $$;

-- LLM metadata
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'llm_provider') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN llm_provider VARCHAR(50);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'llm_model') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN llm_model VARCHAR(100);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'llm_latency_ms') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN llm_latency_ms INTEGER;
    END IF;
END $$;

-- Intent confidence
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_messages' AND column_name = 'intent_confidence') 
    THEN
        ALTER TABLE v_voice_messages ADD COLUMN intent_confidence DECIMAL(5,4);
    END IF;
END $$;

-- =============================================
-- INDEXES (using correct column names from 004)
-- =============================================

-- Role filter
CREATE INDEX IF NOT EXISTS idx_voice_messages_role 
    ON v_voice_messages(role);

-- Intent analysis
CREATE INDEX IF NOT EXISTS idx_voice_messages_intent 
    ON v_voice_messages(detected_intent) 
    WHERE detected_intent IS NOT NULL;

-- Time-based queries (using insert_date from 004)
CREATE INDEX IF NOT EXISTS idx_voice_messages_date 
    ON v_voice_messages(insert_date DESC);

-- =============================================
-- COMMENTS
-- =============================================

COMMENT ON TABLE v_voice_messages IS 'Individual messages in voice AI conversations';
