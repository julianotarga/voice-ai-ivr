"""
Voice AI Handoff Handler

Gerencia transferência de chamadas para atendentes humanos e fallback para ticket.

Multi-tenant: domain_uuid obrigatório
Ref: openspec/changes/add-realtime-handoff-omni/design.md

Storage: Usa MinIO compartilhado para gravações
MinIO URL: https://minio.netplay.net.br/
Bucket: voice-recordings
"""

import os
import json
import time
import logging
import asyncio
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
import aiohttp

from ..utils.minio_uploader import get_minio_uploader, UploadResult

logger = logging.getLogger(__name__)

# Configurações via ambiente
OMNIPLAY_API_URL = os.getenv("OMNIPLAY_API_URL", "http://host.docker.internal:8080")
OMNIPLAY_SERVICE_TOKEN = os.getenv("VOICE_AI_SERVICE_TOKEN", "")  # Token para auth máquina-a-máquina
HANDOFF_TIMEOUT_MS = int(os.getenv("HANDOFF_TIMEOUT_MS", "30000"))
HANDOFF_KEYWORDS = os.getenv("HANDOFF_KEYWORDS", "atendente,humano,pessoa,operador,falar com alguém").split(",")

# Número de teste para usar quando caller_id for ramal interno (desenvolvimento)
# Se vazio, handoff é bloqueado para ramais
DEV_TEST_NUMBER = os.getenv("DEV_TEST_NUMBER", "5518999999999")


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
    omniplay_company_id: Optional[int] = None  # OmniPlay companyId para API


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
        """Obtém sessão HTTP reutilizável com headers de Service Auth."""
        if self._http_session is None or self._http_session.closed:
            headers = {
                "Content-Type": "application/json",
                "X-Service-Name": "voice-ai-realtime",
            }
            
            # Adicionar token de serviço se disponível
            if OMNIPLAY_SERVICE_TOKEN:
                headers["Authorization"] = f"Bearer {OMNIPLAY_SERVICE_TOKEN}"
            
            # Adicionar company_id se disponível
            if self.config.omniplay_company_id:
                headers["X-Company-Id"] = str(self.config.omniplay_company_id)
            
            # Timeout de 10s para primeira conexão (DNS + TLS handshake)
            # O handoff agora roda em background, não bloqueia mais o áudio
            self._http_session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10, connect=5)
            )
            logger.info(
                f"HTTP session created for OmniPlay API",
                extra={
                    "api_url": OMNIPLAY_API_URL,
                    "company_id": self.config.omniplay_company_id,
                    "has_token": bool(OMNIPLAY_SERVICE_TOKEN),
                }
            )
        return self._http_session
    
    async def close(self):
        """Fecha recursos."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
    
    def increment_turn(self):
        """Incrementa contador de turns."""
        self._turn_count += 1
    
    @staticmethod
    def normalize_brazilian_number(number: str) -> str:
        """
        Normaliza número brasileiro para formato E.164 (55 + DDD + número).
        
        Exemplos:
        - 18997751073 → 5518997751073 (celular, 11 dígitos)
        - 1836215152 → 551836215152 (fixo, 10 dígitos)
        - 5518997751073 → 5518997751073 (já normalizado)
        - 1000 → 1000 (ramal, não normaliza)
        """
        if not number:
            return number
        
        # Remover caracteres não numéricos
        clean = "".join(c for c in number if c.isdigit())
        
        # Se já começa com 55 e tem 12-13 dígitos, já está normalizado
        if clean.startswith("55") and len(clean) in (12, 13):
            return clean
        
        # Se tem 10-11 dígitos (DDD + número brasileiro), adicionar 55
        if len(clean) in (10, 11):
            return f"55{clean}"
        
        # Outros casos (ramal, número estrangeiro, etc.) - retornar como está
        return clean
    
    @staticmethod
    def is_internal_extension(number: str) -> bool:
        """
        Verifica se é ramal interno (não é número de telefone real).
        Ramais internos têm 2-4 dígitos (ex: 10, 100, 1000).
        Números brasileiros reais têm 10+ dígitos.
        """
        if not number:
            return True
        
        clean = "".join(c for c in number if c.isdigit())
        # Ramal: 2-4 dígitos
        return len(clean) <= 4
    
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
                f"Error checking online agents: {type(e).__name__}: {e}",
                extra={
                    "call_uuid": self.call_uuid,
                    "url": f"{OMNIPLAY_API_URL}/api/voice/agents/online",
                    "company_id": self.config.omniplay_company_id,
                },
                exc_info=True  # Mostrar traceback completo
            )
            return {"has_online_agents": False, "agents": [], "dial_string": None}
    
    async def upload_recording(
        self,
        audio_data: bytes,
        content_type: str = "audio/mpeg",
        metadata: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """
        Faz upload da gravação para MinIO compartilhado.
        
        Args:
            audio_data: Bytes do áudio
            content_type: MIME type (default: audio/mpeg)
            metadata: Metadados adicionais
            
        Returns:
            URL pública da gravação ou None se falhar
        """
        if not audio_data:
            logger.debug("No audio data to upload", extra={"call_uuid": self.call_uuid})
            return None
        
        if not self.config.omniplay_company_id:
            logger.warning(
                "Cannot upload recording: omniplay_company_id not configured",
                extra={"call_uuid": self.call_uuid}
            )
            return None
        
        try:
            uploader = get_minio_uploader()
            
            if not uploader.is_available:
                logger.warning(
                    "MinIO uploader not available",
                    extra={"call_uuid": self.call_uuid}
                )
                return None
            
            # Adicionar metadados do domínio
            upload_metadata = {
                "domain-uuid": self.domain_uuid,
                "secretary-uuid": self.config.secretary_uuid or "",
            }
            if metadata:
                upload_metadata.update(metadata)
            
            result = uploader.upload_audio(
                audio_data=audio_data,
                call_uuid=self.call_uuid,
                company_id=self.config.omniplay_company_id,
                content_type=content_type,
                metadata=upload_metadata
            )
            
            if result.success and result.url:
                logger.info(
                    "Recording uploaded to MinIO",
                    extra={
                        "call_uuid": self.call_uuid,
                        "url": result.url,
                        "size": result.size
                    }
                )
                return result.url
            else:
                logger.warning(
                    f"Recording upload failed: {result.error}",
                    extra={"call_uuid": self.call_uuid}
                )
                return None
                
        except Exception as e:
            logger.error(
                f"Error uploading recording: {e}",
                extra={"call_uuid": self.call_uuid}
            )
            return None
    
    async def create_fallback_ticket(
        self,
        caller_number: str,
        provider: str,
        language: str = "pt-BR",
        duration_seconds: int = 0,
        avg_latency_ms: Optional[float] = None,
        handoff_reason: str = "no_agents_available",
        recording_url: Optional[str] = None,
        audio_data: Optional[bytes] = None
    ) -> HandoffResult:
        """
        Cria ticket pending via API OmniPlay.
        
        Args:
            caller_number: Número do chamador
            provider: Nome do provider de IA usado
            language: Idioma da conversa
            duration_seconds: Duração da chamada
            avg_latency_ms: Latência média
            handoff_reason: Motivo do handoff
            recording_url: URL da gravação (se já foi feito upload)
            audio_data: Bytes do áudio (faz upload se recording_url não fornecido)
        """
        try:
            session = await self._get_http_session()
            
            # Fazer upload da gravação se fornecida e não tiver URL
            final_recording_url = recording_url
            if not final_recording_url and audio_data:
                final_recording_url = await self.upload_recording(audio_data)
            
            # Gerar resumo simples se não houver LLM
            summary = self._generate_simple_summary()
            
            transcript_dicts = [t.to_dict() for t in self.transcript]
            payload = {
                "call_uuid": self.call_uuid,
                "caller_id": caller_number,
                "transcript": transcript_dicts,
                "summary": summary,
                "provider": provider,
                "language": language,
                "duration_seconds": duration_seconds,
                "turns": self._turn_count,
                "avg_latency_ms": avg_latency_ms,
                "handoff_reason": handoff_reason,
                "queue_id": self.config.fallback_queue_id,
                "secretary_uuid": self.config.secretary_uuid,
                "recording_url": final_recording_url  # ← URL da gravação no MinIO
            }
            
            logger.info(
                "Creating fallback ticket",
                extra={
                    "call_uuid": self.call_uuid,
                    "transcript_count": len(transcript_dicts),
                    "summary_length": len(summary) if summary else 0,
                    "has_recording": bool(final_recording_url),
                }
            )
            
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
                            "ticket_uuid": data.get("ticket_uuid"),
                            "has_recording": bool(final_recording_url)
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
                f"Error creating fallback ticket: {type(e).__name__}: {e}",
                extra={
                    "call_uuid": self.call_uuid,
                    "url": f"{OMNIPLAY_API_URL}/api/tickets/realtime-handoff",
                    "company_id": self.config.omniplay_company_id,
                },
                exc_info=True  # Mostrar traceback completo
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
        avg_latency_ms: Optional[float] = None,
        audio_data: Optional[bytes] = None,
        recording_url: Optional[str] = None
    ) -> HandoffResult:
        """
        Inicia processo de handoff.
        
        1. Verifica atendentes online
        2. Se houver: solicita transfer
        3. Se não houver: faz upload da gravação e cria ticket
        
        Args:
            reason: Motivo do handoff (keyword, max_turns, etc.)
            caller_number: Número do chamador
            provider: Nome do provider de IA
            language: Idioma da conversa
            duration_seconds: Duração da chamada
            avg_latency_ms: Latência média
            audio_data: Bytes da gravação (será feito upload para MinIO)
            recording_url: URL da gravação (se já foi feito upload)
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
        
        # Verificar se é ramal interno (2-4 dígitos)
        if self.is_internal_extension(caller_number):
            if DEV_TEST_NUMBER:
                # Modo desenvolvimento: usar número de teste
                logger.info(
                    "Internal extension detected - using DEV_TEST_NUMBER for testing",
                    extra={
                        "call_uuid": self.call_uuid,
                        "original_number": caller_number,
                        "test_number": DEV_TEST_NUMBER,
                        "reason": reason,
                    }
                )
                normalized_number = DEV_TEST_NUMBER
            else:
                # Produção: bloquear handoff para ramais
                logger.info(
                    "Handoff skipped: internal extension (no real phone number)",
                    extra={
                        "call_uuid": self.call_uuid,
                        "caller_number": caller_number,
                        "reason": reason,
                    }
                )
                return HandoffResult(
                    success=False,
                    action="abandoned",
                    reason=reason,
                    error="Internal extension - handoff not available for internal calls"
                )
        else:
            # Normalizar número brasileiro (adicionar 55 se necessário)
            normalized_number = self.normalize_brazilian_number(caller_number)
        
        logger.info(
            "Initiating handoff",
            extra={
                "domain_uuid": self.domain_uuid,
                "call_uuid": self.call_uuid,
                "reason": reason,
                "turns": self._turn_count,
                "caller_number_original": caller_number,
                "caller_number_normalized": normalized_number,
                "has_audio": bool(audio_data),
                "has_recording_url": bool(recording_url),
                "omniplay_company_id": self.config.omniplay_company_id,
                "omniplay_api_url": OMNIPLAY_API_URL,
            }
        )
        
        # Usar número normalizado daqui em diante
        caller_number = normalized_number
        
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
                        handoff_reason=f"{reason}:transfer_failed",
                        audio_data=audio_data,
                        recording_url=recording_url
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
            handoff_reason=reason,
            audio_data=audio_data,
            recording_url=recording_url
        )
