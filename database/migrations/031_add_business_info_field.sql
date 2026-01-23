-- ============================================
-- Migration 031: Adicionar campo business_info
-- 
-- Permite configurar informações da empresa
-- que a IA usa para responder perguntas.
--
-- ⚠️ IDEMPOTENTE: Pode ser executada múltiplas vezes
-- ============================================

-- Adicionar coluna business_info (JSONB) para informações da empresa
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' 
        AND column_name = 'business_info'
    ) THEN
        ALTER TABLE v_voice_secretaries 
        ADD COLUMN business_info JSONB DEFAULT '{}'::jsonb;
        
        COMMENT ON COLUMN v_voice_secretaries.business_info IS 
            'Informações da empresa em JSON: servicos, horarios, localizacao, contato, precos, etc.';
    END IF;
END $$;

-- Exemplo de estrutura do business_info:
-- {
--   "servicos": "Internet fibra 100MB, 200MB, 500MB...",
--   "horarios": "Segunda a sexta, 8h às 18h",
--   "localizacao": "Rua Example, 123 - São Paulo",
--   "contato": "WhatsApp: (11) 99999-9999, Email: contato@empresa.com",
--   "precos": "100MB: R$99, 200MB: R$149, 500MB: R$199",
--   "promocoes": "Primeira mensalidade grátis para novos clientes",
--   "sobre": "Empresa fundada em 2010, líder em telecomunicações..."
-- }

-- Criar índice para busca no JSONB (opcional, melhora performance)
CREATE INDEX IF NOT EXISTS idx_secretaries_business_info 
    ON v_voice_secretaries USING gin (business_info);
