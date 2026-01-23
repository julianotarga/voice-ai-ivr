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
