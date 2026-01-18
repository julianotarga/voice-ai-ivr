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
