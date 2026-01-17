-- =====================================================================
-- SCRIPT: Testar troca para OpenAI Realtime
-- =====================================================================
-- 
-- INSTRUÇÕES:
-- 1. Execute este script no psql conectado ao banco fusionpbx
-- 2. Ajuste o domain_uuid e secretary_uuid conforme seu ambiente
-- 3. Configure a OPENAI_API_KEY no .env do voice-ai-service
-- 4. Reinicie o container: docker compose restart voice-ai-realtime
-- 5. Faça uma chamada de teste
--
-- PARA REVERTER: Execute a seção "ROLLBACK" no final deste arquivo
-- =====================================================================

-- =====================================================================
-- PASSO 1: Verificar domínio atual
-- =====================================================================

-- Listar todos os domínios
SELECT 
    domain_uuid,
    domain_name,
    domain_enabled
FROM v_domains
WHERE domain_enabled = true
ORDER BY domain_name;

-- =====================================================================
-- PASSO 2: Verificar secretária atual e provider
-- =====================================================================

-- Listar secretárias e seus providers
SELECT 
    s.voice_secretary_uuid,
    s.secretary_name,
    s.extension,
    p.provider_name as current_provider,
    p.display_name as provider_display,
    s.enabled
FROM v_voice_secretaries s
LEFT JOIN v_voice_ai_providers p ON p.voice_ai_provider_uuid = s.realtime_provider_uuid
WHERE s.enabled = true
ORDER BY s.secretary_name;

-- =====================================================================
-- PASSO 3: Listar providers realtime disponíveis
-- =====================================================================

SELECT 
    voice_ai_provider_uuid,
    domain_uuid,
    provider_type,
    provider_name,
    display_name,
    enabled,
    is_default
FROM v_voice_ai_providers
WHERE provider_type = 'realtime'
ORDER BY domain_uuid, provider_name;

-- =====================================================================
-- PASSO 4: Criar providers realtime se não existirem
-- =====================================================================

-- Substitua 'SEU_DOMAIN_UUID' pelo UUID real do seu domínio
-- Você pode pegar do PASSO 1

DO $$
DECLARE
    target_domain UUID := 'SEU_DOMAIN_UUID'::uuid;  -- <<< ALTERE AQUI
BEGIN
    -- Criar ElevenLabs Conversational
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain, 
        'realtime', 
        'elevenlabs_conversational', 
        'ElevenLabs Conversational AI',
        '{"use_agent_config": true}'::jsonb,
        true,
        true
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    -- Criar OpenAI Realtime
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, enabled
    )
    VALUES (
        target_domain,
        'realtime',
        'openai_realtime',
        'OpenAI Realtime (GPT-Realtime)',
        '{
            "model": "gpt-realtime",
            "voice": "marin",
            "vad_threshold": 0.5,
            "silence_duration_ms": 500,
            "prefix_padding_ms": 300
        }'::jsonb,
        false,
        true
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO NOTHING;
    
    RAISE NOTICE 'Providers realtime criados para domain %', target_domain;
END $$;

-- =====================================================================
-- PASSO 5: TROCAR SECRETÁRIA PARA OPENAI
-- =====================================================================

-- Substitua 'SEU_SECRETARY_UUID' e 'SEU_DOMAIN_UUID' pelos valores reais

UPDATE v_voice_secretaries
SET realtime_provider_uuid = (
    SELECT voice_ai_provider_uuid 
    FROM v_voice_ai_providers 
    WHERE domain_uuid = 'SEU_DOMAIN_UUID'::uuid  -- <<< ALTERE AQUI
      AND provider_type = 'realtime'
      AND provider_name = 'openai_realtime'
    LIMIT 1
)
WHERE voice_secretary_uuid = 'SEU_SECRETARY_UUID'::uuid;  -- <<< ALTERE AQUI

-- Verificar mudança
SELECT 
    s.secretary_name,
    p.provider_name as new_provider,
    p.config
FROM v_voice_secretaries s
JOIN v_voice_ai_providers p ON p.voice_ai_provider_uuid = s.realtime_provider_uuid
WHERE s.voice_secretary_uuid = 'SEU_SECRETARY_UUID'::uuid;  -- <<< ALTERE AQUI

-- =====================================================================
-- PASSO 6: Verificar .env tem OPENAI_API_KEY
-- =====================================================================

-- No servidor, execute:
-- grep OPENAI_API_KEY /caminho/para/voice-ai-ivr/.env
-- 
-- Se não tiver, adicione:
-- OPENAI_API_KEY=sk-proj-XXXXXXXX

-- =====================================================================
-- PASSO 7: Reiniciar container
-- =====================================================================

-- docker compose restart voice-ai-realtime
-- docker compose logs -f voice-ai-realtime

-- =====================================================================
-- PASSO 8: Testar chamada
-- =====================================================================

-- Ligue para o ramal da secretária e observe os logs
-- Deve aparecer: "Creating realtime provider" com "openai_realtime"

-- =====================================================================
-- ROLLBACK: Reverter para ElevenLabs
-- =====================================================================

-- UPDATE v_voice_secretaries
-- SET realtime_provider_uuid = (
--     SELECT voice_ai_provider_uuid 
--     FROM v_voice_ai_providers 
--     WHERE domain_uuid = 'SEU_DOMAIN_UUID'::uuid
--       AND provider_type = 'realtime'
--       AND provider_name = 'elevenlabs_conversational'
--     LIMIT 1
-- )
-- WHERE voice_secretary_uuid = 'SEU_SECRETARY_UUID'::uuid;

-- =====================================================================
-- FIM
-- =====================================================================
