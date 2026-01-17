# Proposal: Attended Transfer com Anúncio

## Resumo

Implementar transferência assistida onde o agente de IA anuncia o cliente para o atendente humano antes de conectar, similar ao comportamento de uma secretária tradicional.

## Motivação

Atualmente, quando o cliente pede transferência:
1. O agente coloca em espera (MOH)
2. Liga para o ramal destino
3. Quando o humano atende, faz bridge IMEDIATO

**Problema**: O atendente não sabe quem está ligando ou o motivo, resultando em experiência ruim.

**Solução**: O agente anuncia para o humano antes de conectar:
- "Olá, tenho o Sr. João na linha. Ele quer falar sobre contratação de plano. Aceita a ligação?"

## Fluxo Proposto

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FLUXO: ANNOUNCED TRANSFER                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. Cliente: "Me transfere para vendas"                             │
│                      │                                              │
│                      ▼                                              │
│  2. Agente: "Um momento, vou verificar com o time de vendas"        │
│                      │                                              │
│                      ▼                                              │
│  3. MOH inicia no A-leg (cliente)                                   │
│                      │                                              │
│                      ▼                                              │
│  4. Originate B-leg (ramal do humano)                               │
│                      │                                              │
│         ┌───────────┴───────────┐                                   │
│         │                       │                                   │
│    Não Atende              Atende                                   │
│         │                       │                                   │
│         ▼                       ▼                                   │
│  Voltar ao cliente      5. ANÚNCIO via TTS:                         │
│  "Vendas não atendeu"      "Olá, tenho o Sr. João na linha,         │
│                             ele quer falar sobre plano de           │
│                             internet. Pressione 1 para aceitar      │
│                             ou 2 para recusar."                     │
│                                 │                                   │
│              ┌──────────────────┼──────────────────┐                │
│              │                  │                  │                │
│          DTMF 1            DTMF 2             Hangup/Timeout        │
│          (aceita)          (recusa)                │                │
│              │                  │                  │                │
│              ▼                  ▼                  ▼                │
│        6a. Bridge          6b. Matar B-leg    6c. Matar B-leg       │
│        A-leg ↔ B-leg           │                   │                │
│              │                  ▼                   │               │
│              │           Voltar ao cliente ◄────────┘               │
│              │           "Vendas não pode atender agora.            │
│              │            Quer deixar um recado?"                   │
│              │                  │                                   │
│              ▼                  ▼                                   │
│        7. Sessão          Fluxo de Recado/Callback                  │
│        encerra            (FASE 2)                                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Decisões Técnicas

### D1: Como gerar o TTS para o B-leg?

**Opção A**: FreeSWITCH `mod_say` (voz robótica)
```
uuid_broadcast {B-leg} say:pt-BR:Tenho o senhor João na linha aleg
```
- ✅ Simples, sem dependências externas
- ❌ Voz robótica, menos natural

**Opção B**: ElevenLabs TTS → arquivo → playback
```python
audio = await elevenlabs.generate_tts(announcement)
await upload_to_minio(audio, f"announcements/{uuid}.wav")
await esl.uuid_broadcast(b_leg, f"http://minio/announcements/{uuid}.wav")
```
- ✅ Voz natural, consistente com o agente
- ❌ Latência adicional (~500ms-1s)

**Opção C**: Cache de frases pré-geradas
- Gerar frases comuns offline: "Tenho um cliente na linha", "Aceita a ligação?"
- Apenas o nome do cliente é gerado em tempo real
- ✅ Latência baixa, voz natural
- ❌ Menos flexível

**Decisão**: **Opção A para MVP**, migrar para **Opção C** depois.

### D2: Como detectar resposta do humano?

**Opção A**: DTMF explícito
- "Pressione 1 para aceitar, 2 para recusar"
- ✅ Simples, confiável
- ❌ Menos natural

**Opção B**: Speech Recognition
- Detectar "sim/não" via ASR
- ✅ Natural
- ❌ Requer ASR no B-leg, complexo

**Opção C**: Timeout = aceitar
- Se humano não desligar em X segundos, assumir aceite
- ✅ Simples
- ❌ Pode conectar sem consentimento explícito

**Opção D**: Híbrida (Timeout + DTMF para recusar) ⭐
- "Olá, tenho um cliente na linha. Pressione 2 para recusar ou aguarde para aceitar."
- Timeout de 5 segundos → aceitar automaticamente
- DTMF 2 → recusar
- Hangup → recusar
- ✅ Natural (humano não precisa fazer nada para aceitar)
- ✅ Ainda permite recusar facilmente
- ✅ Simples de implementar

**Decisão**: **Opção D (Híbrida)** - mais natural para o atendente.

### D3: O que incluir no anúncio?

Informações disponíveis:
- Nome do cliente (se identificado)
- Número do telefone (caller_id)
- Motivo da ligação (extraído da conversa)
- Resumo da conversa (opcional)

**Template de anúncio**:
```
"Olá, tenho [nome ou número] na linha.
[Motivo se disponível].
Pressione 2 para recusar ou aguarde para aceitar."
```

**Exemplos**:
- "Olá, tenho o número 11999887766 na linha, sobre contratação de plano. Pressione 2 para recusar ou aguarde para aceitar."
- "Olá, tenho um cliente na linha. Aguarde para aceitar ou pressione 2 para recusar."

**Comportamento**:
- Humano **não faz nada** → após 5 segundos, conecta automaticamente
- Humano **pressiona 2** → recusa, agente volta ao cliente
- Humano **desliga** → recusa, agente volta ao cliente

### D4: Timeout e fallback

| Cenário | Ação |
|---------|------|
| Humano não atende (30s) | Voltar ao cliente: "Vendas não atendeu" |
| Humano atende mas não responde (15s) | Assumir aceite, fazer bridge |
| Humano pressiona 1 | Fazer bridge imediato |
| Humano pressiona 2 | Voltar ao cliente: "Vendas não pode atender" |
| Humano desliga | Voltar ao cliente: "Vendas não pode atender" |
| Cliente desliga durante espera | Encerrar tudo |

## Componentes a Modificar

### 1. `transfer_manager.py`

Novo método `execute_announced_transfer()`:
- Manter MOH no A-leg
- Originate B-leg
- Após CHANNEL_ANSWER, tocar anúncio via TTS
- Aguardar DTMF (1=aceitar, 2=recusar) ou timeout
- Se aceitar: bridge
- Se recusar: matar B-leg, retornar status

### 2. `esl_client.py`

Novos métodos:
- `uuid_recv_dtmf()`: Subscrever e aguardar DTMF
- `uuid_say()`: Wrapper para `mod_say`
- Ou: `uuid_broadcast()` já existe, usar com `say:pt-BR:...`

### 3. `session.py`

Modificar `_execute_intelligent_handoff()`:
- Construir texto de anúncio com contexto da conversa
- Chamar novo método `execute_announced_transfer()`
- Tratar resultado (SUCCESS, REJECTED, NO_ANSWER)

### 4. `RealtimeSessionConfig`

Novos campos:
```python
@dataclass
class RealtimeSessionConfig:
    # ... existentes ...
    
    # Announced Transfer
    transfer_announcement_enabled: bool = True
    transfer_announcement_voice: str = "pt-BR"  # Para mod_say
    transfer_announcement_timeout: int = 15  # Segundos para aguardar DTMF
    transfer_include_caller_name: bool = True
    transfer_include_reason: bool = True
```

## Dependências

- `mod_say` do FreeSWITCH (já instalado por padrão)
- Língua pt-BR para `mod_say` (verificar se está configurada)

## Riscos

| Risco | Mitigação |
|-------|-----------|
| mod_say pt-BR não instalado | Fallback para inglês ou arquivo de áudio |
| DTMF não funciona em alguns telefones | Timeout = aceitar como fallback |
| Latência no anúncio TTS | Usar mod_say local (sem rede) |
| Cliente desliga durante anúncio | Monitorar A-leg, cancelar se hangup |

## Estimativa

| Fase | Tarefa | Horas |
|------|--------|-------|
| 1 | Implementar `uuid_say()` e teste com mod_say | 2h |
| 2 | Implementar detecção DTMF no B-leg | 3h |
| 3 | Criar `execute_announced_transfer()` | 4h |
| 4 | Integrar com `session.py` | 2h |
| 5 | Construir texto de anúncio dinamicamente | 2h |
| 6 | Testes end-to-end | 3h |
| 7 | Fallback para recado/callback (básico) | 2h |
| **Total** | | **18h** |

## Fora de Escopo (Futuro)

- TTS com ElevenLabs para anúncio (voz mais natural)
- Speech recognition para "sim/não" ao invés de DTMF
- Anúncio bidirecional (cliente também ouve)
- Gravação do anúncio para compliance
