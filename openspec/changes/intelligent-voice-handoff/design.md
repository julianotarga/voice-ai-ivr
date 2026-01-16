# Design: Sistema de Handoff Inteligente de Voz

## Metadata
- **Proposal:** intelligent-voice-handoff/proposal.md
- **Author:** Claude AI + Juliano Targa
- **Created:** 2026-01-16
- **Status:** PROPOSED

## Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ARQUITETURA DO HANDOFF                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    SIP     ┌─────────────────┐   WebSocket                 │
│  │   Cliente   │◄──────────►│   FreeSWITCH    │◄────────────┐               │
│  │  (Telefone) │            │   + mod_audio   │             │               │
│  └─────────────┘            └────────┬────────┘             │               │
│                                      │                      │               │
│                                      │ ESL                  │               │
│                                      ▼                      ▼               │
│                             ┌─────────────────────────────────┐             │
│                             │      Voice AI Realtime          │             │
│                             │  ┌───────────────────────────┐  │             │
│                             │  │    TransferManager        │  │             │
│                             │  │  - Mapeia destinos        │  │             │
│                             │  │  - Executa transfer       │  │             │
│                             │  │  - Monitora resultado     │  │             │
│                             │  └───────────────────────────┘  │             │
│                             │  ┌───────────────────────────┐  │             │
│                             │  │    RecordingManager       │  │             │
│                             │  │  - Controla gravação      │  │             │
│                             │  │  - Upload MinIO           │  │             │
│                             │  └───────────────────────────┘  │             │
│                             └──────────────┬──────────────────┘             │
│                                            │                                │
│                                            │ HTTP/REST                      │
│                                            ▼                                │
│                             ┌─────────────────────────────────┐             │
│                             │      OmniPlay Backend           │             │
│                             │  - VoiceHandoffService          │             │
│                             │  - VoiceRecordingService        │             │
│                             │  - Ticket + Message             │             │
│                             └─────────────────────────────────┘             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Componentes Detalhados

### 1. Tabela de Destinos de Transferência

#### Schema SQL (FusionPBX)

```sql
-- Migration: 001_create_voice_transfer_destinations.sql

CREATE TABLE IF NOT EXISTS v_voice_transfer_destinations (
    transfer_destination_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid) ON DELETE CASCADE,
    secretary_uuid UUID REFERENCES v_voice_secretaries(voice_secretary_uuid) ON DELETE SET NULL,
    
    -- Identificação por voz/texto (para o LLM entender)
    name VARCHAR(100) NOT NULL,
    aliases JSONB DEFAULT '[]',  -- ["jeni", "jeniffer", "jennifer", "financeiro"]
    
    -- Destino FreeSWITCH
    destination_type VARCHAR(20) NOT NULL CHECK (destination_type IN (
        'extension',      -- Ramal individual
        'ring_group',     -- Grupo de toque
        'queue',          -- Fila de callcenter
        'external',       -- Número externo
        'voicemail'       -- Caixa postal
    )),
    destination_number VARCHAR(50) NOT NULL,  -- 1004, 9000, 5551999999999
    destination_context VARCHAR(50) DEFAULT 'default',
    
    -- Configurações de transfer
    ring_timeout_seconds INT DEFAULT 30,
    max_retries INT DEFAULT 1,
    retry_delay_seconds INT DEFAULT 5,
    
    -- Fallback quando não atende
    fallback_action VARCHAR(30) DEFAULT 'offer_ticket' CHECK (fallback_action IN (
        'offer_ticket',   -- Perguntar se quer deixar recado
        'create_ticket',  -- Criar ticket automaticamente
        'voicemail',      -- Transferir para voicemail
        'return_agent',   -- Voltar ao agente IA
        'hangup'          -- Desligar
    )),
    
    -- Metadados para contexto do agente
    department VARCHAR(100),
    role VARCHAR(100),           -- "Atendente", "Gerente", "Técnico"
    description TEXT,
    working_hours JSONB,         -- {"start": "08:00", "end": "18:00", "days": [1,2,3,4,5]}
    
    -- Controle
    priority INT DEFAULT 100,    -- Para ordenação quando múltiplos matches
    is_enabled BOOLEAN DEFAULT true,
    is_default BOOLEAN DEFAULT false,  -- Destino padrão quando não especificado
    
    -- Auditoria
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Índices
CREATE INDEX idx_vtd_domain ON v_voice_transfer_destinations(domain_uuid);
CREATE INDEX idx_vtd_secretary ON v_voice_transfer_destinations(secretary_uuid);
CREATE INDEX idx_vtd_enabled ON v_voice_transfer_destinations(is_enabled) WHERE is_enabled = true;

-- Destino padrão único por domain
CREATE UNIQUE INDEX idx_vtd_default ON v_voice_transfer_destinations(domain_uuid) 
    WHERE is_default = true AND is_enabled = true;
```

#### Exemplos de Dados

```sql
-- Fila genérica de atendimento (default)
INSERT INTO v_voice_transfer_destinations VALUES (
    gen_random_uuid(),
    'domain-uuid-here',
    'secretary-uuid-here',
    'Atendimento',
    '["atendimento", "atendente", "alguém", "pessoa", "humano", "operador"]',
    'ring_group',
    '9000',
    'default',
    30, 1, 5,
    'offer_ticket',
    'Atendimento Geral',
    'Atendente',
    'Fila principal de atendimento',
    '{"start": "08:00", "end": "18:00", "days": [1,2,3,4,5]}',
    100, true, true,
    NOW(), NOW()
);

-- Jeni do Financeiro
INSERT INTO v_voice_transfer_destinations VALUES (
    gen_random_uuid(),
    'domain-uuid-here',
    'secretary-uuid-here',
    'Jeni - Financeiro',
    '["jeni", "jeniffer", "jennifer", "financeiro", "contas", "boleto", "pagamento"]',
    'extension',
    '1004',
    'default',
    25, 2, 3,
    'offer_ticket',
    'Financeiro',
    'Analista Financeiro',
    'Responsável por cobranças e boletos',
    '{"start": "08:00", "end": "17:00", "days": [1,2,3,4,5]}',
    50, true, false,
    NOW(), NOW()
);

-- Suporte Técnico
INSERT INTO v_voice_transfer_destinations VALUES (
    gen_random_uuid(),
    'domain-uuid-here',
    'secretary-uuid-here',
    'Suporte Técnico',
    '["suporte", "técnico", "problema", "internet", "conexão", "lento", "caiu"]',
    'queue',
    '5001',
    'default',
    45, 1, 5,
    'create_ticket',
    'Suporte',
    'Técnico',
    'Equipe de suporte técnico',
    '{"start": "00:00", "end": "23:59", "days": [0,1,2,3,4,5,6]}',
    75, true, false,
    NOW(), NOW()
);
```

### 2. TransferManager (Python)

```python
# voice-ai-service/realtime/handlers/transfer_manager.py

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum
import asyncio
import logging

logger = logging.getLogger(__name__)


class TransferResult(Enum):
    """Resultado da tentativa de transferência."""
    SUCCESS = "success"           # Atendeu, bridge estabelecido
    BUSY = "busy"                 # Ramal ocupado
    NO_ANSWER = "no_answer"       # Não atendeu (timeout)
    UNAVAILABLE = "unavailable"   # Ramal offline/inexistente
    REJECTED = "rejected"         # Recusou a chamada
    FAILED = "failed"             # Erro técnico


@dataclass
class TransferDestination:
    """Destino de transferência."""
    uuid: str
    name: str
    aliases: List[str]
    destination_type: str
    destination_number: str
    destination_context: str
    ring_timeout_seconds: int
    max_retries: int
    fallback_action: str
    department: Optional[str]
    role: Optional[str]
    description: Optional[str]


@dataclass
class TransferAttempt:
    """Registro de uma tentativa de transferência."""
    destination: TransferDestination
    result: TransferResult
    duration_seconds: float
    sip_code: Optional[int]
    error_message: Optional[str]


class TransferManager:
    """
    Gerencia transferências de chamada para destinos configurados.
    
    Responsabilidades:
    1. Carregar destinos do banco de dados
    2. Encontrar destino por nome/alias (fuzzy match)
    3. Executar transferência via FreeSWITCH ESL
    4. Monitorar resultado e retornar status
    """
    
    def __init__(
        self,
        domain_uuid: str,
        secretary_uuid: str,
        call_uuid: str,
        esl_client: Any  # FreeSWITCH ESL client
    ):
        self.domain_uuid = domain_uuid
        self.secretary_uuid = secretary_uuid
        self.call_uuid = call_uuid
        self.esl = esl_client
        self._destinations: List[TransferDestination] = []
        self._loaded = False
    
    async def load_destinations(self) -> None:
        """Carrega destinos do banco de dados."""
        # Query ao PostgreSQL do FusionPBX
        query = """
            SELECT * FROM v_voice_transfer_destinations
            WHERE domain_uuid = $1
            AND (secretary_uuid = $2 OR secretary_uuid IS NULL)
            AND is_enabled = true
            ORDER BY priority ASC
        """
        # ... implementação de query
        self._loaded = True
    
    def find_destination(self, user_text: str) -> Optional[TransferDestination]:
        """
        Encontra destino que melhor corresponde ao texto do usuário.
        
        Exemplos:
        - "quero falar com a Jeni" → match "jeni" → ramal 1004
        - "preciso do financeiro" → match "financeiro" → ramal 1004
        - "quero falar com alguém" → match default → ring_group 9000
        """
        text_lower = user_text.lower()
        
        # 1. Busca exata em aliases
        for dest in self._destinations:
            for alias in dest.aliases:
                if alias.lower() in text_lower:
                    logger.info(f"Transfer destination found: {dest.name} (alias: {alias})")
                    return dest
        
        # 2. Busca no nome
        for dest in self._destinations:
            if dest.name.lower() in text_lower:
                return dest
        
        # 3. Retornar default se existir
        for dest in self._destinations:
            if dest.fallback_action == "offer_ticket":  # is_default
                return dest
        
        return None
    
    async def execute_transfer(
        self,
        destination: TransferDestination,
        announce_message: Optional[str] = None
    ) -> TransferAttempt:
        """
        Executa transferência attended para o destino.
        
        Fluxo:
        1. Coloca chamada original em hold (música de espera)
        2. Origina nova chamada para o destino
        3. Aguarda resultado (atendeu/ocupado/timeout)
        4. Se atendeu: bridge as chamadas
        5. Se não: retorna resultado para o agente IA
        """
        logger.info(f"Executing transfer to {destination.name} ({destination.destination_number})")
        
        start_time = asyncio.get_event_loop().time()
        
        try:
            # 1. Colocar chamada em hold
            await self._hold_call()
            
            # 2. Anunciar transferência (opcional)
            if announce_message:
                await self._play_announcement(announce_message)
            
            # 3. Originar chamada para destino
            result = await self._originate_to_destination(destination)
            
            duration = asyncio.get_event_loop().time() - start_time
            
            return TransferAttempt(
                destination=destination,
                result=result.status,
                duration_seconds=duration,
                sip_code=result.sip_code,
                error_message=result.error
            )
            
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            duration = asyncio.get_event_loop().time() - start_time
            return TransferAttempt(
                destination=destination,
                result=TransferResult.FAILED,
                duration_seconds=duration,
                sip_code=None,
                error_message=str(e)
            )
    
    async def _hold_call(self) -> None:
        """Coloca a chamada em hold com música de espera."""
        # FreeSWITCH: uuid_hold <uuid>
        await self.esl.execute("uuid_hold", self.call_uuid)
    
    async def _unhold_call(self) -> None:
        """Retira a chamada do hold."""
        await self.esl.execute("uuid_hold", f"off {self.call_uuid}")
    
    async def _originate_to_destination(self, dest: TransferDestination) -> Any:
        """
        Origina chamada para o destino e monitora resultado.
        
        FreeSWITCH Originate:
        originate {origination_uuid=xxx,call_timeout=30}sofia/internal/1004@domain 
            &bridge({origination_uuid=yyy}sofia/internal/caller@domain)
        """
        dial_string = self._build_dial_string(dest)
        timeout = dest.ring_timeout_seconds
        
        # Usar att_xfer (attended transfer) ou bridge
        # att_xfer aguarda atendimento antes de conectar
        
        # Método 1: uuid_bridge com monitoramento
        # Método 2: originate com callback de eventos
        
        # Monitorar eventos: CHANNEL_ANSWER, CHANNEL_HANGUP, etc.
        result = await self.esl.originate_and_monitor(
            dial_string=dial_string,
            timeout=timeout,
            caller_uuid=self.call_uuid
        )
        
        return result
    
    def _build_dial_string(self, dest: TransferDestination) -> str:
        """Constrói dial string para o destino."""
        if dest.destination_type == "extension":
            return f"user/{dest.destination_number}@{dest.destination_context}"
        elif dest.destination_type == "ring_group":
            return f"group/{dest.destination_number}@{dest.destination_context}"
        elif dest.destination_type == "queue":
            return f"fifo/{dest.destination_number}@{dest.destination_context}"
        elif dest.destination_type == "external":
            return f"sofia/gateway/default/{dest.destination_number}"
        else:
            return f"user/{dest.destination_number}@{dest.destination_context}"
    
    def get_status_message(self, result: TransferResult, dest: TransferDestination) -> str:
        """Retorna mensagem amigável para o cliente baseada no resultado."""
        name = dest.name.split(" - ")[0]  # "Jeni - Financeiro" → "Jeni"
        
        messages = {
            TransferResult.SUCCESS: f"Conectando você com {name}...",
            TransferResult.BUSY: f"O ramal de {name} está ocupado no momento.",
            TransferResult.NO_ANSWER: f"{name} não está disponível no momento.",
            TransferResult.UNAVAILABLE: f"{name} não está online no momento.",
            TransferResult.REJECTED: f"{name} não pode atender agora.",
            TransferResult.FAILED: "Desculpe, não foi possível completar a transferência.",
        }
        
        return messages.get(result, "Não foi possível completar a transferência.")
    
    def get_fallback_message(self, dest: TransferDestination) -> str:
        """Retorna mensagem de fallback baseada na configuração."""
        if dest.fallback_action == "offer_ticket":
            return "Gostaria de deixar um recado? Vou anotar e encaminhar para retorno."
        elif dest.fallback_action == "voicemail":
            return "Vou transferir para a caixa postal."
        else:
            return "Posso ajudar com mais alguma coisa?"
```

### 3. Integração com Session (Python)

```python
# voice-ai-service/realtime/session.py - Modificações

class RealtimeSession:
    
    async def _handle_transfer_intent(self, user_text: str) -> None:
        """
        Chamado quando o LLM detecta intenção de transferência.
        
        O LLM pode chamar a function "transfer_call" ou detectar keywords.
        """
        if not self._transfer_manager:
            # Fallback: criar ticket
            await self._initiate_handoff(reason="no_transfer_manager")
            return
        
        # 1. Encontrar destino
        destination = self._transfer_manager.find_destination(user_text)
        
        if not destination:
            # Sem destino configurado - usar default ou criar ticket
            await self._send_to_provider(
                "Não encontrei esse contato. Posso transferir para o atendimento geral?"
            )
            return
        
        # 2. Informar cliente que vai transferir
        await self._send_to_provider(
            f"Um momento, vou transferir você para {destination.name}..."
        )
        
        # 3. Pausar o áudio do agente IA
        await self._pause_agent()
        
        # 4. Executar transferência
        attempt = await self._transfer_manager.execute_transfer(destination)
        
        # 5. Processar resultado
        if attempt.result == TransferResult.SUCCESS:
            # Bridge estabelecido - encerrar sessão do agente
            logger.info("Transfer successful, ending AI session")
            await self.stop("transfer_success")
            return
        
        # 6. Transfer falhou - retornar ao agente
        await self._resume_agent()
        
        # 7. Informar cliente sobre o resultado
        status_msg = self._transfer_manager.get_status_message(
            attempt.result, destination
        )
        await self._send_to_provider(status_msg)
        
        # 8. Oferecer alternativa baseada em fallback_action
        if destination.fallback_action == "offer_ticket":
            fallback_msg = self._transfer_manager.get_fallback_message(destination)
            await self._send_to_provider(fallback_msg)
            self._awaiting_ticket_confirmation = True
            self._pending_ticket_destination = destination
        
        elif destination.fallback_action == "create_ticket":
            # Criar ticket automaticamente
            await self._initiate_handoff(
                reason=f"transfer_failed:{attempt.result.value}",
                intended_destination=destination.name
            )
        
        elif destination.fallback_action == "voicemail":
            await self._transfer_to_voicemail(destination)
```

### 4. Gravação de Chamada

```python
# voice-ai-service/realtime/handlers/recording_manager.py

import os
import asyncio
from datetime import datetime
from typing import Optional
import aiofiles

from ..utils.minio_uploader import get_minio_uploader


class RecordingManager:
    """
    Gerencia gravação de chamadas.
    
    O FreeSWITCH já pode estar gravando via dialplan.
    Este manager apenas:
    1. Verifica se gravação existe
    2. Faz upload para MinIO
    3. Retorna URL para anexar ao ticket
    """
    
    def __init__(
        self,
        domain_uuid: str,
        call_uuid: str,
        recordings_path: str = "/var/lib/freeswitch/recordings"
    ):
        self.domain_uuid = domain_uuid
        self.call_uuid = call_uuid
        self.recordings_path = recordings_path
        self._recording_file: Optional[str] = None
    
    def get_recording_path(self) -> Optional[str]:
        """Busca arquivo de gravação por call_uuid."""
        # Padrões comuns de nome de arquivo:
        # - {call_uuid}.wav
        # - {domain}/{date}/{call_uuid}.wav
        # - {domain}/archive/{year}/{month}/{day}/{call_uuid}.wav
        
        patterns = [
            f"{self.recordings_path}/{self.call_uuid}.wav",
            f"{self.recordings_path}/{self.call_uuid}.mp3",
            f"{self.recordings_path}/{self.domain_uuid}/{self.call_uuid}.wav",
        ]
        
        for pattern in patterns:
            if os.path.exists(pattern):
                return pattern
        
        # Buscar recursivamente
        for root, dirs, files in os.walk(self.recordings_path):
            for file in files:
                if self.call_uuid in file:
                    return os.path.join(root, file)
        
        return None
    
    async def upload_recording(self) -> Optional[str]:
        """
        Faz upload da gravação para MinIO.
        
        Returns:
            URL pública da gravação ou None se não encontrada
        """
        recording_path = self.get_recording_path()
        
        if not recording_path:
            logger.warning(f"Recording not found for call {self.call_uuid}")
            return None
        
        uploader = get_minio_uploader()
        
        if not uploader.is_available:
            logger.warning("MinIO uploader not available")
            return None
        
        # Gerar nome único no MinIO
        ext = os.path.splitext(recording_path)[1]
        date_prefix = datetime.now().strftime("%Y/%m/%d")
        object_name = f"voice/{self.domain_uuid}/{date_prefix}/{self.call_uuid}{ext}"
        
        # Upload
        async with aiofiles.open(recording_path, "rb") as f:
            data = await f.read()
        
        content_type = "audio/wav" if ext == ".wav" else "audio/mpeg"
        
        result = await uploader.upload_object(
            object_name=object_name,
            data=data,
            content_type=content_type,
            metadata={
                "call-uuid": self.call_uuid,
                "domain-uuid": self.domain_uuid,
            }
        )
        
        if result.success:
            logger.info(f"Recording uploaded: {result.url}")
            return result.url
        
        logger.error(f"Recording upload failed: {result.error}")
        return None
    
    async def start_recording(self) -> bool:
        """
        Inicia gravação via ESL (se não estiver gravando).
        
        Comando FreeSWITCH:
        uuid_record <uuid> start /path/to/recording.wav
        """
        # Verificar se já está gravando
        # Se não, iniciar via ESL
        pass
    
    async def stop_recording(self) -> Optional[str]:
        """
        Para gravação e retorna caminho do arquivo.
        
        Comando FreeSWITCH:
        uuid_record <uuid> stop /path/to/recording.wav
        """
        pass
```

### 5. Modificações no Dialplan (FreeSWITCH)

```lua
-- /usr/share/freeswitch/scripts/voice_secretary.lua
-- Modificações para suportar gravação e transfer

-- Iniciar gravação no início da chamada
local recording_path = "/var/lib/freeswitch/recordings/" .. domain_uuid
local recording_file = recording_path .. "/" .. call_uuid .. ".wav"

-- Criar diretório se não existir
os.execute("mkdir -p " .. recording_path)

-- Iniciar gravação
session:execute("record_session", recording_file)
freeswitch.consoleLog("INFO", "[VoiceSecretary] Recording started: " .. recording_file .. "\n")

-- ... resto do script existente ...

-- Parar gravação ao encerrar
-- (automático com record_session)
```

### 6. Interface FusionPBX

#### Arquivos PHP

```
fusionpbx-app/voice_transfer_destinations/
├── app_config.php
├── app_languages.php
├── voice_transfer_destination_edit.php
├── voice_transfer_destinations.php
└── resources/
    └── classes/
        └── voice_transfer_destinations.php
```

#### Layout da Página

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Voice Transfer Destinations                                    [+ Add]  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────┬───────────────┬──────────┬─────────┬────────┬──────────┐  │
│  │ Enabled │ Name          │ Type     │ Number  │ Timeout│ Fallback │  │
│  ├─────────┼───────────────┼──────────┼─────────┼────────┼──────────┤  │
│  │ ✅      │ Atendimento ⭐│ RingGroup│ 9000    │ 30s    │ Ticket   │  │
│  │ ✅      │ Jeni          │ Extension│ 1004    │ 25s    │ Ticket   │  │
│  │ ✅      │ Suporte       │ Queue    │ 5001    │ 45s    │ Ticket   │  │
│  │ ❌      │ Vendas        │ RingGroup│ 9001    │ 30s    │ Voicemail│  │
│  └─────────┴───────────────┴──────────┴─────────┴────────┴──────────┘  │
│                                                                         │
│  ⭐ = Default destination                                               │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Decisões de Design

### Decision 1: Attended vs Blind Transfer

**Escolhido: Attended Transfer**

- O agente IA aguarda confirmação de atendimento
- Se não atender, pode informar o cliente e oferecer alternativas
- Melhor experiência do usuário

### Decision 2: Onde iniciar a gravação

**Escolhido: No dialplan, antes do audio_stream**

- Garante que toda a conversa seja gravada
- Independente de falhas no Voice AI
- Formato nativo do FreeSWITCH (WAV)

### Decision 3: Fallback quando transfer falha

**Escolhido: Configurável por destino**

- Cada destino pode ter comportamento diferente
- Suporte pode criar ticket automaticamente
- Financeiro pode perguntar se quer deixar recado

### Decision 4: Como o LLM identifica o destino

**Escolhido: Function calling + keywords**

- LLM recebe lista de destinos no system prompt
- Pode chamar `transfer_call(destination: "jeni")` 
- Fallback: match de keywords nos aliases

## Sequência de Eventos

```
Cliente                    FreeSWITCH              Voice AI             LLM              OmniPlay
   │                           │                      │                   │                  │
   │──── Liga ────────────────►│                      │                   │                  │
   │                           │──── Audio Stream ───►│                   │                  │
   │                           │◄─── Greeting ────────│◄── Saudação ──────│                  │
   │◄── "Olá, sou a..." ───────│                      │                   │                  │
   │                           │                      │                   │                  │
   │── "Quero falar c/ Jeni" ─►│────────────────────►│─── Transcrição ──►│                  │
   │                           │                      │◄── transfer_call ─│                  │
   │                           │                      │    (jeni)         │                  │
   │                           │                      │                   │                  │
   │◄─ "Transferindo..." ──────│◄────────────────────│                   │                  │
   │                           │                      │                   │                  │
   │                           │──── HOLD ───────────►│                   │                  │
   │◄── [Música de espera] ────│                      │                   │                  │
   │                           │                      │                   │                  │
   │                           │──── Originate ──────►│                   │                  │
   │                           │     user/1004        │                   │                  │
   │                           │                      │                   │                  │
   │                           │◄─── NO_ANSWER ───────│                   │                  │
   │                           │     (timeout 25s)    │                   │                  │
   │                           │                      │                   │                  │
   │                           │──── UNHOLD ─────────►│                   │                  │
   │◄─ "Jeni não disponível" ──│◄────────────────────│                   │                  │
   │◄─ "Quer deixar recado?" ──│◄────────────────────│                   │                  │
   │                           │                      │                   │                  │
   │── "Sim, pode anotar" ─────►│────────────────────►│─── Transcrição ──►│                  │
   │◄─ "Ok, estou anotando" ───│◄────────────────────│◄── Resposta ──────│                  │
   │                           │                      │                   │                  │
   │── "Preciso do boleto" ────►│────────────────────►│─── Transcrição ──►│                  │
   │◄─ "Anotado. Mais algo?" ──│◄────────────────────│◄── Resposta ──────│                  │
   │                           │                      │                   │                  │
   │── "Não, obrigado" ────────►│────────────────────►│─── Transcrição ──►│                  │
   │◄─ "Recado registrado" ────│◄────────────────────│◄── Resposta ──────│                  │
   │                           │                      │                   │                  │
   │                           │                      │─── Criar Ticket ─────────────────────►│
   │                           │                      │    + Áudio                            │
   │                           │                      │    + Transcrição                      │
   │                           │                      │◄── Ticket #123 ───────────────────────│
   │                           │                      │                   │                  │
   │◄─ "Tchau!" ───────────────│◄────────────────────│                   │                  │
   │                           │◄─── HANGUP ─────────│                   │                  │
   │                           │                      │                   │                  │
```

## Configuração do System Prompt

O LLM precisa saber quais destinos estão disponíveis:

```
Você é a secretária virtual da empresa XYZ.

## Destinos de Transferência Disponíveis

Quando o cliente quiser falar com alguém, use a função `transfer_call`:

| Nome | Departamento | Função |
|------|--------------|--------|
| Jeni | Financeiro | Boletos, pagamentos, cobranças |
| Suporte | Técnico | Problemas de conexão, lentidão |
| Atendimento | Geral | Dúvidas gerais, informações |

Exemplos:
- "Quero falar com a Jeni" → transfer_call(destination="jeni")
- "Preciso do financeiro" → transfer_call(destination="jeni") 
- "Minha internet caiu" → transfer_call(destination="suporte")
- "Quero falar com alguém" → transfer_call(destination="atendimento")

Antes de transferir, confirme com o cliente:
"Vou transferir você para [Nome] do [Departamento]. Um momento..."
```

## Próximos Passos

1. Criar migration SQL para `v_voice_transfer_destinations`
2. Implementar `TransferManager` em Python
3. Modificar `RealtimeSession` para usar TransferManager
4. Implementar `RecordingManager` 
5. Criar interface FusionPBX para gerenciamento
6. Modificar dialplan para gravação
7. Testes end-to-end
