# Debug: Transferência de Chamadas

## Problema Reportado
> O agente transfere mas quando o atendente humano atende a ligação cai

## Fluxo Esperado
```
1. Cliente pede transferência → AI anuncia
2. MOH inicia no A-leg
3. originate B-leg → Telefone toca
4. Humano atende → originate retorna +OK
5. MOH para
6. uuid_bridge(A, B) → Conecta A e B
7. session.stop("transfer_success") → NÃO desliga A-leg
8. Cliente conversa com humano
```

## Hipóteses de Falha

### H1: uuid_bridge retorna +OK mas falha silenciosamente
- O bridge pode não estar funcionando corretamente
- Teste: Verificar `show channels` após bridge

### H2: uuid_audio_stream stop derruba a chamada
- Linha 1254 em session.py: `uuid_audio_stream {uuid} stop`
- Pode estar matando o canal ao invés de apenas parar o stream
- Teste: Remover temporariamente essa linha

### H3: ESL Outbound desconecta e mata o canal
- O mod_audio_stream usa ESL Outbound
- Quando a sessão Python fecha, o ESL Outbound pode desligar o canal
- Teste: Verificar se o canal morre quando o WebSocket fecha

### H4: hangup_after_bridge mata prematuramente
- Variável `hangup_after_bridge=true` na originate
- Pode estar interpretando incorretamente
- Teste: Verificar eventos HANGUP

---

## LINHA DE DEBUG

### Passo 1: Habilitar logs detalhados no FreeSWITCH

```bash
# No fs_cli:
sofia global siptrace on
sofia loglevel all 9
console loglevel debug
```

### Passo 2: Observar logs durante teste

**Em um terminal (logs do container):**
```bash
docker logs -f voice-ai-realtime 2>&1 | tee /tmp/voice-ai-debug.log
```

**Em outro terminal (logs do FreeSWITCH):**
```bash
tail -f /var/log/freeswitch/freeswitch.log | grep -E "(uuid_bridge|uuid_audio|HANGUP|CHANNEL_ANSWER)"
```

**Em outro terminal (fs_cli):**
```bash
fs_cli
/event plain CHANNEL_ANSWER CHANNEL_HANGUP CHANNEL_BRIDGE CHANNEL_UNBRIDGE
```

### Passo 3: Fazer uma chamada de teste

1. Ligar para a secretária
2. Pedir: "Me transfere para vendas"
3. Atender no ramal 1001
4. Observar os logs

### Passo 4: Coletar informações

**Perguntas a responder:**
1. O `uuid_bridge` retornou +OK?
2. Qual evento veio primeiro após o bridge?
3. Qual foi o `Hangup-Cause`?
4. Quem desligou: A-leg ou B-leg?
5. O `uuid_audio_stream stop` foi executado?

---

## COMANDOS DE DEBUG RÁPIDO

### Ver chamadas ativas:
```bash
fs_cli -x "show channels"
```

### Ver variáveis de um canal:
```bash
fs_cli -x "uuid_dump <UUID>"
```

### Ver bridges ativos:
```bash
fs_cli -x "show bridges"
```

### Verificar se uuid existe:
```bash
fs_cli -x "uuid_exists <UUID>"
```

---

## TESTE MANUAL (SEM VOICE AI)

Para isolar se o problema é no FreeSWITCH ou no Python:

```bash
# No fs_cli, simular transferência manual:

# 1. Originar chamada para ramal 1001
originate user/1001@ativo.netplay.net.br &park()

# 2. Copiar o UUID retornado (ex: abc123...)

# 3. Em outra chamada ativa (A-leg), criar bridge:
uuid_bridge <A-leg-uuid> <B-leg-uuid>

# 4. Verificar se as chamadas estão conectadas
show bridges
```

Se funcionar manualmente, o problema está no Python.
Se NÃO funcionar, o problema está no FreeSWITCH.

---

## LOGS ESPERADOS (SUCESSO)

```
# Voice AI
Transfer successful: bridge established
Realtime session stopped (reason=transfer_success)

# FreeSWITCH
CHANNEL_ANSWER B-leg-uuid
uuid_bridge success: A-leg <-> B-leg
CHANNEL_BRIDGE A-leg-uuid B-leg-uuid
```

## LOGS ESPERADOS (FALHA)

```
# Se uuid_bridge falhar:
uuid_bridge failed: -ERR ...

# Se uuid_audio_stream matar o canal:
CHANNEL_HANGUP A-leg-uuid (após uuid_audio_stream stop)

# Se ESL Outbound desconectar:
CHANNEL_HANGUP A-leg-uuid (Hangup-Cause: MANAGER_REQUEST)
```

---

## CORREÇÃO PROVISÓRIA (Se H2 for confirmada)

Se `uuid_audio_stream stop` estiver causando o problema:

```python
# Em session.py, linha ~1248-1259, comentar temporariamente:

# if reason == "transfer_success":
#     try:
#         from .esl import get_esl_adapter
#         adapter = get_esl_adapter(self.call_uuid)
#         await adapter.execute_api(f"uuid_audio_stream {self.call_uuid} stop")
#     except Exception as e:
#         logger.warning(...)
```

---

## PRÓXIMOS PASSOS

1. [ ] Executar Passo 1-4 e coletar logs
2. [ ] Identificar qual hipótese está correta
3. [ ] Aplicar correção apropriada
4. [ ] Testar novamente
