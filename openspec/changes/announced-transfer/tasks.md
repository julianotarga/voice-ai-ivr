# Tasks: Attended Transfer com Anúncio

## Fase 1: Infraestrutura ESL (2h)

### 1.1 Verificar mod_say pt-BR
- [ ] Verificar se `mod_say` está carregado no FreeSWITCH
- [ ] Testar comando: `say pt-BR CURRENT_DATE_TIME`
- [ ] Se não tiver pt-BR, configurar ou usar fallback en-US

**Comandos de verificação:**
```bash
fs_cli -x "module_exists mod_say"
fs_cli -x "say pt-BR CURRENT_DATE_TIME" 
```

### 1.2 Implementar uuid_say() no esl_client.py
- [ ] Adicionar método `uuid_say(uuid, text, lang="pt-BR")`
- [ ] Usar `uuid_broadcast {uuid} say:{lang}:{text} aleg`
- [ ] Retornar True/False baseado no resultado

```python
async def uuid_say(
    self, 
    uuid: str, 
    text: str, 
    lang: str = "pt-BR"
) -> bool:
    """
    Fala texto para um canal usando mod_say.
    
    Args:
        uuid: UUID do canal
        text: Texto a falar
        lang: Idioma (pt-BR, en-US, etc)
    
    Returns:
        True se sucesso
    """
    # Escapar aspas e caracteres especiais
    text_escaped = text.replace("'", "\\'")
    
    result = await self.execute_api(
        f"uuid_broadcast {uuid} 'say:{lang}:{text_escaped}' aleg"
    )
    return "+OK" in result
```

---

## Fase 2: Detecção de DTMF no B-leg (3h)

### 2.1 Subscrever eventos DTMF
- [ ] Adicionar DTMF à lista de eventos monitorados
- [ ] Criar queue para armazenar DTMFs recebidos por UUID

### 2.2 Implementar wait_for_reject_or_timeout()
- [ ] Aguardar DTMF "2" (recusar) ou timeout (aceitar)
- [ ] Filtrar por UUID do canal
- [ ] Retornar "accept" se timeout, "reject" se DTMF 2 ou hangup

```python
async def wait_for_reject_or_timeout(
    self,
    uuid: str,
    timeout: float = 5.0
) -> str:
    """
    Aguarda recusa (DTMF 2 ou hangup) ou timeout (aceitar).
    
    Modelo híbrido: não fazer nada = aceitar, pressionar 2 = recusar.
    
    Args:
        uuid: UUID do canal
        timeout: Tempo para aceitar automaticamente (segundos)
    
    Returns:
        "accept" - Timeout (humano aguardou)
        "reject" - DTMF 2 pressionado
        "hangup" - Humano desligou
    """
    start = time.time()
    
    while time.time() - start < timeout:
        # Verificar se canal ainda existe
        if not await self.uuid_exists(uuid):
            return "hangup"
        
        # Verificar se DTMF 2 foi pressionado
        dtmf = self._dtmf_queue.get(uuid)
        if dtmf == "2":
            return "reject"
        
        await asyncio.sleep(0.1)
    
    # Timeout = aceitar
    return "accept"
```

### 2.3 Handler de evento DTMF
- [ ] Processar evento `DTMF` do FreeSWITCH
- [ ] Armazenar na queue por UUID

---

## Fase 3: Método execute_announced_transfer() (4h)

### 3.1 Estrutura básica
- [ ] Copiar lógica de `execute_attended_transfer()`
- [ ] Adicionar parâmetro `announcement: str`

### 3.2 Fluxo de anúncio
- [ ] Após B-leg atender (originate retorna +OK):
  1. Tocar anúncio via `uuid_say()`
  2. Aguardar DTMF com `wait_for_dtmf()`
  3. Processar resposta

### 3.3 Tratamento de respostas (Modelo Híbrido)
- [ ] Timeout (5s): aceitar automaticamente → bridge A↔B
- [ ] DTMF 2: recusar → matar B-leg, retornar REJECTED
- [ ] Hangup: recusar → retornar REJECTED

```python
async def execute_announced_transfer(
    self,
    destination: TransferDestination,
    announcement: str,
    ring_timeout: int = 30,
    accept_timeout: int = 5,  # Tempo para aceitar automaticamente
) -> TransferResult:
    """
    Transferência com anúncio para o humano.
    
    Modelo híbrido:
    - Não fazer nada por 5s = aceitar
    - Pressionar 2 = recusar
    - Desligar = recusar
    """
    # 1. MOH no cliente
    await self._start_moh()
    
    # 2. Originate B-leg
    b_leg_uuid = await self._esl.originate(...)
    if not b_leg_uuid:
        await self._stop_moh()
        return TransferResult(status=FAILED)
    
    # 3. Anunciar para o humano (modelo híbrido)
    announcement_text = (
        f"{announcement}. "
        "Pressione 2 para recusar ou aguarde para aceitar."
    )
    await self._esl.uuid_say(b_leg_uuid, announcement_text)
    
    # 4. Aguardar resposta (timeout = aceitar)
    response = await self._esl.wait_for_reject_or_timeout(
        b_leg_uuid, 
        timeout=accept_timeout
    )
    
    # 5. Processar resposta
    if response == "accept":  # Timeout = aceitar
        await self._stop_moh()
        await self._esl.uuid_setvar(self.call_uuid, "hangup_after_bridge", "true")
        success = await self._esl.uuid_bridge(self.call_uuid, b_leg_uuid)
        
        if success:
            return TransferResult(status=SUCCESS, b_leg_uuid=b_leg_uuid)
        else:
            await self._esl.uuid_kill(b_leg_uuid)
            return TransferResult(status=FAILED, error="Bridge failed")
    
    elif response == "reject":  # DTMF 2 pressionado
        await self._esl.uuid_kill(b_leg_uuid)
        await self._stop_moh()
        return TransferResult(
            status=REJECTED,
            message="O atendente não pode atender agora. Quer deixar um recado?",
            should_offer_callback=True
        )
    
    else:  # Hangup
        await self._stop_moh()
        return TransferResult(
            status=REJECTED,
            message="O atendente não está disponível. Quer deixar um recado?",
            should_offer_callback=True
        )
```

---

## Fase 4: Integração com session.py (2h)

### 4.1 Modificar _execute_intelligent_handoff()
- [ ] Construir texto de anúncio com contexto
- [ ] Chamar `execute_announced_transfer()` ao invés de `execute_attended_transfer()`
- [ ] Tratar novo status REJECTED

### 4.2 Construir anúncio dinâmico
- [ ] Extrair nome do cliente (se disponível)
- [ ] Extrair motivo da ligação (do transcript)
- [ ] Montar texto de anúncio

```python
def _build_announcement(self) -> str:
    """
    Constrói texto de anúncio baseado no contexto da conversa.
    """
    parts = []
    
    # Nome ou número
    caller_name = self._extract_caller_name()
    if caller_name:
        parts.append(f"Olá, tenho {caller_name} na linha")
    else:
        parts.append(f"Olá, tenho o número {self.config.caller_id} na linha")
    
    # Motivo (extraído da conversa)
    reason = self._extract_call_reason()
    if reason:
        parts.append(f"sobre {reason}")
    
    return ". ".join(parts)


def _extract_caller_name(self) -> Optional[str]:
    """
    Extrai nome do cliente do transcript.
    Procura padrões como "meu nome é X" ou "aqui é o X".
    """
    for entry in self._transcript:
        if entry.role == "user":
            # Padrões comuns
            import re
            patterns = [
                r"meu nome [ée] (\w+)",
                r"aqui [ée] o? ?(\w+)",
                r"sou o? ?(\w+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, entry.text.lower())
                if match:
                    return match.group(1).capitalize()
    return None


def _extract_call_reason(self) -> Optional[str]:
    """
    Extrai motivo da ligação do transcript ou do request_handoff.
    """
    # Se o request_handoff tinha "reason", usar
    if hasattr(self, '_last_handoff_reason') and self._last_handoff_reason:
        return self._last_handoff_reason
    
    # Senão, tentar extrair do transcript
    # (simplificado: pegar últimas mensagens do usuário)
    user_messages = [e.text for e in self._transcript if e.role == "user"][-3:]
    if user_messages:
        # Resumir (simplificado)
        return user_messages[-1][:50]
    
    return None
```

---

## Fase 5: Configuração (2h)

### 5.1 Adicionar campos ao RealtimeSessionConfig
- [ ] `transfer_announcement_enabled: bool = True`
- [ ] `transfer_announcement_lang: str = "pt-BR"`
- [ ] `transfer_dtmf_timeout: int = 15`

### 5.2 Adicionar ao app_config.php do FusionPBX
- [ ] Campos no frontend para configurar anúncio
- [ ] Labels em app_languages.php

### 5.3 Atualizar server.py
- [ ] Ler novos campos do banco
- [ ] Passar para RealtimeSessionConfig

---

## Fase 6: Testes (3h)

### 6.1 Teste manual - Fluxo feliz (timeout = aceitar)
- [ ] Cliente pede transferência
- [ ] Humano atende, ouve anúncio
- [ ] Humano **aguarda 5 segundos** (não faz nada)
- [ ] Bridge estabelecido automaticamente
- [ ] Conversa funciona

### 6.2 Teste manual - Humano recusa (DTMF 2)
- [ ] Cliente pede transferência
- [ ] Humano atende, ouve anúncio
- [ ] Humano **pressiona 2**
- [ ] Agente volta ao cliente: "Vendas não pode atender..."

### 6.3 Teste manual - Humano desliga
- [ ] Cliente pede transferência
- [ ] Humano atende, ouve anúncio
- [ ] Humano **desliga** antes do timeout
- [ ] Agente volta ao cliente: "Vendas não está disponível..."

### 6.4 Teste manual - Humano não atende
- [ ] Cliente pede transferência
- [ ] Telefone toca mas humano **não atende** (30s)
- [ ] Agente volta ao cliente: "Vendas não atendeu..."

### 6.5 Teste manual - Cliente desliga durante anúncio
- [ ] Cliente pede transferência
- [ ] Durante anúncio, **cliente desliga**
- [ ] B-leg é encerrado automaticamente

---

## Fase 7: Fallback - Recado/Callback (2h)

### 7.1 Quando humano recusa
- [ ] Agente pergunta: "Quer deixar um recado?"
- [ ] Se sim: capturar recado via transcript
- [ ] Criar ticket no OmniPlay com recado

### 7.2 Quando humano não atende
- [ ] Agente pergunta: "Quer que retornem sua ligação?"
- [ ] Se sim: capturar número (ou confirmar caller_id)
- [ ] Criar ticket de callback no OmniPlay

**Nota**: Esta fase pode ser simplificada para MVP, deixando a lógica completa para FASE 2 do roadmap.

---

## Checklist de Entrega

- [ ] Código implementado e testado
- [ ] Logs de debug podem ser removidos ou mantidos em nível DEBUG
- [ ] Documentação atualizada (se necessário)
- [ ] Commit com mensagem descritiva
- [ ] Testes manuais passando

---

## Comandos Úteis para Debug

```bash
# Ver eventos DTMF no fs_cli
/event plain DTMF

# Testar mod_say manualmente
fs_cli -x "originate user/1001 &say(pt-BR,Olá, tenho um cliente na linha)"

# Ver variáveis de um canal
fs_cli -x "uuid_dump {uuid}"

# Ver bridges ativos
fs_cli -x "show bridges"
```
