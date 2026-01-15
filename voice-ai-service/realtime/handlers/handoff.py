"""
Voice AI Handoff Handler

Gerencia transferência de chamadas para atendentes humanos e fallback para ticket.

Multi-tenant: domain_uuid obrigatório
Ref: openspec/changes/add-realtime-handoff-omni/design.md
"""

import os
import json
import time
import logging
import asyncio
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
import aiohttp

logger = logging.getLogger(__name__)

# Configurações via ambiente
OMNIPLAY_API_URL = os.getenv("OMNIPLAY_API_URL", "http://host.docker.internal:8080")
OMNIPLAY_API_TOKEN = os.getenv("OMNIPLAY_API_TOKEN", "")
HANDOFF_TIMEOUT_MS = int(os.getenv("HANDOFF_TIMEOUT_MS", "30000"))
HANDOFF_KEYWORDS = os.getenv("HANDOFF_KEYWORDS", "atendente,humano,pessoa,operador,falar com alguém").split(",")


@dataclass
class TranscriptEntry:
    """Uma entrada de transcrição."""
    role: str  # "user" ou "assistant"
    text: str
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "timestamp": int(self.timestamp * 1000)
        }


@dataclass
class HandoffConfig:
    """Configuração de handoff por tenant."""
    enabled: bool = True
    timeout_ms: int = 30000
    keywords: List[str] = field(default_factory=lambda: HANDOFF_KEYWORDS.copy())
    max_ai_turns: int = 20
    fallback_queue_id: Optional[int] = None
    secretary_uuid: Optional[str] = None


@dataclass
class HandoffResult:
    """Resultado do processo de handoff."""
    success: bool
    action: str  # "transferred", "ticket_created", "abandoned", "error"
    reason: str
    ticket_id: Optional[int] = None
    ticket_uuid: Optional[str] = None
    transferred_to: Optional[str] = None
    error: Optional[str] = None


class HandoffHandler:
    """
    Gerencia o processo de handoff de chamadas.
    
    Fluxo:
    1. Detecta trigger de handoff (keyword, intent, max_turns)
    2. Consulta atendentes online via API OmniPlay
    3. Se houver atendentes: solicita transfer ao FreeSWITCH
    4. Se não houver: cria ticket pending com transcrição e resumo
    """
    
    def __init__(
        self,
        domain_uuid: str,
        call_uuid: str,
        config: HandoffConfig,
        transcript: List[TranscriptEntry],
        on_transfer: Optional[Callable[[str], Any]] = None,
        on_message: Optional[Callable[[str], Any]] = None,
    ):
        self.domain_uuid = domain_uuid
        self.call_uuid = call_uuid
        self.config = config
        self.transcript = transcript
        self.on_transfer = on_transfer  # Callback para solicitar transfer ao FreeSWITCH
        self.on_message = on_message    # Callback para enviar mensagem ao caller
        
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._handoff_initiated = False
        self._turn_count = 0
    
    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Obtém sessão HTTP reutilizável."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {OMNIPLAY_API_TOKEN}",
                    "Content-Type": "application/json"
                },
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._http_session
    
    async def close(self):
        """Fecha recursos."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
    
    def increment_turn(self):
        """Incrementa contador de turns."""
        self._turn_count += 1
    
    def should_check_handoff(self) -> bool:
        """Verifica se deve checar handoff neste turn."""
        if not self.config.enabled or self._handoff_initiated:
            return False
        
        # Checar a cada 3 turns após o 5º
        if self._turn_count >= 5 and self._turn_count % 3 == 0:
            return True
        
        # Checar se atingiu max_turns
        if self._turn_count >= self.config.max_ai_turns:
            return True
        
        return False
    
    def detect_handoff_keyword(self, text: str) -> Optional[str]:
        """Detecta keyword de handoff no texto."""
        text_lower = text.lower()
        for keyword in self.config.keywords:
            if keyword.lower().strip() in text_lower:
                return keyword
        return None
    
    async def check_online_agents(self) -> Dict[str, Any]:
        """Consulta atendentes online via API OmniPlay."""
        try:
            session = await self._get_http_session()
            
            url = f"{OMNIPLAY_API_URL}/api/voice/agents/online"
            params = {}
            if self.config.fallback_queue_id:
                params["queue_id"] = self.config.fallback_queue_id
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(
                        "Agents online check",
                        extra={
                            "domain_uuid": self.domain_uuid,
                            "call_uuid": self.call_uuid,
                            "has_agents": data.get("has_online_agents"),
                            "count": data.get("agent_count", 0)
                        }
                    )
                    return data
                else:
                    logger.warning(
                        f"Failed to check online agents: {response.status}",
                        extra={"call_uuid": self.call_uuid}
                    )
                    return {"has_online_agents": False, "agents": [], "dial_string": None}
                    
        except Exception as e:
            logger.error(
                f"Error checking online agents: {e}",
                extra={"call_uuid": self.call_uuid}
            )
            return {"has_online_agents": False, "agents": [], "dial_string": None}
    
    async def create_fallback_ticket(
        self,
        caller_number: str,
        provider: str,
        language: str = "pt-BR",
        duration_seconds: int = 0,
        avg_latency_ms: Optional[float] = None,
        handoff_reason: str = "no_agents_available"
    ) -> HandoffResult:
        """Cria ticket pending via API OmniPlay."""
        try:
            session = await self._get_http_session()
            
            # Gerar resumo simples se não houver LLM
            summary = self._generate_simple_summary()
            
            payload = {
                "call_uuid": self.call_uuid,
                "caller_id": caller_number,
                "transcript": [t.to_dict() for t in self.transcript],
                "summary": summary,
                "provider": provider,
                "language": language,
                "duration_seconds": duration_seconds,
                "turns": self._turn_count,
                "avg_latency_ms": avg_latency_ms,
                "handoff_reason": handoff_reason,
                "queue_id": self.config.fallback_queue_id,
                "secretary_uuid": self.config.secretary_uuid
            }
            
            url = f"{OMNIPLAY_API_URL}/api/tickets/realtime-handoff"
            
            async with session.post(url, json=payload) as response:
                if response.status in (200, 201):
                    data = await response.json()
                    logger.info(
                        "Fallback ticket created",
                        extra={
                            "domain_uuid": self.domain_uuid,
                            "call_uuid": self.call_uuid,
                            "ticket_id": data.get("ticket_id"),
                            "ticket_uuid": data.get("ticket_uuid")
                        }
                    )
                    return HandoffResult(
                        success=True,
                        action="ticket_created",
                        reason=handoff_reason,
                        ticket_id=data.get("ticket_id"),
                        ticket_uuid=data.get("ticket_uuid")
                    )
                else:
                    error_text = await response.text()
                    logger.error(
                        f"Failed to create fallback ticket: {response.status} - {error_text}",
                        extra={"call_uuid": self.call_uuid}
                    )
                    return HandoffResult(
                        success=False,
                        action="error",
                        reason=handoff_reason,
                        error=f"API error: {response.status}"
                    )
                    
        except Exception as e:
            logger.error(
                f"Error creating fallback ticket: {e}",
                extra={"call_uuid": self.call_uuid}
            )
            return HandoffResult(
                success=False,
                action="error",
                reason=handoff_reason,
                error=str(e)
            )
    
    def _generate_simple_summary(self) -> str:
        """Gera resumo simples da conversa (sem LLM)."""
        if not self.transcript:
            return "Conversa via voz - ver transcrição completa"
        
        # Pegar últimas mensagens do usuário
        user_messages = [t.text for t in self.transcript if t.role == "user"]
        if not user_messages:
            return f"Conversa via voz ({self._turn_count} turnos) - ver transcrição completa"
        
        last_user_msg = user_messages[-1]
        truncated = last_user_msg[:150] + "..." if len(last_user_msg) > 150 else last_user_msg
        
        return f"Conversa via voz ({self._turn_count} turnos). Última mensagem: \"{truncated}\""
    
    async def initiate_handoff(
        self,
        reason: str,
        caller_number: str,
        provider: str,
        language: str = "pt-BR",
        duration_seconds: int = 0,
        avg_latency_ms: Optional[float] = None
    ) -> HandoffResult:
        """
        Inicia processo de handoff.
        
        1. Verifica atendentes online
        2. Se houver: solicita transfer
        3. Se não houver: cria ticket
        """
        if self._handoff_initiated:
            logger.warning("Handoff already initiated", extra={"call_uuid": self.call_uuid})
            return HandoffResult(
                success=False,
                action="error",
                reason=reason,
                error="Handoff already initiated"
            )
        
        self._handoff_initiated = True
        
        logger.info(
            "Initiating handoff",
            extra={
                "domain_uuid": self.domain_uuid,
                "call_uuid": self.call_uuid,
                "reason": reason,
                "turns": self._turn_count
            }
        )
        
        # 1. Verificar atendentes online
        agents_data = await self.check_online_agents()
        
        if agents_data.get("has_online_agents") and agents_data.get("dial_string"):
            # 2. Solicitar transfer
            dial_string = agents_data["dial_string"]
            
            if self.on_message:
                await self.on_message("Um momento, estou transferindo para um atendente...")
            
            if self.on_transfer:
                try:
                    await self.on_transfer(dial_string)
                    logger.info(
                        "Transfer initiated",
                        extra={
                            "call_uuid": self.call_uuid,
                            "dial_string": dial_string
                        }
                    )
                    return HandoffResult(
                        success=True,
                        action="transferred",
                        reason=reason,
                        transferred_to=dial_string
                    )
                except Exception as e:
                    logger.error(
                        f"Transfer failed: {e}",
                        extra={"call_uuid": self.call_uuid}
                    )
                    # Fallback para ticket se transfer falhar
                    return await self.create_fallback_ticket(
                        caller_number=caller_number,
                        provider=provider,
                        language=language,
                        duration_seconds=duration_seconds,
                        avg_latency_ms=avg_latency_ms,
                        handoff_reason=f"{reason}:transfer_failed"
                    )
        
        # 3. Sem atendentes - criar ticket
        if self.on_message:
            await self.on_message(
                "No momento não temos atendentes disponíveis. "
                "Vou registrar sua solicitação e entraremos em contato em breve."
            )
        
        return await self.create_fallback_ticket(
            caller_number=caller_number,
            provider=provider,
            language=language,
            duration_seconds=duration_seconds,
            avg_latency_ms=avg_latency_ms,
            handoff_reason=reason
        )
