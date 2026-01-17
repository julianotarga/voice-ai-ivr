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
