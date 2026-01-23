-- ============================================
-- Script de diagnóstico: business_info
-- 
-- Execute para verificar se a coluna existe e 
-- se os dados estão sendo salvos.
-- ============================================

-- 1. Verificar se a coluna business_info existe
SELECT 
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'v_voice_secretaries' 
            AND column_name = 'business_info'
        ) 
        THEN '✅ Coluna business_info EXISTE'
        ELSE '❌ Coluna business_info NÃO EXISTE - Execute a migration 031'
    END as status_coluna;

-- 2. Verificar se a coluna hold_return_message existe
SELECT 
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'v_voice_secretaries' 
            AND column_name = 'hold_return_message'
        ) 
        THEN '✅ Coluna hold_return_message EXISTE'
        ELSE '❌ Coluna hold_return_message NÃO EXISTE - Execute a migration 032'
    END as status_coluna;

-- 3. Mostrar secretárias com business_info preenchido
SELECT 
    voice_secretary_uuid,
    secretary_name,
    CASE 
        WHEN business_info IS NULL THEN '(null)'
        WHEN business_info::text = '{}' THEN '(vazio)'
        ELSE business_info::text
    END as business_info,
    CASE 
        WHEN hold_return_message IS NULL THEN '(null)'
        ELSE hold_return_message
    END as hold_return_message
FROM v_voice_secretaries
ORDER BY secretary_name;

-- 4. Se precisar forçar um valor para teste:
-- UPDATE v_voice_secretaries 
-- SET business_info = '{"servicos": "Internet 100MB, 200MB", "precos": "100MB: R$99", "promocoes": "Primeira mensalidade grátis"}'::jsonb
-- WHERE secretary_name = 'EVA';
