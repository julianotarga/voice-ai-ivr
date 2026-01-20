# 🎙️ Voice AI IVR - Documentação Técnica Completa

## Índice

1. [Visão Geral](#visão-geral)
2. [Arquitetura do Sistema](#arquitetura-do-sistema)
3. [Fluxo de Áudio](#fluxo-de-áudio)
4. [Codecs e Formatos de Áudio](#codecs-e-formatos-de-áudio)
5. [Integração com OpenAI Realtime](#integração-com-openai-realtime)
6. [Integração com FreeSWITCH](#integração-com-freeswitch)
7. [Echo Cancellation (AEC)](#echo-cancellation-aec)
8. [Intelligent Handoff](#intelligent-handoff)
9. [Evolução do Projeto](#evolução-do-projeto)
10. [FAQ Técnico](#faq-técnico)

---

## Visão Geral

O **Voice AI IVR** é um sistema de atendimento telefônico inteligente que utiliza IA conversacional em tempo real para conduzir conversas naturais por voz. O sistema atua como uma "ponte" (bridge) entre:

- **FreeSWITCH/FusionPBX**: Central telefônica VoIP
- **Provedores de IA**: OpenAI Realtime, ElevenLabs, Google Gemini Live
- **OmniPlay Backend**: Sistema omnichannel para tickets e atendimento

### Características Principais

| Característica | Descrição |
|----------------|-----------|
| **Latência** | ~300-500ms end-to-end |
| **Codec Nativo** | G.711 μ-law (PCMU) @ 8kHz |
| **Formato OpenAI** | PCM16 (L16) @ 24kHz |
| **Barge-in** | Suportado via VAD + AEC |
| **Multi-tenant** | Isolamento por domain/company |
| **Transcodificação** | Sim (8kHz ↔ 24kHz) |

---

## Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                               VOICE AI IVR ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌──────────────┐     ┌───────────────────────┐     ┌─────────────────────────────┐ │
│  │   Telefone   │────▶│     FreeSWITCH        │────▶│      voice-ai-realtime      │ │
│  │  (SIP/PSTN)  │     │   + FusionPBX         │     │      (Python Bridge)        │ │
│  │              │     │   + mod_audio_stream  │     │                             │ │
│  │ G.711 μ-law  │     │   + ESL Outbound      │     │  ┌─────────────────────┐    │ │
│  │   @ 8kHz     │     │                       │     │  │  Echo Canceller     │    │ │
│  └──────────────┘     └───────────────────────┘     │  │  (Speex DSP)        │    │ │
│        ▲                         │                   │  └─────────────────────┘    │ │
│        │                         │                   │  ┌─────────────────────┐    │ │
│        │                         │                   │  │  Resampler          │    │ │
│        │                         │                   │  │  8kHz ↔ 24kHz       │    │ │
│        │                         │                   │  └─────────────────────┘    │ │
│        │                         │                   │  ┌─────────────────────┐    │ │
│        │              WebSocket  │                   │  │  G.711 Codec        │    │ │
│        │            (ws://8085)  │                   │  │  (μ-law/A-law)      │    │ │
│        │                         ▼                   │  └─────────────────────┘    │ │
│        │              ┌─────────────────────┐        └───────────┬────────────────┘ │
│        │              │  Audio Buffer       │                    │                  │
│        │              │  Warmup: 300ms      │                    │                  │
│        │              │  Pacing: 20ms       │                    │                  │
│        │              └─────────────────────┘                    │                  │
│        │                                                         │                  │
│        │                                         WebSocket (wss://api.openai.com)   │
│        │                                                         │                  │
│        │                                                         ▼                  │
│        │                                          ┌─────────────────────────────┐   │
│        │                                          │     OpenAI Realtime API     │   │
│        │                                          │                             │   │
│        └──────────────────────────────────────────│  Model: gpt-realtime        │   │
│                     Áudio de resposta             │  Voice: marin/alloy/sage    │   │
│                     (G.711 → PCM16 → G.711)       │  VAD: semantic_vad          │   │
│                                                   │  Format: PCM16 @ 24kHz      │   │
│                                                   └─────────────────────────────┘   │
│                                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                           ESL CONTROL PLANE                                   │   │
│  │  ┌─────────────────┐  ┌─────────────────────┐  ┌─────────────────────────┐   │   │
│  │  │  ESL Outbound   │  │  ESL Inbound        │  │  ESL Hybrid Adapter     │   │   │
│  │  │  (FS → Python)  │  │  (Python → FS)      │  │  (Fallback automático)  │   │   │
│  │  │  Port: 8022     │  │  Port: 8021         │  │                         │   │   │
│  │  └─────────────────┘  └─────────────────────┘  └─────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Componentes

| Componente | Linguagem | Porta | Função |
|------------|-----------|-------|--------|
| **voice-ai-realtime** | Python | 8085 | Bridge WebSocket, processamento de áudio, integração com LLMs |
| **mod_audio_stream** | C | - | Módulo FreeSWITCH para streaming de áudio via WebSocket |
| **voice_secretary.lua** | Lua | - | Script que inicia a sessão de Voice AI |
| **FusionPBX App** | PHP | - | UI para configuração de secretárias e providers |

---

## Fluxo de Áudio

### Direção: Caller → AI (Upstream)

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Telefone   │───▶│ FreeSWITCH  │───▶│ mod_audio   │───▶│ voice-ai    │───▶│   OpenAI    │
│             │    │             │    │ _stream     │    │ realtime    │    │  Realtime   │
│  G.711 μ    │    │ G.711 μ     │    │ PCM16 16kHz │    │ PCM16 24kHz │    │ PCM16 24kHz │
│  8kHz       │    │ 8kHz        │    │ (binário)   │    │ (base64)    │    │             │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                                                               │
                                                    ┌──────────┴──────────┐
                                                    │  Processamento:     │
                                                    │  1. G.711 → PCM16   │
                                                    │  2. Echo Canceller  │
                                                    │  3. Resample 8→24k  │
                                                    │  4. Encode base64   │
                                                    └─────────────────────┘
```

### Direção: AI → Caller (Downstream)

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   OpenAI    │───▶│ voice-ai    │───▶│ mod_audio   │───▶│ FreeSWITCH  │───▶│  Telefone   │
│  Realtime   │    │ realtime    │    │ _stream     │    │             │    │             │
│ PCM16 24kHz │    │ PCM16 8kHz  │    │ PCM16 8kHz  │    │ G.711 μ     │    │  G.711 μ    │
│  (base64)   │    │ (binário)   │    │ (binário)   │    │ 8kHz        │    │  8kHz       │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                          │
               ┌──────────┴──────────┐
               │  Processamento:     │
               │  1. Decode base64   │
               │  2. Resample 24→8k  │
               │  3. Audio Buffer    │
               │     (warmup 300ms)  │
               │  4. Pacing 20ms     │
               │  5. Feed AEC ref    │
               └─────────────────────┘
```

---

## Codecs e Formatos de Áudio

### Tabela de Formatos

| Ponto | Codec | Sample Rate | Bits | Bytes/20ms |
|-------|-------|-------------|------|------------|
| **Telefone ↔ FreeSWITCH** | G.711 μ-law (PCMU) | 8 kHz | 8-bit | 160 bytes |
| **mod_audio_stream ↔ Bridge** | L16 PCM | 16 kHz | 16-bit | 640 bytes |
| **Bridge ↔ OpenAI Realtime** | L16 PCM | 24 kHz | 16-bit | 960 bytes |

### Transcodificação

O sistema realiza **duas transcodificações**:

1. **G.711 ↔ L16 PCM**: No FreeSWITCH (transparente)
2. **8kHz/16kHz ↔ 24kHz**: No voice-ai-realtime (via Resampler)

```python
# Resampler (voice-ai-service/realtime/utils/resampler.py)
class ResamplerPair:
    """Par de resamplers para comunicação bidirecional."""
    
    def __init__(self, freeswitch_rate: int = 8000, openai_rate: int = 24000):
        # Upstream: FS → OpenAI (8kHz → 24kHz)
        self.upstream = Resampler(freeswitch_rate, openai_rate)
        
        # Downstream: OpenAI → FS (24kHz → 8kHz)  
        self.downstream = Resampler(openai_rate, freeswitch_rate)
```

### G.711 Codec

```python
# voice-ai-service/realtime/utils/audio_codec.py
class G711Codec:
    """G.711 μ-law codec para telefonia."""
    
    def encode(self, pcm_data: bytes) -> bytes:
        """PCM16 → G.711 μ-law (compressão 2:1)"""
        return audioop.lin2ulaw(pcm_data, 2)
    
    def decode(self, ulaw_data: bytes) -> bytes:
        """G.711 μ-law → PCM16 (expansão 1:2)"""
        return audioop.ulaw2lin(ulaw_data, 2)
```

---

## Integração com OpenAI Realtime

### Conexão WebSocket

```python
# Endpoint
url = "wss://api.openai.com/v1/realtime?model=gpt-realtime"

# Headers (API GA - sem OpenAI-Beta)
headers = {
    "Authorization": f"Bearer {OPENAI_API_KEY}"
}

# Conectar
ws = await websockets.connect(url, additional_headers=headers)
```

### Configuração de Sessão (API GA)

```json
{
    "type": "session.update",
    "session": {
        "type": "realtime",
        "output_modalities": ["audio"],
        "instructions": "Você é uma secretária virtual...",
        "tools": [...],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "noise_reduction": {"type": "far_field"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "medium",
                    "create_response": true,
                    "interrupt_response": true
                },
                "transcription": {"model": "gpt-4o-transcribe"}
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "voice": "marin"
            }
        }
    }
}
```

### Eventos Principais

| Evento | Direção | Descrição |
|--------|---------|-----------|
| `input_audio_buffer.append` | → Server | Enviar chunk de áudio (base64) |
| `response.output_audio.delta` | ← Server | Receber chunk de áudio (base64) |
| `response.audio_transcript.delta` | ← Server | Transcrição do assistente |
| `conversation.item.input_audio_transcription.completed` | ← Server | Transcrição do usuário |
| `input_audio_buffer.speech_started` | ← Server | VAD: usuário começou a falar |
| `input_audio_buffer.speech_stopped` | ← Server | VAD: usuário parou de falar |
| `response.function_call_arguments.done` | ← Server | Function call (handoff, end_call) |
| `response.cancel` | → Server | Barge-in: interromper resposta |

### Vozes Disponíveis (API GA)

| Voz | Gênero | Característica |
|-----|--------|----------------|
| **marin** | Feminino | Natural, pt-BR recomendada |
| **alloy** | Neutro | Versátil |
| **ash** | Masculino | Profundo |
| **ballad** | Feminino | Suave |
| **coral** | Feminino | Expressiva |
| **echo** | Masculino | Claro |
| **sage** | Feminino | Calma |
| **shimmer** | Feminino | Animada |
| **verse** | Masculino | Articulado |
| **cedar** | Masculino | Robusto |

---

## Integração com FreeSWITCH

### mod_audio_stream

Módulo customizado que faz streaming bidirecional de áudio via WebSocket.

```bash
# Dialplan (voice_secretary.lua)
uuid_audio_stream <uuid> start ws://voice-ai:8085/stream/<domain>/<call_uuid> mono 16k
```

#### Protocolo WebSocket

**FreeSWITCH → Bridge:**
- Binário: Chunks de PCM16 @ 16kHz

**Bridge → FreeSWITCH:**
- JSON (uma vez): `{"type":"rawAudio","data":{"sampleRate":16000}}`
- Binário: Chunks de 640 bytes (20ms) com pacing

### ESL (Event Socket Library)

O sistema usa ESL híbrido para controle de chamadas:

```python
class ESLHybridAdapter:
    """Tenta ESL Outbound primeiro, fallback para Inbound."""
    
    async def hold_call(self, uuid: str) -> bool:
        """Coloca chamada em hold."""
        if self._outbound_connected:
            return await self._outbound_hold(uuid)
        else:
            return await self._inbound_hold(uuid)
```

**Comandos ESL usados:**

| Comando | Função |
|---------|--------|
| `uuid_broadcast` | Reproduzir áudio |
| `uuid_audio_stream` | Iniciar/parar streaming |
| `uuid_transfer` | Transferir chamada |
| `uuid_bridge` | Conectar canais (bridge) |
| `uuid_break` | Interromper playback |
| `uuid_kill` | Encerrar chamada |
| `sofia status profile internal reg` | Verificar registros SIP |

---

## Echo Cancellation (AEC)

### Problema

Quando o assistente fala, o áudio é reproduzido no telefone do caller. O microfone do telefone captura esse áudio como "eco", fazendo o assistente ouvir a si mesmo e se interromper.

### Solução

Implementamos **Acoustic Echo Cancellation** usando Speex DSP no Python:

```python
class EchoCancellerWrapper:
    """
    Echo Canceller com buffer de delay.
    
    O echo leva ~200ms para aparecer no mic:
    - FreeSWITCH → RTP → Telefone: ~50-100ms
    - Speaker → Mic acústico: ~10-20ms
    - Telefone → RTP → FreeSWITCH: ~50-100ms
    
    Por isso, mantemos os frames do speaker em um delay_buffer
    antes de usá-los como referência para o AEC.
    """
    
    def __init__(self, echo_delay_ms: int = 200):
        self.echo_delay_frames = int(echo_delay_ms / 20)  # 10 frames
        self.delay_buffer = deque(maxlen=30)
        self.speaker_buffer = deque(maxlen=30)
        self._ec = EchoCanceller.create(frame_size=160, filter_length=1024)
    
    def add_speaker_frame(self, audio: bytes):
        """Adiciona áudio do speaker ao delay buffer."""
        self.delay_buffer.append(audio)
        
        # Mover para speaker_buffer após o delay
        while len(self.delay_buffer) > self.echo_delay_frames:
            self.speaker_buffer.append(self.delay_buffer.popleft())
    
    def process(self, mic_audio: bytes) -> bytes:
        """Remove eco do áudio do mic usando referência do speaker."""
        if self.speaker_buffer:
            speaker_ref = self.speaker_buffer.popleft()
        else:
            speaker_ref = bytes(len(mic_audio))  # Silêncio
        
        return self._ec.process(mic_audio, speaker_ref)
```

### Fluxo AEC

```
                    ┌─────────────────────────┐
                    │     voice-ai-realtime   │
                    │                         │
   ┌────────────────┼───────────────────────────────────────────┐
   │                │                         │                 │
   │  SPEAKER PATH  │                         │  MIC PATH       │
   │                ▼                         │                 │
   │  ┌─────────────────────┐                 │                 │
   │  │  OpenAI Response    │                 │                 │
   │  │  (audio.delta)      │                 │                 │
   │  └──────────┬──────────┘                 │                 │
   │             │                            │                 │
   │             ▼                            │                 │
   │  ┌─────────────────────┐                 │                 │
   │  │  add_speaker_frame  │────────────┐    │                 │
   │  └──────────┬──────────┘            │    │                 │
   │             │                       │    │                 │
   │             ▼                       │    │                 │
   │  ┌─────────────────────┐            │    │   ┌────────────────────┐
   │  │  delay_buffer       │            │    │   │  FreeSWITCH Input  │
   │  │  (200ms delay)      │            │    │   │  (mic audio)       │
   │  └──────────┬──────────┘            │    │   └─────────┬──────────┘
   │             │                       │    │             │
   │             ▼                       │    │             ▼
   │  ┌─────────────────────┐            │    │  ┌─────────────────────┐
   │  │  speaker_buffer     │◀───────────┘    │  │  AEC.process()      │
   │  │  (referência)       │─────────────────┼─▶│  (remove echo)      │
   │  └──────────┬──────────┘                 │  └─────────┬───────────┘
   │             │                            │            │
   │             ▼                            │            ▼
   │  ┌─────────────────────┐                 │  ┌─────────────────────┐
   │  │  Send to FreeSWITCH │                 │  │  Send to OpenAI     │
   │  │  (caller hears)     │                 │  │  (clean audio)      │
   │  └─────────────────────┘                 │  └─────────────────────┘
   │                                          │
   └──────────────────────────────────────────┘
```

---

## Intelligent Handoff

### Visão Geral

O sistema detecta quando o caller quer falar com um humano e executa uma transferência inteligente:

1. **Detecção**: Function call `request_handoff(destination, reason)`
2. **Resolução**: Busca ramal/extensão para o destino
3. **Verificação**: Checa se extensão está registrada online
4. **Anúncio**: Usa Realtime API para conversar com o atendente
5. **Bridge**: Conecta caller com atendente se aceito

### Fluxo de Transferência Anunciada

```
┌──────────────┐     ┌───────────────┐     ┌───────────────┐     ┌──────────────┐
│   Caller     │     │ voice-ai      │     │  FreeSWITCH   │     │  Atendente   │
│ (A-leg)      │     │ realtime      │     │               │     │  (B-leg)     │
└──────┬───────┘     └───────┬───────┘     └───────┬───────┘     └──────┬───────┘
       │                     │                     │                     │
       │  "Quero falar       │                     │                     │
       │   com vendas"       │                     │                     │
       │────────────────────▶│                     │                     │
       │                     │                     │                     │
       │                     │ request_handoff     │                     │
       │                     │ (destination="vendas")                    │
       │                     │                     │                     │
       │  "Um momento,       │                     │                     │
       │   vou transferir"   │                     │                     │
       │◀────────────────────│                     │                     │
       │                     │                     │                     │
       │                     │ HOLD + MOH          │                     │
       │                     │────────────────────▶│                     │
       │                     │                     │                     │
       │                     │ sofia status reg    │                     │
       │                     │────────────────────▶│                     │
       │                     │◀────────────────────│                     │
       │                     │ (1001 registrado)   │                     │
       │                     │                     │                     │
       │                     │ originate B-leg     │                     │
       │                     │────────────────────▶│────────────────────▶│
       │                     │                     │                     │
       │                     │                     │◀────────────────────│
       │                     │                     │   ANSWER            │
       │                     │                     │                     │
       │                     │ uuid_audio_stream   │                     │
       │                     │────────────────────▶│                     │
       │                     │                     │                     │
       │                     │◀════════════════════│                     │
       │                     │ Audio WebSocket     │                     │
       │                     │                     │                     │
       │                     │  OpenAI Realtime    │                     │
       │                     │  "Olá, tenho um     │                     │
       │                     │   cliente que quer  │                     │
       │                     │   falar sobre..."   │                     │
       │                     │═══════════════════════════════════════════▶
       │                     │                     │                     │
       │                     │◀══════════════════════════════════════════│
       │                     │  "Pode passar"      │                     │
       │                     │                     │                     │
       │                     │ uuid_bridge A ↔ B   │                     │
       │                     │────────────────────▶│                     │
       │                     │                     │                     │
       │◀════════════════════════════════════════════════════════════════▶
       │               CONVERSA DIRETA             │                     │
```

### Realtime Announcement Session

```python
class RealtimeAnnouncementSession:
    """
    Sessão OpenAI Realtime dedicada para anunciar transferência ao atendente.
    
    Usa conexão ESL dedicada (não singleton) para evitar conflitos.
    """
    
    async def run(self, timeout: float = 15.0) -> AnnouncementResult:
        # 1. Conectar ao OpenAI Realtime
        await self._connect_openai()
        
        # 2. Configurar sessão (mesmo formato GA)
        await self._configure_session()
        
        # 3. Iniciar stream de áudio com B-leg
        await self._start_audio_stream()
        
        # 4. Enviar mensagem inicial
        # "Olá, tenho um cliente na linha sobre..."
        await self._send_initial_message()
        
        # 5. Loop de eventos até aceite/recusa/timeout
        await self._event_loop()
        
        return AnnouncementResult(
            accepted=self._accepted,
            rejected=self._rejected,
            transcript=self._transcript
        )
```

---

## Evolução do Projeto

### Onde Começamos (v1.0 - Novembro 2025)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Telefone   │────▶│ FreeSWITCH  │────▶│   STT       │────▶│    LLM      │
│             │     │             │     │  (Whisper)  │     │  (GPT-4)    │
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                   │
                                                                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Telefone   │◀────│ FreeSWITCH  │◀────│    TTS      │◀────│   Texto     │
│             │     │             │     │ (ElevenLabs)│     │  resposta   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

**Problemas:**
- Latência alta (~2-3 segundos por turno)
- Sem barge-in (não podia interromper)
- Conversa não natural (turn-based)
- Silêncio entre falas

### Evolução para Realtime (v2.0 - Dezembro 2025)

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Telefone   │◀═══▶│   FreeSWITCH    │◀═══▶│  voice-ai       │◀═══▶  OpenAI Realtime
│             │     │ mod_audio_stream│     │  realtime       │       ElevenLabs Conv
└─────────────┘     └─────────────────┘     └─────────────────┘       Gemini Live
                                                    │
                                          ┌─────────┴─────────┐
                                          │  Processamento:   │
                                          │  • Resampling     │
                                          │  • Base64 enc/dec │
                                          │  • Warmup buffer  │
                                          │  • Pacing 20ms    │
                                          └───────────────────┘
```

**Melhorias:**
- Latência ~300-500ms
- Streaming bidirecional
- Conversa natural

**Problemas restantes:**
- Agente se interrompia (echo)
- Handoff era abrupto

### Implementação de AEC (v2.5 - Janeiro 2026)

**Problema:** O agente ouvia a si mesmo (eco) e se interrompia.

**Solução:** Speex DSP Echo Canceller no Python com delay buffer:

```python
# Delay buffer sincroniza speaker com echo no mic
self.echo_delay_ms = 200  # Tempo do echo aparecer
self.delay_buffer = deque()  # Guarda frames do speaker

# Quando speaker frame chega, vai pro delay_buffer
# Depois de 200ms, vai pro speaker_buffer (referência AEC)
```

### Intelligent Handoff (v2.6 - Janeiro 2026)

**Problema:** Transferência era abrupta, atendente não sabia contexto.

**Solução:** Realtime Announcement Session

```python
# Ao transferir, abre sessão OpenAI dedicada com B-leg
# Agente anuncia: "Tenho cliente sobre X, pode atender?"
# Atendente responde por voz
# Se aceitar: bridge
# Se recusar: volta pro caller com mensagem
```

### Onde Estamos Agora (v2.6.1 - Janeiro 2026)

```
                              ┌─────────────────────────────────────────┐
                              │           VOICE AI REALTIME v2.6        │
                              ├─────────────────────────────────────────┤
                              │                                         │
┌─────────────┐     ┌─────────┴───────────┐     ┌─────────────────────┐ │
│  Telefone   │◀═══▶│   FreeSWITCH        │◀═══▶│  voice-ai-realtime  │ │
│  G.711 μ    │     │   mod_audio_stream  │     │                     │ │
│  8kHz       │     │   ESL Outbound      │     │  ┌───────────────┐  │ │
└─────────────┘     └─────────────────────┘     │  │ Echo Canceller│  │ │
                                                │  │ (Speex DSP)   │  │ │
                                                │  └───────────────┘  │ │
                                                │                     │ │
                                                │  ┌───────────────┐  │ │
                                                │  │ Resampler     │  │ │
                                                │  │ 8k ↔ 24k      │  │ │
                                                │  └───────────────┘  │ │
                                                │                     │ │
                                                │  ┌───────────────┐  │ │
                                                │  │ Transfer Mgr  │  │ │
                                                │  │ + Announcement│  │ │
                                                │  └───────────────┘  │◀╦══▶ OpenAI Realtime
                                                │                     │ ║    (gpt-realtime)
                                                │  ┌───────────────┐  │ ║
                                                │  │ ESL Hybrid    │  │ ║
                                                │  │ Adapter       │  │ ║
                                                │  └───────────────┘  │ ║
                                                └─────────────────────┘ ║
                                                                        ║
                              ┌─────────────────────────────────────────╝
                              │
                              ▼
                    ┌─────────────────────┐
                    │  Realtime Session   │
                    │  • semantic_vad     │
                    │  • function calls   │
                    │  • barge-in         │
                    │  • voice: marin     │
                    └─────────────────────┘
```

**Funcionalidades atuais:**
- ✅ Streaming bidirecional de áudio
- ✅ Latência ~300-500ms
- ✅ Barge-in (interrupção) via VAD
- ✅ Echo Cancellation (Speex DSP)
- ✅ Handoff inteligente com anúncio
- ✅ Multi-tenant por domain
- ✅ Function calls (request_handoff, end_call)
- ✅ Métricas Prometheus
- ✅ ESL Híbrido (Outbound + Inbound)

---

## FAQ Técnico

### 1. O sistema é realmente Realtime?

**Sim.** O sistema usa WebSocket para streaming bidirecional de áudio:

- **Upstream** (caller → AI): Chunks de 20ms enviados em tempo real
- **Downstream** (AI → caller): Chunks de 20ms reproduzidos com pacing

A latência total é ~300-500ms, composta por:
- Rede: ~50ms
- Processamento OpenAI: ~150-250ms
- Warmup buffer: ~200-300ms

### 2. Quais codecs são usados?

| Segmento | Codec | Sample Rate |
|----------|-------|-------------|
| Telefone ↔ FreeSWITCH | G.711 μ-law (PCMU) | 8 kHz |
| FreeSWITCH ↔ Bridge | L16 PCM | 16 kHz |
| Bridge ↔ OpenAI | L16 PCM | 24 kHz |

### 3. O sistema faz transcodificação?

**Sim**, em duas etapas:

1. **G.711 ↔ PCM**: Feita pelo FreeSWITCH (transparente)
2. **8kHz/16kHz ↔ 24kHz**: Feita pelo Resampler no Python

### 4. Como funciona o barge-in?

O barge-in permite que o caller interrompa o agente:

1. **Detecção**: OpenAI VAD detecta fala do usuário
2. **Sinal**: `input_audio_buffer.speech_started` enviado
3. **Interrupção**: Bridge envia `response.cancel` para OpenAI
4. **Stop**: Bridge envia `StopAudio` para FreeSWITCH

```python
# Quando VAD detecta fala
if event_type == "input_audio_buffer.speech_started":
    # Parar playback no FreeSWITCH
    await self._send_stop_audio()
    # Cancelar resposta em andamento no OpenAI
    await self._provider.interrupt()
```

### 5. Como o Echo Cancellation funciona?

O AEC usa Speex DSP com um delay buffer:

1. **Referência**: Áudio enviado ao caller é guardado no `delay_buffer`
2. **Delay**: Após `echo_delay_ms` (200ms), frames vão para `speaker_buffer`
3. **Processo**: Quando áudio do mic chega, AEC subtrai a referência

```python
# Delay compensa o tempo do echo:
# Speaker → RTP → Telefone → Speaker físico → Mic → RTP → FreeSWITCH
# Total: ~150-250ms
```

### 6. O que acontece se não houver atendente disponível?

O TransferManager verifica disponibilidade antes de transferir:

1. **Verifica registro SIP**: `sofia status profile internal reg`
2. **Se offline**: Retorna `TransferStatus.OFFLINE`
3. **Fallback**: Cria ticket no OmniPlay com transcrição

```python
if not is_registered:
    # Agente informa ao caller
    "Infelizmente não há atendentes disponíveis no momento. 
     Vou criar um protocolo para você..."
    
    # Cria ticket via API
    await omniplay_api.create_ticket(transcript, reason)
```

### 7. Como funciona a verificação de registro SIP multi-tenant?

A verificação respeita isolamento por domínio:

```python
# Comando
result = await esl.execute_api("sofia status profile internal reg")

# Parsing seguro (multi-tenant)
target = f"{extension}@{domain}"
for line in result.split('\n'):
    if line.startswith("User:") and target in line:
        return True, contact_info
```

### 8. Quais são as diferenças entre API GA e Preview?

| Aspecto | API GA (gpt-realtime) | API Preview |
|---------|----------------------|-------------|
| Header | Sem `OpenAI-Beta` | Requer `OpenAI-Beta: realtime=v1` |
| session.type | Obrigatório (`"realtime"`) | Não existe |
| Audio format | `audio.input.format`, `audio.output.format` | `input_audio_format`, `output_audio_format` |
| Voice | `audio.output.voice` | `voice` (raiz) |
| turn_detection | `audio.input.turn_detection` | `turn_detection` (raiz) |
| Custo | ~20% menor | - |

### 9. Quais vozes funcionam na API GA?

Vozes válidas: `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin`, `cedar`

**Recomendada para pt-BR**: `marin` (feminina, natural)

### 10. Como configurar para produção?

```bash
# Variáveis de ambiente essenciais
OPENAI_API_KEY=sk-...
OPENAI_REALTIME_MODEL=gpt-realtime
OPENAI_REALTIME_VOICE=marin

# FreeSWITCH ESL
ESL_HOST=host.docker.internal
ESL_PORT=8021
ESL_PASSWORD=ClueCon

# WebSocket
REALTIME_HOST=0.0.0.0
REALTIME_PORT=8085

# AEC
AEC_ENABLED=true
AEC_ECHO_DELAY_MS=200
```

### 11. Como monitorar o sistema?

Métricas Prometheus disponíveis em `:8100/metrics`:

| Métrica | Descrição |
|---------|-----------|
| `voice_ai_sessions_active` | Sessões ativas |
| `voice_ai_latency_seconds` | Latência por provider |
| `voice_ai_audio_chunks_total` | Chunks processados |
| `voice_ai_transfers_total` | Transferências por status |
| `voice_ai_errors_total` | Erros por tipo |

### 12. Qual a diferença entre ESL Outbound e Inbound?

| Aspecto | ESL Outbound | ESL Inbound |
|---------|--------------|-------------|
| Direção | FS → Python | Python → FS |
| Porta | 8022 | 8021 |
| Conexão | FS inicia | Python inicia |
| Uso | Eventos, hold/unhold | Comandos API |
| Disponibilidade | Sempre (durante chamada) | Depende de conexão |

O `ESLHybridAdapter` tenta Outbound primeiro, fallback para Inbound.

---

*Documentação atualizada em Janeiro 2026*
