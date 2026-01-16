-- ============================================================================
-- Seed: 001_seed_transfer_destinations.sql
-- Description: Dados iniciais de exemplo para destinos de transferência
-- Author: Claude AI + Juliano Targa
-- Created: 2026-01-16
-- NOTA: Este seed deve ser customizado para cada ambiente/cliente
-- ============================================================================

-- ============================================================================
-- INSTRUÇÕES DE USO
-- ============================================================================
-- 
-- Este seed cria destinos de exemplo. Para usar em produção:
-- 1. Substitua 'YOUR-DOMAIN-UUID-HERE' pelo domain_uuid real
-- 2. Substitua 'YOUR-SECRETARY-UUID-HERE' pelo voice_secretary_uuid real
-- 3. Ajuste os ramais/números de acordo com sua configuração
--
-- Exemplo de como obter os UUIDs:
-- SELECT domain_uuid FROM v_domains WHERE domain_name = 'seudominio.com';
-- SELECT voice_secretary_uuid FROM v_voice_secretaries WHERE name = 'Secretária Principal';
-- ============================================================================

-- Função auxiliar para inserir apenas se não existir (por nome + domain)
-- Evita duplicatas em re-execuções

CREATE OR REPLACE FUNCTION insert_transfer_destination_if_not_exists(
    p_domain_uuid UUID,
    p_secretary_uuid UUID,
    p_name VARCHAR,
    p_aliases JSONB,
    p_destination_type VARCHAR,
    p_destination_number VARCHAR,
    p_ring_timeout_seconds INT,
    p_fallback_action VARCHAR,
    p_department VARCHAR,
    p_role VARCHAR,
    p_description TEXT,
    p_working_hours JSONB,
    p_priority INT,
    p_is_default BOOLEAN
) RETURNS VOID AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM v_voice_transfer_destinations 
        WHERE domain_uuid = p_domain_uuid AND name = p_name
    ) THEN
        INSERT INTO v_voice_transfer_destinations (
            domain_uuid,
            secretary_uuid,
            name,
            aliases,
            destination_type,
            destination_number,
            destination_context,
            ring_timeout_seconds,
            max_retries,
            retry_delay_seconds,
            fallback_action,
            department,
            role,
            description,
            working_hours,
            priority,
            is_enabled,
            is_default
        ) VALUES (
            p_domain_uuid,
            p_secretary_uuid,
            p_name,
            p_aliases,
            p_destination_type,
            p_destination_number,
            'default',
            p_ring_timeout_seconds,
            1,
            5,
            p_fallback_action,
            p_department,
            p_role,
            p_description,
            p_working_hours,
            p_priority,
            true,
            p_is_default
        );
        RAISE NOTICE 'Destino "%" criado com sucesso', p_name;
    ELSE
        RAISE NOTICE 'Destino "%" já existe, pulando...', p_name;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- EXEMPLO: INSERIR DESTINOS PADRÃO
-- Descomente e ajuste os UUIDs para seu ambiente
-- ============================================================================

/*
-- Variáveis de exemplo (substitua pelos UUIDs reais)
DO $$
DECLARE
    v_domain_uuid UUID := 'YOUR-DOMAIN-UUID-HERE';
    v_secretary_uuid UUID := 'YOUR-SECRETARY-UUID-HERE';
BEGIN

    -- ========================================================================
    -- DESTINO 1: Fila de Atendimento Geral (DEFAULT)
    -- ========================================================================
    PERFORM insert_transfer_destination_if_not_exists(
        v_domain_uuid,
        v_secretary_uuid,
        'Atendimento',
        '["atendimento", "atendente", "alguém", "pessoa", "humano", "operador", "falar com alguém"]'::jsonb,
        'ring_group',
        '9000',
        30,
        'offer_ticket',
        'Atendimento Geral',
        'Atendente',
        'Fila principal de atendimento. Destino padrão quando cliente pede para falar com alguém.',
        '{"start": "08:00", "end": "18:00", "days": [1,2,3,4,5], "timezone": "America/Sao_Paulo"}'::jsonb,
        100,
        true  -- IS_DEFAULT
    );

    -- ========================================================================
    -- DESTINO 2: Financeiro
    -- ========================================================================
    PERFORM insert_transfer_destination_if_not_exists(
        v_domain_uuid,
        v_secretary_uuid,
        'Jeni - Financeiro',
        '["jeni", "jeniffer", "jennifer", "financeiro", "contas", "boleto", "pagamento", "cobrança", "segunda via"]'::jsonb,
        'extension',
        '1004',
        25,
        'offer_ticket',
        'Financeiro',
        'Analista Financeiro',
        'Responsável por cobranças, boletos e questões financeiras.',
        '{"start": "08:00", "end": "17:00", "days": [1,2,3,4,5], "timezone": "America/Sao_Paulo"}'::jsonb,
        50,
        false
    );

    -- ========================================================================
    -- DESTINO 3: Suporte Técnico
    -- ========================================================================
    PERFORM insert_transfer_destination_if_not_exists(
        v_domain_uuid,
        v_secretary_uuid,
        'Suporte Técnico',
        '["suporte", "técnico", "problema", "internet", "conexão", "lento", "caiu", "não funciona", "erro"]'::jsonb,
        'queue',
        '5001',
        45,
        'create_ticket',  -- Suporte cria ticket automaticamente
        'Suporte',
        'Técnico',
        'Equipe de suporte técnico. Opera 24/7.',
        '{"start": "00:00", "end": "23:59", "days": [0,1,2,3,4,5,6], "timezone": "America/Sao_Paulo"}'::jsonb,
        75,
        false
    );

    -- ========================================================================
    -- DESTINO 4: Comercial
    -- ========================================================================
    PERFORM insert_transfer_destination_if_not_exists(
        v_domain_uuid,
        v_secretary_uuid,
        'Comercial',
        '["comercial", "vendas", "comprar", "novo plano", "upgrade", "contratar", "orçamento", "proposta"]'::jsonb,
        'ring_group',
        '9001',
        30,
        'offer_ticket',
        'Comercial',
        'Consultor de Vendas',
        'Equipe comercial para novos contratos e upgrades.',
        '{"start": "08:00", "end": "18:00", "days": [1,2,3,4,5], "timezone": "America/Sao_Paulo"}'::jsonb,
        60,
        false
    );

    RAISE NOTICE 'Seeds de destinos de transferência executados com sucesso!';
END $$;
*/

-- ============================================================================
-- LIMPAR FUNÇÃO AUXILIAR (OPCIONAL)
-- ============================================================================
-- DROP FUNCTION IF EXISTS insert_transfer_destination_if_not_exists;

-- ============================================================================
-- LOG DE EXECUÇÃO
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Seed 001_seed_transfer_destinations.sql carregado em %', NOW();
    RAISE NOTICE 'NOTA: Descomente e ajuste os UUIDs para inserir dados';
END $$;
