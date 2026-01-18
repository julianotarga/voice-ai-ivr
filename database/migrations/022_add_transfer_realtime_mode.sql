-- Migration: Add transfer realtime mode configuration
-- Version: 022
-- Description: Adiciona configuração para usar OpenAI Realtime na transferência com humano
--
-- Quando ativado, o agente IA conversa por voz com o humano durante a transferência
-- em vez de apenas tocar um TTS. Isso permite:
-- - Humano responder por voz ("pode passar", "estou ocupado")
-- - Dar instruções ao agente ("diz que ligo em 5 minutos")
-- - Experiência mais natural e premium

-- Adicionar coluna para modo realtime na transferência
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS transfer_realtime_enabled BOOLEAN DEFAULT false;

-- Adicionar coluna para prompt do agente ao falar com humano
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS transfer_realtime_prompt TEXT;

-- Adicionar coluna para timeout de resposta do humano (segundos)
ALTER TABLE v_voice_secretaries
ADD COLUMN IF NOT EXISTS transfer_realtime_timeout INTEGER DEFAULT 15;

-- Comentários
COMMENT ON COLUMN v_voice_secretaries.transfer_realtime_enabled IS 
    'Quando true, usa OpenAI Realtime para conversar com humano durante transferência. Mais natural mas mais caro.';

COMMENT ON COLUMN v_voice_secretaries.transfer_realtime_prompt IS 
    'Prompt de sistema para o agente ao conversar com humano. Ex: "Você está anunciando uma ligação para um atendente..."';

COMMENT ON COLUMN v_voice_secretaries.transfer_realtime_timeout IS 
    'Timeout em segundos para o humano responder. Após esse tempo, assume aceite.';

-- Valor padrão para o prompt
UPDATE v_voice_secretaries
SET transfer_realtime_prompt = 'Você está anunciando uma ligação para um atendente humano.
Informe quem está ligando e o motivo.
Se o humano disser que pode atender, diga "conectando" e encerre.
Se disser que não pode, pergunte se quer deixar recado.
Seja breve e objetivo.'
WHERE transfer_realtime_prompt IS NULL;
