-- ============================================
-- Migration 030: Fix and Enable Gemini Live
-- 
-- Corrige a coluna enabled -> is_enabled na migration anterior
-- e habilita o provider Gemini Live para todos os domains.
--
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- =====================================================================
-- FUNÇÃO: Criar/Atualizar provider Gemini Live para um domínio
-- =====================================================================

CREATE OR REPLACE FUNCTION create_or_enable_gemini_live(target_domain_uuid UUID)
RETURNS void AS $$
BEGIN
    -- Gemini Live (Google)
    -- Agora usando is_enabled (nome correto da coluna)
    INSERT INTO v_voice_ai_providers (
        domain_uuid, provider_type, provider_name, display_name,
        config, is_default, is_enabled
    )
    VALUES (
        target_domain_uuid,
        'realtime',
        'gemini_live',
        'Google Gemini Live',
        jsonb_build_object(
            'api_key', COALESCE(current_setting('app.google_api_key', true), ''),
            'model', 'gemini-2.0-flash-exp'
        ),
        false,
        true   -- ✅ HABILITADO
    )
    ON CONFLICT (domain_uuid, provider_type, provider_name) DO UPDATE
    SET is_enabled = true,  -- Habilitar se já existe
        display_name = EXCLUDED.display_name,
        update_date = NOW();
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- Habilitar Gemini Live para TODOS os domínios que já têm o provider
-- =====================================================================

DO $$
DECLARE
    d RECORD;
    count_updated INTEGER := 0;
    secretaries_exist BOOLEAN := false;
BEGIN
    -- Atualizar para domínios que já têm o provider (apenas habilitar)
    UPDATE v_voice_ai_providers 
    SET is_enabled = true, update_date = NOW()
    WHERE provider_name = 'gemini_live' 
      AND provider_type = 'realtime'
      AND is_enabled = false;
    
    GET DIAGNOSTICS count_updated = ROW_COUNT;
    RAISE NOTICE 'Gemini Live habilitado para % domínios existentes', count_updated;
    
    -- Verificar se tabela de secretárias existe antes de tentar usá-la
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'v_voice_ai_secretaries'
    ) INTO secretaries_exist;
    
    IF secretaries_exist THEN
        -- Criar para domínios que têm secretárias mas não têm o provider
        FOR d IN 
            SELECT DISTINCT s.domain_uuid 
            FROM v_voice_ai_secretaries s
            WHERE NOT EXISTS (
                SELECT 1 FROM v_voice_ai_providers p 
                WHERE p.domain_uuid = s.domain_uuid 
                  AND p.provider_name = 'gemini_live'
                  AND p.provider_type = 'realtime'
            )
        LOOP
            PERFORM create_or_enable_gemini_live(d.domain_uuid);
            count_updated := count_updated + 1;
        END LOOP;
    ELSE
        RAISE NOTICE 'Tabela v_voice_ai_secretaries não existe, pulando criação para novos domínios';
    END IF;
    
    RAISE NOTICE 'Total: Gemini Live configurado para % domínios', count_updated;
END $$;

-- =====================================================================
-- ALTERNATIVA: Para configurar com API Key específica
-- =====================================================================

-- Se você quiser configurar a API Key do Google para um domínio específico:
--
-- UPDATE v_voice_ai_providers 
-- SET config = jsonb_set(config, '{api_key}', '"SUA_GOOGLE_API_KEY_AQUI"')
-- WHERE domain_uuid = 'SEU-DOMAIN-UUID'
--   AND provider_name = 'gemini_live'
--   AND provider_type = 'realtime';
--
-- Ou via variável de ambiente:
-- SET app.google_api_key = 'SUA_GOOGLE_API_KEY';
-- SELECT create_or_enable_gemini_live('SEU-DOMAIN-UUID');

COMMENT ON FUNCTION create_or_enable_gemini_live(UUID) IS 
'Cria ou habilita provider Gemini Live para Voice AI IVR. Idempotente.';
