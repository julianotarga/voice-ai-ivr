# 🎯 Guia de Implementação: Transferência Anunciada com mod_conference

## 📋 Sumário Executivo

**Para**: Cursor AI (Claude Sonnet 4.5)  
**Objetivo**: Implementar transferência anunciada robusta usando mod_conference do FreeSWITCH  
**Tempo Estimado**: 4-6 horas de desenvolvimento  
**Complexidade**: Média  

### Problema Atual
```
Cliente → Secretária IA → Transfer com &park()
                              ↓
                         B-leg MUDO (WebSocket não conecta)
                         A-leg PRESO após B desligar
```

### Solução Proposta
```
Cliente → Secretária IA → Transfer com mod_conference
                              ↓
                         B-leg em conferência (áudio OK)
                         OpenAI anuncia via uuid_audio_stream
                         B aceita/recusa via function call
                         Cleanup automático
```

---

## 🏗️ Arquitetura da Solução

```
┌────────────────────────────────────────────────────────────────┐
│                  FLUXO COMPLETO DA TRANSFERÊNCIA                │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1️⃣ Cliente solicita transferência para "vendas"               │
│      ↓                                                          │
│  2️⃣ Criar conferência temporária: transfer_UUID_TIMESTAMP       │
│      ↓                                                          │
│  3️⃣ Mover A-leg (cliente) para conferência com flags:          │
│      • mute (não pode falar ainda)                             │
│      • moderator (controla conferência)                        │
│      ↓                                                          │
│  4️⃣ Originar B-leg (atendente) direto para conferência         │
│      • SEM flags (pode falar e ouvir normalmente)              │
│      ↓                                                          │
│  5️⃣ Conectar OpenAI Realtime ao B-leg via uuid_audio_stream    │
│      ↓                                                          │
│  6️⃣ OpenAI anuncia: "Você tem um cliente na linha..."          │
│      ↓                                                          │
│  7️⃣ Aguardar decisão do B-leg (timeout: 15s)                   │
│      ↓                                                          │
│  ┌───┴───────────────────────┐                                 │
│  ↓                           ↓                                 │
│  B ACEITA                    B RECUSA / TIMEOUT                │
│  ↓                           ↓                                 │
│  8a️⃣ Unmute A-leg           8b️⃣ Kick B-leg                     │
│  Conversa continua           Criar ticket no OmniPlay          │
│                              Informar A-leg                    │
│                              Retornar A ao IVR                 │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

---

## 📂 Estrutura de Arquivos

### Arquivos a CRIAR:
```
voice-ai-service/
├── realtime/
│   └── handlers/
│       └── transfer_manager_conference.py  ← NOVO (arquivo principal)
```

### Arquivos a MODIFICAR:
```
voice-ai-service/
├── realtime/
│   ├── handlers/
│   │   └── realtime_announcement.py  ← ADAPTAR para conferência
│   └── session.py  ← INTEGRAR novo transfer manager
```

---

## 🔨 PARTE 1: Criar ConferenceTransferManager

### Arquivo: `realtime/handlers/transfer_manager_conference.py`

Este é o arquivo PRINCIPAL da solução. Copie o código abaixo COMPLETO:

```python
"""
Transferência anunciada usando mod_conference do FreeSWITCH.

Substitui a abordagem de &park() que apresentava problemas de áudio.
Usa conferência temporária para conectar A-leg (cliente) e B-leg (atendente).
"""

import asyncio
import logging
import time
from typing import Optional, Dict
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)


class TransferDecision(Enum):
    """Decisão do atendente sobre a transferência."""
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class TransferResult:
    """Resultado da transferência anunciada."""
    success: bool
    decision: TransferDecision
    b_leg_uuid: Optional[str] = None
    conference_name: Optional[str] = None
    error: Optional[str] = None
    ticket_id: Optional[str] = None


class ConferenceTransferManager:
    """
    Gerencia transferências anunciadas usando mod_conference.
    
    Fluxo:
    1. Cria conferência temporária
    2. Move A-leg para conferência (muted)
    3. Origina B-leg para conferência
    4. OpenAI anuncia para B-leg
    5. B-leg aceita/recusa
    6. Processa decisão
    """
    
    def __init__(
        self,
        esl_client,  # ESLClient instance
        a_leg_uuid: str,
        domain: str,
        caller_id: str,
        openai_config: dict,
        omniplay_api = None
    ):
        """
        Inicializa o transfer manager.
        
        Args:
            esl_client: Cliente ESL para comandos FreeSWITCH
            a_leg_uuid: UUID do A-leg (cliente)
            domain: Domínio SIP (ex: ativo.netplay.net.br)
            caller_id: Caller ID para originate
            openai_config: Config OpenAI (api_key, model, voice)
            omniplay_api: API OmniPlay para criar tickets (opcional)
        """
        self.esl = esl_client
        self.a_leg_uuid = a_leg_uuid
        self.domain = domain
        self.caller_id = caller_id
        self.openai_config = openai_config
        self.omniplay_api = omniplay_api
        
        # Estado da transferência
        self.b_leg_uuid: Optional[str] = None
        self.conference_name: Optional[str] = None
        self.announcement = None
    
    async def execute_announced_transfer(
        self,
        destination: str,
        context: str,
        timeout: float = 15.0
    ) -> TransferResult:
        """
        Executa transferência anunciada completa.
        
        Este é o método PRINCIPAL que orquestra todo o fluxo.
        
        Args:
            destination: Extensão destino (ex: "1001")
            context: Contexto da transferência (ex: "vendas")
            timeout: Timeout para resposta do B-leg (segundos)
            
        Returns:
            TransferResult com resultado da operação
        """
        logger.info("=" * 70)
        logger.info("🎯 ANNOUNCED TRANSFER - mod_conference")
        logger.info(f"A-leg UUID: {self.a_leg_uuid}")
        logger.info(f"Destination: {destination}@{self.domain}")
        logger.info(f"Context: {context}")
        logger.info(f"Timeout: {timeout}s")
        logger.info("=" * 70)
        
        try:
            # STEP 1: Criar conferência temporária
            self.conference_name = self._generate_conference_name()
            logger.info(f"📋 Step 1: Conference name: {self.conference_name}")
            
            # STEP 2: Mover A-leg para conferência (muted)
            await self._move_a_leg_to_conference()
            logger.info("✅ Step 2: A-leg in conference (muted)")
            
            # STEP 3: Originar B-leg para conferência
            self.b_leg_uuid = await self._originate_b_leg(destination)
            logger.info(f"✅ Step 3: B-leg originated: {self.b_leg_uuid}")
            
            # STEP 4: Aguardar B-leg estabilizar
            await asyncio.sleep(2)
            
            # STEP 5: Fazer anúncio via OpenAI Realtime
            decision = await self._announce_to_b_leg(context, timeout)
            logger.info(f"✅ Step 5: B-leg decision: {decision.value}")
            
            # STEP 6: Processar decisão
            result = await self._process_decision(decision, context)
            
            return result
            
        except Exception as e:
            logger.error(f"Transfer failed: {e}", exc_info=True)
            await self._cleanup_on_error()
            
            return TransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e)
            )
    
    def _generate_conference_name(self) -> str:
        """
        Gera nome único para conferência temporária.
        
        Format: transfer_SHORTID_TIMESTAMP
        
        Returns:
            str: Nome da conferência
        """
        short_id = self.a_leg_uuid[:8]
        timestamp = int(time.time())
        return f"transfer_{short_id}_{timestamp}"
    
    async def _move_a_leg_to_conference(self):
        """
        Move A-leg (cliente) para conferência com flags especiais.
        
        Flags:
        - mute: Cliente não pode falar (ainda)
        - moderator: Cliente tem controle da conferência
        
        A conferência será criada automaticamente quando A-leg entrar.
        """
        logger.info("📋 Step 2: Moving A-leg to conference...")
        
        # Comando: uuid_transfer UUID conference:NAME@PROFILE+flags{...} inline
        transfer_cmd = (
            f"{self.a_leg_uuid} "
            f"conference:{self.conference_name}@default+"
            f"flags{{mute,moderator}} inline"
        )
        
        try:
            result = await self.esl.execute("uuid_transfer", transfer_cmd)
            
            if "+OK" not in str(result):
                raise Exception(f"uuid_transfer failed: {result}")
            
            logger.info(f"A-leg moved to conference: {result}")
            
        except Exception as e:
            logger.error(f"Failed to move A-leg: {e}")
            raise
    
    async def _originate_b_leg(self, destination: str) -> str:
        """
        Origina B-leg (atendente) direto para conferência.
        
        B-leg entra SEM flags - pode falar e ouvir normalmente.
        
        Args:
            destination: Extensão destino (ex: "1001")
            
        Returns:
            str: UUID do B-leg
            
        Raises:
            Exception: Se originate falhar
        """
        logger.info("📋 Step 3: Originating B-leg...")
        
        # Gerar UUID para B-leg
        b_leg_uuid = str(uuid4())
        
        # Construir comando originate
        # Format: originate {vars}destination &app(args)
        originate_str = (
            f"{{origination_uuid={b_leg_uuid},"
            f"origination_caller_id_number={self.caller_id},"
            f"origination_caller_id_name=Secretaria_Virtual,"
            f"originate_timeout=30,"
            f"ignore_early_media=true}}"
            f"user/{destination}@{self.domain} "
            f"&conference({self.conference_name}@default)"
        )
        
        logger.debug(f"Originate command: {originate_str}")
        
        try:
            # Executar originate via bgapi (assíncrono)
            result = await self.esl.bgapi("originate", originate_str)
            
            logger.info(f"Originate result: {result}")
            
            # Verificar se sucesso
            if "-ERR" in str(result):
                raise Exception(f"Originate failed: {result}")
            
            return b_leg_uuid
            
        except Exception as e:
            logger.error(f"Failed to originate B-leg: {e}")
            raise
    
    async def _announce_to_b_leg(
        self,
        context: str,
        timeout: float
    ) -> TransferDecision:
        """
        Faz anúncio para B-leg via OpenAI Realtime.
        
        OpenAI irá:
        1. Conectar ao B-leg via uuid_audio_stream
        2. Falar: "Você tem um cliente na linha solicitando {context}"
        3. Aguardar resposta do B-leg
        4. Chamar function call accept_transfer() ou reject_transfer()
        
        Args:
            context: Contexto da transferência
            timeout: Timeout em segundos
            
        Returns:
            TransferDecision (ACCEPTED, REJECTED, TIMEOUT, ERROR)
        """
        logger.info("📋 Step 5: Announcing to B-leg via OpenAI...")
        
        # Importar aqui para evitar circular import
        from realtime.handlers.realtime_announcement import RealtimeAnnouncement
        
        # Mensagem inicial para OpenAI
        initial_message = (
            f"Olá, você tem um cliente na linha solicitando atendimento em {context}. "
            f"Se você pode atender agora, diga 'pode conectar' ou 'aceito'. "
            f"Se não pode atender, diga 'não posso' ou 'recuso'."
        )
        
        try:
            # Criar instância do announcement
            self.announcement = RealtimeAnnouncement(
                esl_client=self.esl,
                b_leg_uuid=self.b_leg_uuid,
                model=self.openai_config.get("model", "gpt-realtime"),
                voice=self.openai_config.get("voice", "marin"),
                initial_message=initial_message,
                timeout=timeout,
                api_key=self.openai_config.get("api_key")
            )
            
            # Executar anúncio (retorna TransferDecision)
            decision = await self.announcement.run()
            
            return decision
            
        except asyncio.TimeoutError:
            logger.warning(f"Announcement timeout after {timeout}s")
            return TransferDecision.TIMEOUT
            
        except Exception as e:
            logger.error(f"Announcement error: {e}")
            return TransferDecision.ERROR
    
    async def _process_decision(
        self,
        decision: TransferDecision,
        context: str
    ) -> TransferResult:
        """
        Processa decisão do B-leg.
        
        Args:
            decision: Decisão do atendente
            context: Contexto original
            
        Returns:
            TransferResult
        """
        logger.info(f"📋 Step 6: Processing decision: {decision.value}")
        
        if decision == TransferDecision.ACCEPTED:
            # B-leg aceitou
            return await self._handle_accepted()
            
        elif decision in [TransferDecision.REJECTED, TransferDecision.TIMEOUT]:
            # B-leg recusou ou timeout
            reason = "Atendente recusou" if decision == TransferDecision.REJECTED else "Timeout"
            return await self._handle_rejected(context, reason)
            
        else:  # ERROR
            return await self._handle_rejected(context, "Erro no anúncio")
    
    async def _handle_accepted(self) -> TransferResult:
        """
        B-leg aceitou - unmute A-leg para iniciar conversa.
        
        Returns:
            TransferResult com sucesso
        """
        logger.info("✅ Transfer ACCEPTED")
        
        try:
            # Desmute A-leg na conferência
            # Comando: conference NAME unmute UUID
            unmute_cmd = f"{self.conference_name} unmute {self.a_leg_uuid}"
            
            result = await self.esl.execute("conference", unmute_cmd)
            
            logger.info(f"A-leg unmuted: {result}")
            logger.info("🎉 Transfer completed - both parties can talk")
            
            return TransferResult(
                success=True,
                decision=TransferDecision.ACCEPTED,
                b_leg_uuid=self.b_leg_uuid,
                conference_name=self.conference_name
            )
            
        except Exception as e:
            logger.error(f"Failed to unmute A-leg: {e}")
            return TransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e)
            )
    
    async def _handle_rejected(
        self,
        context: str,
        reason: str
    ) -> TransferResult:
        """
        B-leg recusou/timeout - cleanup e criar ticket.
        
        Passos:
        1. Kick B-leg da conferência
        2. Criar ticket no OmniPlay
        3. Informar A-leg sobre ticket
        4. Retornar A-leg ao IVR
        
        Args:
            context: Contexto da transferência
            reason: Razão da rejeição
            
        Returns:
            TransferResult com ticket
        """
        logger.info(f"❌ Transfer REJECTED/TIMEOUT: {reason}")
        
        try:
            # 1. Kick B-leg
            if self.b_leg_uuid:
                kick_cmd = f"{self.conference_name} kick {self.b_leg_uuid}"
                await self.esl.execute("conference", kick_cmd)
                logger.info("B-leg kicked from conference")
            
            # 2. Criar ticket
            ticket_id = None
            if self.omniplay_api:
                ticket_id = await self._create_ticket(context, reason)
            
            # 3. Informar A-leg
            await self._announce_ticket_to_a_leg(ticket_id)
            
            # 4. Retornar A ao IVR
            await self._return_a_leg_to_ivr()
            
            return TransferResult(
                success=False,
                decision=TransferDecision.REJECTED,
                ticket_id=ticket_id
            )
            
        except Exception as e:
            logger.error(f"Error handling rejection: {e}")
            return TransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e)
            )
    
    async def _create_ticket(self, context: str, reason: str) -> Optional[str]:
        """
        Cria ticket no OmniPlay.
        
        Args:
            context: Contexto da solicitação
            reason: Razão do ticket
            
        Returns:
            str: ID do ticket ou None
        """
        logger.info("🎫 Creating ticket in OmniPlay...")
        
        try:
            ticket_data = {
                "caller_id": self.caller_id,
                "context": context,
                "reason": reason,
                "uuid": self.a_leg_uuid,
                "timestamp": time.time()
            }
            
            # Chamar API OmniPlay
            ticket = await self.omniplay_api.create_ticket(ticket_data)
            ticket_id = ticket.get("id")
            
            logger.info(f"✅ Ticket created: {ticket_id}")
            return ticket_id
            
        except Exception as e:
            logger.error(f"Failed to create ticket: {e}")
            return None
    
    async def _announce_ticket_to_a_leg(self, ticket_id: Optional[str]):
        """
        Anuncia para A-leg que ticket foi criado.
        
        IMPORTANTE: A-leg ainda está na conferência (muted).
        Usar uuid_broadcast para falar com A-leg.
        
        Args:
            ticket_id: ID do ticket ou None
        """
        if ticket_id:
            message = (
                f"Infelizmente não há atendentes disponíveis no momento. "
                f"Criamos o protocolo {ticket_id} e nossa equipe entrará em contato em breve."
            )
        else:
            message = (
                f"Infelizmente não há atendentes disponíveis no momento. "
                f"Por favor, tente novamente mais tarde."
            )
        
        logger.info(f"📢 Announcing ticket to A-leg: {ticket_id}")
        
        try:
            # TODO: Implementar TTS para gerar arquivo de áudio
            # Por enquanto, usar mensagem simples
            
            # Opção 1: Usar say (FreeSWITCH TTS)
            # say_cmd = f"say::pt::number::{ticket_id}"
            
            # Opção 2: Gerar arquivo WAV com OpenAI TTS
            # audio_file = await self._generate_tts(message)
            
            # Por enquanto, apenas log
            logger.info(f"TODO: Announce message: {message}")
            
            # Aguardar mensagem tocar
            await asyncio.sleep(3)
            
        except Exception as e:
            logger.error(f"Failed to announce ticket: {e}")
    
    async def _return_a_leg_to_ivr(self):
        """
        Retorna A-leg ao IVR/dialplan original.
        
        Kick A-leg da conferência, fazendo-o retornar ao dialplan.
        """
        logger.info("🔙 Returning A-leg to IVR...")
        
        try:
            # Kick A-leg da conferência
            kick_cmd = f"{self.conference_name} kick {self.a_leg_uuid}"
            await self.esl.execute("conference", kick_cmd)
            
            logger.info("A-leg returned to IVR")
            
        except Exception as e:
            logger.error(f"Failed to return A-leg: {e}")
    
    async def _cleanup_on_error(self):
        """
        Cleanup em caso de erro.
        
        Garante que:
        - Conferência seja destruída
        - B-leg seja desligado
        - A-leg retorne ao dialplan
        """
        logger.info("🧹 Cleaning up after error...")
        
        try:
            # Kick todos da conferência
            if self.conference_name:
                await self.esl.execute(
                    "conference",
                    f"{self.conference_name} kick all"
                )
            
            # Hangup B-leg
            if self.b_leg_uuid:
                await self.esl.execute("uuid_kill", self.b_leg_uuid)
            
            # Retornar A ao dialplan
            if self.a_leg_uuid:
                await self.esl.execute("uuid_break", self.a_leg_uuid)
            
            logger.info("Cleanup completed")
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
```

---

## 🔨 PARTE 2: Adaptar RealtimeAnnouncement

### Arquivo: `realtime/handlers/realtime_announcement.py`

**MODIFICAR** este arquivo para trabalhar com conferência ao invés de &park().

### Mudanças Necessárias:

#### 1. Adicionar Function Calls ao Session Config

```python
# No método _configure_session() ou equivalente

# Adicionar functions para accept/reject
functions = [
    {
        "type": "function",
        "name": "accept_transfer",
        "description": "Chamado quando atendente ACEITA a transferência",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "type": "function",
        "name": "reject_transfer",
        "description": "Chamado quando atendente RECUSA a transferência",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Motivo opcional da recusa"
                }
            }
        }
    }
]

session_config = {
    "type": "session.update",
    "session": {
        "type": "realtime",
        "modalities": ["audio", "text"],
        "instructions": (
            "Você está anunciando uma ligação para um atendente humano.\n"
            "Explique quem está ligando e o motivo da chamada.\n\n"
            "IMPORTANTE:\n"
            "- Se o atendente aceitar (dizer 'sim', 'aceito', 'pode conectar', etc), "
            "chame a função accept_transfer().\n"
            "- Se o atendente recusar (dizer 'não', 'não posso', 'recuso', etc), "
            "chame a função reject_transfer().\n\n"
            "Seja educado, profissional e objetivo."
        ),
        "voice": self.voice,
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "turn_detection": {"type": "server_vad"}
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000}
            }
        },
        "tools": functions,  # ← ADICIONAR
        "tool_choice": "auto"  # ← ADICIONAR
    }
}
```

#### 2. Adicionar Handler de Function Calls

```python
# Adicionar método para processar function calls

async def _handle_function_call(self, event: dict):
    """
    Processa function calls do OpenAI.
    
    Args:
        event: Evento de function call
    """
    function_name = event.get("name")
    call_id = event.get("call_id")
    
    logger.info(f"🔧 Function call received: {function_name}")
    
    # Processar decisão
    if function_name == "accept_transfer":
        self.decision = TransferDecision.ACCEPTED
        
    elif function_name == "reject_transfer":
        self.decision = TransferDecision.REJECTED
    
    # Enviar output da function (obrigatório)
    await self._send_function_output(call_id, {"status": "ok"})
    
    # Sinalizar que decisão foi tomada
    self.decision_event.set()

async def _send_function_output(self, call_id: str, output: dict):
    """Envia output da function call."""
    await self.ws.send_json({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(output)
        }
    })
```

#### 3. Modificar Loop de Eventos

```python
async def _process_openai_events(self):
    """Processa eventos do OpenAI WebSocket."""
    
    async for message in self.ws:
        event = json.loads(message.data)
        event_type = event.get("type")
        
        # ... outros handlers existentes ...
        
        # ADICIONAR: Handler de function calls
        if event_type == "response.function_call_arguments.done":
            await self._handle_function_call(event)
        
        # Se decisão foi tomada, interromper loop
        if self.decision_event.is_set():
            break
```

#### 4. Adicionar Método run()

```python
# Adicionar este método principal

async def run(self) -> TransferDecision:
    """
    Executa anúncio e aguarda decisão.
    
    Returns:
        TransferDecision (ACCEPTED, REJECTED, TIMEOUT, ERROR)
    """
    # Criar evento para sinalizar decisão
    self.decision_event = asyncio.Event()
    self.decision = None
    
    try:
        # 1. Conectar OpenAI
        await self._connect_openai()
        
        # 2. Configurar sessão
        await self._configure_session()
        
        # 3. Iniciar audio streaming
        await self._start_audio_streaming()
        
        # 4. Enviar mensagem inicial
        await self._send_initial_message()
        
        # 5. Processar eventos até decisão ou timeout
        await asyncio.wait_for(
            self._process_openai_events(),
            timeout=self.timeout
        )
        
        # Retornar decisão
        return self.decision or TransferDecision.ERROR
        
    except asyncio.TimeoutError:
        return TransferDecision.TIMEOUT
        
    except Exception as e:
        logger.error(f"Announcement error: {e}")
        return TransferDecision.ERROR
        
    finally:
        await self._cleanup()
```

---

## 🔨 PARTE 3: Integração no Sistema

### Arquivo: `realtime/session.py`

**MODIFICAR** o método que trata transferências.

#### Substituir Código Antigo:

```python
# ❌ CÓDIGO ANTIGO (remover)
async def _handle_transfer(self, destination: str, context: str):
    # ... código usando &park() ...
    transfer_manager = TransferManager(...)
    await transfer_manager.execute_transfer(...)
```

#### Por Código Novo:

```python
# ✅ CÓDIGO NOVO (usar)
async def _handle_transfer(self, destination: str, context: str):
    """Executa transferência anunciada usando conferência."""
    
    from realtime.handlers.transfer_manager_conference import (
        ConferenceTransferManager,
        TransferDecision
    )
    
    # Criar transfer manager
    manager = ConferenceTransferManager(
        esl_client=self.esl,
        a_leg_uuid=self.uuid,
        domain=self.domain,
        caller_id=self.caller_id,
        openai_config={
            "api_key": self.openai_api_key,
            "model": self.openai_model,
            "voice": self.openai_voice
        },
        omniplay_api=self.omniplay_api  # Se disponível
    )
    
    # Executar transferência
    result = await manager.execute_announced_transfer(
        destination=destination,
        context=context,
        timeout=15.0
    )
    
    # Processar resultado
    if result.success:
        logger.info(f"✅ Transfer successful: {result.conference_name}")
        # Sessão continua na conferência
        
    else:
        logger.warning(f"❌ Transfer failed: {result.decision.value}")
        
        if result.ticket_id:
            logger.info(f"📋 Ticket created: {result.ticket_id}")
        
        # A-leg foi retornado ao IVR automaticamente
```

---

## 🧪 PARTE 4: Testes

### Teste Manual 1: Transferência Aceita

```bash
# 1. Ligar para o sistema
# 2. Solicitar transferência para "vendas"
# 3. Atendente atende
# 4. OpenAI anuncia: "Você tem um cliente..."
# 5. Atendente fala: "Aceito" ou "Pode conectar"
# 6. Verificar: A-leg e B-leg conversam normalmente
```

**Logs Esperados:**
```
🎯 ANNOUNCED TRANSFER - mod_conference
📋 Step 1: Conference name: transfer_4730f4d2_1737392873
✅ Step 2: A-leg in conference (muted)
✅ Step 3: B-leg originated: 3258ae34-cf25-4786-94f3-ff661d5914ff
📋 Step 5: Announcing to B-leg via OpenAI...
🔧 Function call received: accept_transfer
✅ Step 5: B-leg decision: accepted
✅ Transfer ACCEPTED
🎉 Transfer completed - both parties can talk
```

### Teste Manual 2: Transferência Recusada

```bash
# 1. Ligar para o sistema
# 2. Solicitar transferência
# 3. Atendente atende
# 4. OpenAI anuncia
# 5. Atendente fala: "Não posso" ou "Recuso"
# 6. Verificar: Ticket criado, A-leg informado
```

**Logs Esperados:**
```
🔧 Function call received: reject_transfer
✅ Step 5: B-leg decision: rejected
❌ Transfer REJECTED/TIMEOUT: Atendente recusou
B-leg kicked from conference
🎫 Creating ticket in OmniPlay...
✅ Ticket created: TKT-12345
🔙 Returning A-leg to IVR...
```

### Teste Manual 3: Timeout

```bash
# 1. Ligar para o sistema
# 2. Solicitar transferência
# 3. Atendente atende mas não responde
# 4. Aguardar 15 segundos
# 5. Verificar: Timeout, ticket criado
```

**Logs Esperados:**
```
Announcement timeout after 15.0s
✅ Step 5: B-leg decision: timeout
❌ Transfer REJECTED/TIMEOUT: Timeout
```

---

## 📋 CHECKLIST DE IMPLEMENTAÇÃO

### Fase 1: Estrutura Base
- [ ] Criar arquivo `transfer_manager_conference.py`
- [ ] Copiar código da classe `ConferenceTransferManager`
- [ ] Verificar imports necessários

### Fase 2: RealtimeAnnouncement
- [ ] Adicionar functions ao session config
- [ ] Implementar `_handle_function_call()`
- [ ] Implementar `_send_function_output()`
- [ ] Adicionar handler no loop de eventos
- [ ] Implementar método `run()`

### Fase 3: Integração
- [ ] Modificar `session.py` para usar novo transfer manager
- [ ] Remover código antigo de &park()
- [ ] Verificar configurações OpenAI

### Fase 4: Testes
- [ ] Teste: Transferência aceita
- [ ] Teste: Transferência recusada
- [ ] Teste: Timeout
- [ ] Teste: B-leg desliga durante anúncio
- [ ] Teste: Erro de rede

### Fase 5: Refinamentos
- [ ] Implementar TTS para anúncio de ticket
- [ ] Ajustar timeouts conforme necessidade
- [ ] Adicionar métricas Prometheus
- [ ] Documentar configurações

---

## ⚠️ PONTOS DE ATENÇÃO

### 1. ESL Client
```python
# Verificar se métodos existem:
await self.esl.execute("uuid_transfer", ...)  # ✅ Deve existir
await self.esl.execute("conference", ...)     # ✅ Deve existir
await self.esl.bgapi("originate", ...)        # ✅ Deve existir
```

### 2. Imports
```python
# Adicionar no topo de transfer_manager_conference.py:
from realtime.handlers.realtime_announcement import RealtimeAnnouncement
from enum import Enum
from dataclasses import dataclass
from uuid import uuid4
```

### 3. OmniPlay API
```python
# Se omniplay_api não existir, criar mock:
if self.omniplay_api is None:
    # Simular criação de ticket
    ticket_id = f"TKT-{int(time.time())}"
```

### 4. Function Calls OpenAI
```python
# Garantir que modelo suporta function calls:
# ✅ gpt-realtime
# ✅ gpt-4o-realtime-preview
# ❌ Modelos antigos podem não suportar
```

---

## 🐛 DEBUGGING

### Logs Importantes

```python
# Adicionar logs em pontos críticos:

logger.info(f"Conference name: {self.conference_name}")
logger.info(f"A-leg UUID: {self.a_leg_uuid}")
logger.info(f"B-leg UUID: {self.b_leg_uuid}")
logger.info(f"Originate result: {result}")
logger.info(f"Function call: {function_name}")
logger.info(f"Decision: {self.decision}")
```

### Comandos FreeSWITCH CLI

```bash
# Verificar conferências ativas
fs_cli -x "conference list"

# Ver participantes de conferência
fs_cli -x "conference transfer_XXX list"

# Verificar canal
fs_cli -x "uuid_dump {uuid}"

# Matar conferência
fs_cli -x "conference transfer_XXX kick all"
```

---

## 📊 Diferenças: Antes vs Depois

| Aspecto | &park() (Antigo) | mod_conference (Novo) |
|---------|------------------|----------------------|
| **Áudio B-leg** | ❌ Mudo | ✅ Funciona |
| **WebSocket** | ⚠️ Necessário | ✅ Usa uuid_audio_stream |
| **Cleanup** | ❌ Manual/complexo | ✅ Automático |
| **A-leg preso** | ❌ Sim, após B desligar | ✅ Retorna ao IVR |
| **Fallback** | ❌ Difícil | ✅ Simples (ticket + IVR) |
| **Debugging** | 🔴 Difícil | ✅ Logs claros |
| **Confiabilidade** | 🔴 Baixa | ✅ Alta |

---

## 🎯 Resultado Final Esperado

Após implementação completa:

```
✅ Cliente liga e solicita transferência
✅ Conferência criada automaticamente
✅ A-leg entra muted (ouve mas não fala)
✅ B-leg entra normal (pode falar e ouvir)
✅ OpenAI conecta ao B-leg via uuid_audio_stream
✅ OpenAI anuncia: "Você tem cliente na linha..."
✅ B-leg responde verbalmente
✅ OpenAI detecta aceitação/recusa via function call
✅ Se aceito: A-leg unmuted, conversa inicia
✅ Se recusado: B-leg kicked, ticket criado, A volta ao IVR
✅ Cleanup automático em todos os casos
```

---

## 📞 Suporte

Se encontrar problemas durante implementação:

1. **Verificar logs** em `/var/log/freeswitch/freeswitch.log`
2. **Testar FreeSWITCH CLI** com comandos manuais
3. **Validar OpenAI API** funcionando isoladamente
4. **Verificar mod_conference** está carregado no FreeSWITCH

---

**Versão**: 1.0  
**Data**: Janeiro 2026  
**Autor**: Claude (Anthropic)  
**Para**: Cursor AI (Implementation)

---

## 🚀 Comando para Cursor

Cole este documento no Cursor e diga:

```
Implemente a solução de transferência anunciada com mod_conference 
conforme especificado neste documento. Siga a ordem:

1. Criar transfer_manager_conference.py (PARTE 1)
2. Adaptar realtime_announcement.py (PARTE 2)  
3. Integrar em session.py (PARTE 3)

Use os códigos fornecidos como base e adapte conforme 
a estrutura existente do projeto.
```
