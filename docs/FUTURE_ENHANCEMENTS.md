# Future Enhancements - Voice AI

## Baseado nos aprendizados do SignalWire AI Stack

### 1. Tool Calling (SWAIG-like)

Implementar funções que o agente pode chamar durante a conversa:

```python
# Exemplo de tool no ElevenLabs Agent
{
  "name": "transfer_call",
  "description": "Transfer the call to a human agent",
  "parameters": {
    "department": {"type": "string", "enum": ["sales", "support", "billing"]}
  }
}
```

**Implementação no bridge**:
```python
async def handle_tool_call(self, tool_name: str, params: dict):
    if tool_name == "transfer_call":
        department = params.get("department")
        # Enviar comando para FreeSWITCH via ESL
        await self.esl_client.execute(
            f"uuid_transfer {self.call_uuid} {DEPARTMENT_EXTENSIONS[department]}"
        )
        return {"success": True, "message": f"Transferring to {department}"}
```

### 2. Context Switching

Permitir mudança de contexto mid-call:

```python
async def switch_context(self, new_context: str):
    """Muda o contexto do agente durante a conversa."""
    await self.provider.send_contextual_update(f"""
    CONTEXT SWITCH: You are now focused on {new_context}.
    Adjust your responses accordingly.
    """)
```

**Triggers**:
- Intent detectado (ex: "quero falar com vendas")
- Timeout de inatividade
- Webhook externo

### 3. Fillers Inteligentes

Quando o agente está processando algo demorado:

```python
FILLERS = {
    "pt-BR": [
        "Um momento, por favor...",
        "Estou verificando isso para você...",
        "Deixa eu conferir aqui...",
    ],
    "en-US": [
        "One moment please...",
        "Let me check that for you...",
        "Just a second...",
    ]
}

async def play_filler(self, language: str = "pt-BR"):
    """Toca um filler aleatório."""
    filler = random.choice(FILLERS[language])
    await self.provider.send_text(filler)
```

### 4. Sliding Window (Economia de Tokens)

Limitar histórico de conversa:

```python
class ConversationWindow:
    def __init__(self, max_turns: int = 10):
        self.max_turns = max_turns
        self.turns = []
    
    def add_turn(self, role: str, content: str):
        self.turns.append({"role": role, "content": content})
        if len(self.turns) > self.max_turns * 2:  # user + assistant
            self.turns = self.turns[-self.max_turns * 2:]
    
    def get_context(self) -> str:
        return "\n".join([f"{t['role']}: {t['content']}" for t in self.turns])
```

### 5. Post-Prompt Summarization

Ao final da conversa, gerar resumo:

```python
async def on_conversation_end(self, conversation_id: str):
    """Gera resumo da conversa para CRM."""
    summary = await self.llm.summarize(
        self.conversation_history,
        prompt="Resuma esta conversa em formato JSON com: intent, outcome, action_required"
    )
    
    await self.save_to_crm(conversation_id, summary)
```

### 6. Multi-Language Support

Detectar idioma e trocar voz:

```python
LANGUAGE_VOICES = {
    "pt-BR": "voice_id_portuguese",
    "en-US": "voice_id_english", 
    "es-ES": "voice_id_spanish",
}

async def detect_and_switch_language(self, transcript: str):
    """Detecta idioma e troca voz se necessário."""
    detected = langdetect.detect(transcript)
    if detected != self.current_language:
        self.current_language = detected
        await self.switch_voice(LANGUAGE_VOICES.get(detected))
```

### 7. Barge-in Otimizado

Parar imediatamente quando usuário fala:

```python
async def handle_user_speech_start(self):
    """Chamado quando VAD detecta início de fala do usuário."""
    # Parar playback imediatamente
    await self.provider.interrupt()
    
    # Cancelar chunks pendentes
    while not self.audio_queue.empty():
        try:
            self.audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
```

### 8. Métricas e Observabilidade

```python
from prometheus_client import Histogram, Counter

LATENCY = Histogram(
    'voice_ai_latency_seconds',
    'Latency from user speech end to agent response start',
    ['provider']
)

CONVERSATIONS = Counter(
    'voice_ai_conversations_total',
    'Total conversations',
    ['domain_uuid', 'outcome']
)

async def measure_latency(self, start_time: float):
    latency = time.time() - start_time
    LATENCY.labels(provider=self.provider_name).observe(latency)
```

### 9. Fallback entre Providers

Se ElevenLabs falhar, tentar outro:

```python
PROVIDER_PRIORITY = ["elevenlabs", "openai_realtime", "gemini_live"]

async def connect_with_fallback(self):
    """Tenta conectar ao primeiro provider disponível."""
    for provider_name in PROVIDER_PRIORITY:
        try:
            self.provider = await self.factory.create(provider_name)
            await self.provider.connect()
            logger.info(f"Connected to {provider_name}")
            return
        except Exception as e:
            logger.warning(f"Failed to connect to {provider_name}: {e}")
    
    raise RuntimeError("All providers failed")
```

### 10. Gravação de Conversas

Salvar áudio para QA:

```python
async def record_audio(self, audio_bytes: bytes, direction: str):
    """Grava áudio para arquivo."""
    filename = f"/recordings/{self.call_uuid}_{direction}.raw"
    async with aiofiles.open(filename, "ab") as f:
        await f.write(audio_bytes)
```

---

## Prioridade de Implementação

| Enhancement | Impacto | Esforço | Prioridade |
|-------------|---------|---------|------------|
| Tool Calling | Alto | Médio | P1 |
| Barge-in Otimizado | Alto | Baixo | P1 |
| Métricas | Médio | Baixo | P1 |
| Fillers | Médio | Baixo | P2 |
| Fallback | Alto | Médio | P2 |
| Gravação | Médio | Baixo | P2 |
| Context Switching | Médio | Alto | P3 |
| Multi-Language | Baixo | Alto | P3 |
| Sliding Window | Baixo | Baixo | P3 |
| Post-Prompt | Baixo | Médio | P3 |

---

## Referências

- [SignalWire AI Stack](https://signalwire.com/blogs/ceo/building-a-voice-ai-stack-that-balances-power-with-flexibility)
- [SignalWire Digital Employees](https://github.com/signalwire/digital_employees)
- [ElevenLabs Tool Calling](https://elevenlabs.io/docs/agents-platform/client-tools)
