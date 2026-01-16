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
