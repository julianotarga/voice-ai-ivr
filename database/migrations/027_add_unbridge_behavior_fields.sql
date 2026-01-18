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
