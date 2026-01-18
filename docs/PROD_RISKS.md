# Mapeamento de Riscos Críticos - Voice AI IVR

**Data:** 18/01/2026  
**Escopo:** voice-ai-ivr (realtime + handlers + RAG + integração PBX)

---

## 1) Realtime Announcement (B-leg) FULL-DUPLEX
**Risco:** necessidade de validar estabilidade do stream bidirecional (mod_audio_stream + WS).  
**Impacto:** se instável, pode gerar decisões erradas ou latência extra.  
**Mitigação aplicada:** stream full-duplex implementado com WS local e resample 16k↔24k.  
**Arquivo:** `realtime/handlers/realtime_announcement.py`.

---

## 2) Schema RAG inconsistente (document_chunks)
**Risco:** `v_voice_document_chunks` não tinha `domain_uuid` e `metadata` originalmente; código do PgVectorStore exige ambos.  
**Impacto:** falhas em produção ao inserir embeddings ou buscar contexto.  
**Correção aplicada:** migration `026_add_domain_uuid_to_document_chunks.sql` + ajustes no vector store.

---

## 3) Chat API sem config real da secretária
**Risco:** endpoint `/chat` respondia com config “default” e ignorava DB/tenant.  
**Impacto:** vazamento de comportamento entre tenants e respostas inconsistentes.  
**Correção aplicada:** `api/chat.py` carrega config por `domain_uuid` + `secretary_id`.

---

## 4) Gateway fixo no callback (default)
**Risco:** originate para cliente externo sempre via gateway `default`.  
**Impacto:** falhas para tenants com trunk/gateway próprio.  
**Correção aplicada:** `api/callback.py` usa `DEFAULT_GATEWAY` e prepara uso por settings do domínio.

---

## 5) Falhas silenciosas de pgvector
**Risco:** sem `CREATE EXTENSION vector`, o PgVectorStore falhava de forma indireta.  
**Impacto:** RAG sem contexto e erros difíceis de diagnosticar.  
**Correção aplicada:** verificação explícita do pgvector + tipo `embedding`.

---

## 6) Unbridge encerra chamada imediatamente
**Risco:** após unbridge (destino desligar), a sessão encerra a chamada sempre.  
**Impacto:** cliente pode ser derrubado em vez de retornar ao bot.  
**Mitigação aplicada:** comportamento configurável (resume/hangup) por secretária.  
**Arquivo:** `realtime/session.py` (`handle_unbridge`).

---

## 7) Hold sem pausa explícita de áudio
**Risco:** sem bloqueio, IA pode continuar processando áudio durante MOH.  
**Impacto:** respostas indevidas e custo extra.  
**Mitigação aplicada:** bloquear processamento quando `_on_hold` e interromper provider.  
**Arquivo:** `realtime/session.py`.

---

## 8) DND não integrado
**Risco:** TODO para integrar DND em sessão.  
**Impacto:** transferências para ramais em DND podem falhar e gerar má experiência.  
**Arquivo:** `realtime/session.py`.

---

## 9) Realtime Events - compatibilidade GA vs Beta
**Risco:** diferenças de formato `session.update` e nomes de eventos podem quebrar após mudanças de API.  
**Impacto:** perda de áudio, falha em VAD ou function calling.  
**Mitigação:** validar eventos em produção + monitorar logs e rate_limits.  
**Arquivo:** `realtime/providers/openai_realtime.py`.

---

## 10) Feriados não considerados
**Risco:** time_condition_checker ignora feriados por TODO.  
**Impacto:** transferências indevidas fora do horário real.  
**Mitigação aplicada:** carregar `v_time_condition_exceptions` (closed) com timezone.  
**Arquivo:** `realtime/handlers/time_condition_checker.py`.

---

## 11) Ferramentas/Integrações pendentes
**Risco:** function calling ainda possui TODOs (CRM/agenda).  
**Impacto:** ações do agente podem não refletir o real estado do negócio.  
**Arquivo:** `realtime/handlers/function_call.py`.

---

## 12) Riscos operacionais
**Risco:** custos e rate limits variam por modelo/voz/transcrição.  
**Impacto:** aumento inesperado de custo ou interrupção por limites.  
**Mitigação:** monitorar `rate_limits.updated`, uso de cached input, modelos mini.

