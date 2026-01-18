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
