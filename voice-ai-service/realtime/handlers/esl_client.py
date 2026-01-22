"""
ESLClient - Cliente assíncrono para FreeSWITCH Event Socket Library.

Referências:
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/proposal.md
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md (1.2)

Funcionalidades:
- Conexão assíncrona com reconexão automática
- Subscrição de eventos com filtros
- Métodos de alto nível para controle de chamadas
- Wait for event com timeout

IMPORTANTE: Este cliente usa asyncio, não gevent.
Para uso com greenswitch, execute em thread separada com event loop asyncio.
"""

import os
import logging
import asyncio
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, TYPE_CHECKING
import re

logger = logging.getLogger(__name__)

def _is_docker_runtime() -> bool:
    return os.path.exists("/.dockerenv") or os.getenv("DOCKER_CONTAINER") == "true"

def _resolve_esl_host(raw_host: Optional[str]) -> str:
    host = (raw_host or "127.0.0.1").strip()
    allow_loopback = os.getenv("ESL_HOST_ALLOW_LOOPBACK", "false").lower() == "true"
    if host in ("127.0.0.1", "localhost") and _is_docker_runtime() and not allow_loopback:
        logger.warning("ESL_HOST aponta para loopback em container; usando host.docker.internal")
        return "host.docker.internal"
    return host

# Configurações ESL
ESL_HOST = _resolve_esl_host(os.getenv("ESL_HOST"))
ESL_PORT = int(os.getenv("ESL_PORT", "8021"))
ESL_PASSWORD = os.getenv("ESL_PASSWORD", "ClueCon")

# Timeouts
ESL_CONNECT_TIMEOUT = float(os.getenv("ESL_CONNECT_TIMEOUT", "5.0"))
ESL_READ_TIMEOUT = float(os.getenv("ESL_READ_TIMEOUT", "30.0"))
ESL_RECONNECT_DELAY = float(os.getenv("ESL_RECONNECT_DELAY", "2.0"))
ESL_MAX_RECONNECT_ATTEMPTS = int(os.getenv("ESL_MAX_RECONNECT_ATTEMPTS", "3"))
ESL_REGISTRATION_TIMEOUT = float(os.getenv("ESL_REGISTRATION_TIMEOUT", "3.0"))
ESL_API_TIMEOUT = float(os.getenv("ESL_API_TIMEOUT", "5.0"))


class ESLError(Exception):
    """Erro genérico do ESL."""
    pass


class ESLConnectionError(ESLError):
    """Erro de conexão ESL."""
    pass


class ESLCommandError(ESLError):
    """Erro ao executar comando ESL."""
    pass


@dataclass
class ESLEvent:
    """Evento ESL parseado."""
    name: str
    uuid: Optional[str]
    headers: Dict[str, str]
    body: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def hangup_cause(self) -> Optional[str]:
        """Retorna hangup cause se for evento de hangup."""
        return self.headers.get("Hangup-Cause")
    
    @property
    def channel_state(self) -> Optional[str]:
        """Retorna estado do canal."""
        return self.headers.get("Channel-State")
    
    @property
    def caller_id_number(self) -> Optional[str]:
        """Retorna número do chamador."""
        return self.headers.get("Caller-Caller-ID-Number")
    
    @property
    def callee_id_number(self) -> Optional[str]:
        """Retorna número do destino."""
        return self.headers.get("Caller-Callee-ID-Number")


@dataclass
class EventHandler:
    """Handler registrado para eventos."""
    handler_id: str
    event_name: str
    uuid: Optional[str]
    callback: Callable[[ESLEvent], Any]
    once: bool = False


@dataclass
class OriginateResult:
    """
    Resultado de uma operação de originate.
    
    Permite diferenciar entre sucesso, falha e os diferentes motivos de falha.
    """
    success: bool
    uuid: Optional[str] = None
    hangup_cause: Optional[str] = None
    error_message: Optional[str] = None
    
    @property
    def is_offline(self) -> bool:
        """Retorna True se o destino não está registrado/online."""
        return self.hangup_cause in (
            "USER_NOT_REGISTERED",
            "SUBSCRIBER_ABSENT",
            "UNALLOCATED_NUMBER",
            "NO_ROUTE_DESTINATION",
        )
    
    @property
    def is_busy(self) -> bool:
        """Retorna True se o destino está ocupado."""
        return self.hangup_cause in (
            "USER_BUSY",
            "NORMAL_CIRCUIT_CONGESTION",
        )
    
    @property
    def is_no_answer(self) -> bool:
        """Retorna True se tocou mas ninguém atendeu."""
        return self.hangup_cause in (
            "NO_ANSWER",
            "NO_USER_RESPONSE",
            "ORIGINATOR_CANCEL",
            "ALLOTTED_TIMEOUT",
        )
    
    @property
    def is_rejected(self) -> bool:
        """Retorna True se a chamada foi rejeitada."""
        return self.hangup_cause in (
            "CALL_REJECTED",
            "USER_CHALLENGE",
        )
    
    @property
    def is_dnd(self) -> bool:
        """Retorna True se o destino está em Do Not Disturb."""
        return self.hangup_cause == "DO_NOT_DISTURB"


class AsyncESLClient:
    """
    Cliente ESL assíncrono com suporte a eventos.
    
    Uso:
        client = AsyncESLClient()
        await client.connect()
        
        # Executar comando
        result = await client.execute_api("show calls")
        
        # Subscrever eventos
        await client.subscribe_events(["CHANNEL_ANSWER", "CHANNEL_HANGUP"], uuid)
        
        # Aguardar evento específico
        event = await client.wait_for_event(["CHANNEL_ANSWER"], uuid, timeout=30)
        
        await client.disconnect()
    """
    
    def __init__(
        self,
        host: str = ESL_HOST,
        port: int = ESL_PORT,
        password: str = ESL_PASSWORD
    ):
        self.host = host
        self.port = port
        self.password = password
        
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._reconnecting = False
        
        # Event handling
        self._event_handlers: Dict[str, EventHandler] = {}
        self._event_queue: asyncio.Queue[ESLEvent] = asyncio.Queue(maxsize=1000)
        self._event_task: Optional[asyncio.Task] = None
        self._subscribed_events: Set[str] = set()
        
        # Lock para operações thread-safe
        self._command_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        
        # Lock para leitura do socket - resolve "readuntil() called while another coroutine is already waiting"
        # Este lock garante que apenas uma coroutine pode ler do socket por vez
        self._read_lock = asyncio.Lock()
        
        # Event para pausar event reader durante comandos
        # Evita "readuntil() called while another coroutine is already waiting"
        self._reader_paused = asyncio.Event()
        self._reader_paused.set()  # Inicialmente não pausado (set = pode rodar)
        
        # Flag para pausar event reader durante comandos (backup/legado)
        self._command_in_progress = False
    
    async def connect(self) -> bool:
        """
        Conecta ao FreeSWITCH ESL.
        
        Returns:
            True se conectou com sucesso
        """
        async with self._connect_lock:
            if self._connected:
                return True
            
            try:
                # Conectar
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=ESL_CONNECT_TIMEOUT
                )
                
                # Ler banner
                await self._read_response(discard_events=True)
                
                # Autenticar
                await self._send(f"auth {self.password}\n\n")
                response = await self._read_response(discard_events=True)
                
                if "Reply-Text: +OK" in response:
                    self._connected = True
                    logger.info(
                        f"Connected to FreeSWITCH ESL at {self.host}:{self.port}"
                    )
                    
                    # Iniciar task de leitura de eventos
                    self._event_task = asyncio.create_task(self._event_reader_loop())
                    
                    return True
                else:
                    logger.error(f"ESL authentication failed: {response}")
                    await self._close_connection()
                    return False
                    
            except asyncio.TimeoutError:
                logger.error(
                    f"ESL connection timeout ({ESL_CONNECT_TIMEOUT}s) to {self.host}:{self.port}"
                )
                return False
            except Exception as e:
                logger.error(f"ESL connection error: {e}")
                return False
    
    async def disconnect(self) -> None:
        """Desconecta do ESL."""
        self._connected = False
        
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None
        
        await self._close_connection()
        logger.info("Disconnected from ESL")
    
    async def _close_connection(self) -> None:
        """Fecha conexão."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
    
    async def reconnect(self) -> bool:
        """
        Tenta reconectar ao ESL.
        
        Returns:
            True se reconectou com sucesso
        """
        if self._reconnecting:
            return False
        
        self._reconnecting = True
        
        try:
            for attempt in range(ESL_MAX_RECONNECT_ATTEMPTS):
                logger.info(
                    f"ESL reconnect attempt {attempt + 1}/{ESL_MAX_RECONNECT_ATTEMPTS}"
                )
                
                await self._close_connection()
                self._connected = False
                
                await asyncio.sleep(ESL_RECONNECT_DELAY)
                
                if await self.connect():
                    # Re-subscrever eventos
                    if self._subscribed_events:
                        await self._resubscribe_events()
                    return True
            
            logger.error("ESL reconnect failed after max attempts")
            return False
            
        finally:
            self._reconnecting = False
    
    async def _resubscribe_events(self) -> None:
        """
        Re-subscreve eventos após reconexão.
        
        THREAD SAFETY:
        - Usa _reader_paused Event para pausar event reader
        """
        events = list(self._subscribed_events)
        self._subscribed_events.clear()
        
        # Pausar event reader durante resubscribe
        self._reader_paused.clear()
        self._command_in_progress = True
        try:
            await asyncio.sleep(0.05)
            
            for event in events:
                try:
                    await self._send(f"event plain {event}\n\n")
                    await self._read_response(discard_events=True)
                    self._subscribed_events.add(event)
                except Exception as e:
                    logger.warning(f"Failed to resubscribe to {event}: {e}")
        finally:
            self._command_in_progress = False
            self._reader_paused.set()
    
    @property
    def is_connected(self) -> bool:
        """Retorna True se conectado."""
        return self._connected
    
    async def _send(self, data: str) -> None:
        """Envia dados ao ESL."""
        if not self._writer:
            raise ESLConnectionError("Not connected")
        
        self._writer.write(data.encode())
        await self._writer.drain()
    
    async def _read_response(self, timeout: float = ESL_READ_TIMEOUT, discard_events: bool = False, quiet_timeout: bool = False) -> str:
        """
        Lê resposta do ESL (headers + body se houver).
        
        Formato ESL:
            Header: Value\n
            Header2: Value2\n
            \n
            [Body se Content-Length presente]
        
        IMPORTANTE:
        - Se discard_events=True: ignora eventos (Content-Type: text/event-plain) que podem
          ter ficado no buffer e continua lendo até receber uma resposta de comando
          (Content-Type: api/response ou command/reply).
        - Se discard_events=False: retorna o payload mesmo se for evento (usado pelo event loop).
        
        THREAD SAFETY:
        - Usa _read_lock para garantir que apenas uma coroutine lê do socket por vez
        - Resolve erro "readuntil() called while another coroutine is already waiting"
        """
        if not self._reader:
            raise ESLConnectionError("Not connected")
        
        max_retries = 10  # Evitar loop infinito (quando discard_events=True)
        
        # Usar lock para serializar leituras do socket
        # Isso evita "readuntil() called while another coroutine is already waiting"
        async with self._read_lock:
            for attempt in range(max_retries if discard_events else 1):
                try:
                    lines = []
                    content_length = 0
                    content_type = ""
                    
                    # Ler headers
                    while True:
                        line = await asyncio.wait_for(
                            self._reader.readline(),
                            timeout=timeout
                        )
                        line_str = line.decode().rstrip("\r\n")
                        
                        if not line_str:
                            # Linha vazia = fim dos headers
                            break
                        
                        lines.append(line_str)
                        
                        # Verificar Content-Length
                        if line_str.startswith("Content-Length:"):
                            content_length = int(line_str.split(":")[1].strip())
                        
                        # Verificar Content-Type
                        if line_str.startswith("Content-Type:"):
                            content_type = line_str.split(":", 1)[1].strip()
                    
                    # Ler body se houver
                    body = ""
                    if content_length > 0:
                        body_bytes = await asyncio.wait_for(
                            self._reader.read(content_length),
                            timeout=timeout
                        )
                        body = body_bytes.decode()
                    
                    # Verificar se é evento (text/event-plain)
                    # No modo de leitura de comando, descartar e continuar.
                    if discard_events and content_type.startswith("text/event-plain"):
                        logger.debug(f"Discarding buffered event during command read: {body[:100]}...")
                        continue  # Ler próximo pacote
                    
                    # É uma resposta de comando
                    return "\n".join(lines) + ("\n\n" + body if body else "")
                    
                except asyncio.TimeoutError:
                    if not quiet_timeout:
                        logger.debug(f"Read timeout ({timeout}s)")
                    raise ESLError(f"Read timeout ({timeout}s)")
                    
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
                    # Conexão TCP fechada pelo FreeSWITCH
                    logger.warning(f"ESL connection closed: {e}")
                    self._connected = False
                    raise ESLError(f"Connection closed: {e}")
                    
                except OSError as e:
                    # Outros erros de socket (ex: "Bad file descriptor")
                    if "Bad file descriptor" in str(e) or e.errno in (9, 32, 104):
                        logger.warning(f"ESL socket error: {e}")
                        self._connected = False
                        raise ESLError(f"Socket error: {e}")
                    raise
            
            raise ESLError("Max retries reached reading command response")
    
    async def _read_response_unlocked(self, timeout: float = 10.0) -> str:
        """
        Lê resposta do ESL SEM usar lock (chamador já deve ter o lock).
        
        Descarta eventos automaticamente até receber resposta de comando.
        
        Args:
            timeout: Timeout em segundos
            
        Returns:
            Resposta do comando
        """
        if not self._reader:
            raise ESLConnectionError("Not connected")
        
        max_retries = 10
        
        for attempt in range(max_retries):
            try:
                lines = []
                content_length = 0
                content_type = ""
                
                # Ler headers
                while True:
                    line = await asyncio.wait_for(
                        self._reader.readline(),
                        timeout=timeout
                    )
                    line_str = line.decode().rstrip("\r\n")
                    
                    if not line_str:
                        # Linha vazia = fim dos headers
                        break
                    
                    lines.append(line_str)
                    
                    if line_str.startswith("Content-Length:"):
                        content_length = int(line_str.split(":")[1].strip())
                    
                    if line_str.startswith("Content-Type:"):
                        content_type = line_str.split(":", 1)[1].strip()
                
                # Ler body se houver
                body = ""
                if content_length > 0:
                    body_bytes = await asyncio.wait_for(
                        self._reader.read(content_length),
                        timeout=timeout
                    )
                    body = body_bytes.decode()
                
                # Descartar eventos
                if content_type.startswith("text/event-plain"):
                    logger.debug(f"Discarding event during command: {body[:50]}...")
                    continue
                
                # É uma resposta de comando
                return "\n".join(lines) + ("\n\n" + body if body else "")
                
            except asyncio.TimeoutError:
                raise
        
        raise ESLError("Max retries reached reading command response")
    
    async def _event_reader_loop(self) -> None:
        """
        Loop de leitura de eventos em background.
        
        THREAD SAFETY:
        - Aguarda _reader_paused Event antes de cada leitura
        - Usa _read_lock implicitamente via _read_event() -> _read_response()
        - Resolve erro "readuntil() called while another coroutine is already waiting"
        """
        while self._connected:
            try:
                # Aguardar permissão para ler (pausa durante comandos)
                # wait() retorna imediatamente se o Event estiver set
                try:
                    await asyncio.wait_for(self._reader_paused.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    # Timeout esperando permissão - comando em progresso, continuar loop
                    continue
                
                # Flag legado de pausa (backup)
                if self._command_in_progress:
                    await asyncio.sleep(0.05)  # 50ms
                    continue
                
                event = await self._read_event()
                if event:
                    # Processar handlers registrados
                    await self._dispatch_event(event)
                    
                    # Adicionar à fila para wait_for_event
                    try:
                        self._event_queue.put_nowait(event)
                    except asyncio.QueueFull:
                        # Descartar evento antigo
                        try:
                            self._event_queue.get_nowait()
                            self._event_queue.put_nowait(event)
                        except Exception:
                            pass
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._connected and not self._command_in_progress:
                    logger.error(f"Event reader error: {e}")
                    # Tentar reconectar
                    await self.reconnect()
                break
    
    async def _read_event(self) -> Optional[ESLEvent]:
        """Lê e parseia um evento do ESL."""
        try:
            # NOTA: Timeout curto (0.5s) para liberar lock frequentemente
            # Isso permite que execute_api() adquira o lock rapidamente
            # Timeout é esperado e normal quando não há eventos - não logar
            response = await self._read_response(timeout=0.5, discard_events=False, quiet_timeout=True)
            
            # Verificar se é evento
            if "Event-Name:" not in response:
                return None
            
            # Parsear headers
            headers: Dict[str, str] = {}
            body = None
            
            parts = response.split("\n\n", 1)
            header_lines = parts[0].split("\n")
            
            if len(parts) > 1:
                body = parts[1]
            
            for line in header_lines:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip()] = value.strip()
            
            event_name = headers.get("Event-Name", "UNKNOWN")
            event_uuid = headers.get("Unique-ID")
            
            return ESLEvent(
                name=event_name,
                uuid=event_uuid,
                headers=headers,
                body=body
            )
            
        except ESLError as e:
            # Timeout é esperado e normal quando não há eventos - não logar
            if "Read timeout" not in str(e):
                logger.debug(f"Failed to read event: {e}")
            return None
        except Exception as e:
            logger.debug(f"Failed to read event: {e}")
            return None
    
    async def _dispatch_event(self, event: ESLEvent) -> None:
        """Despacha evento para handlers registrados."""
        handlers_to_remove = []
        
        for handler_id, handler in self._event_handlers.items():
            # Verificar match
            if handler.event_name != event.name:
                continue
            
            if handler.uuid and handler.uuid != event.uuid:
                continue
            
            # Chamar callback
            try:
                result = handler.callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Event handler error: {e}")
            
            # Marcar para remoção se once=True
            if handler.once:
                handlers_to_remove.append(handler_id)
        
        # Remover handlers once
        for handler_id in handlers_to_remove:
            del self._event_handlers[handler_id]
    
    # =========================================================================
    # API de comandos
    # =========================================================================
    
    async def execute_api(self, command: str, timeout: float = 10.0) -> str:
        """
        Executa comando API síncrono e retorna resposta.
        
        IMPORTANTE: Pausa o event reader loop para evitar conflito de leitura.
        
        Args:
            command: Comando a executar (ex: "show calls", "uuid_getvar uuid var")
            timeout: Timeout em segundos (padrão: 10s)
        
        Returns:
            Resposta do comando
        
        Raises:
            ESLError: Se timeout ou falha
        """
        async with self._command_lock:
            if not self._connected:
                if not await self.connect():
                    raise ESLConnectionError("Failed to connect to ESL")
            
            # Sinalizar que comando está em progresso
            self._command_in_progress = True
            self._reader_paused.clear()
            
            try:
                # Aguardar event reader liberar o _read_lock
                # Tentar adquirir com timeout curto para evitar deadlock
                lock_acquired = False
                for _ in range(10):  # 10 tentativas de 100ms = 1s máximo
                    try:
                        await asyncio.wait_for(
                            self._read_lock.acquire(),
                            timeout=0.1
                        )
                        lock_acquired = True
                        break
                    except asyncio.TimeoutError:
                        # Event reader ainda está lendo, esperar
                        await asyncio.sleep(0.05)
                
                if not lock_acquired:
                    logger.warning("Could not acquire read lock, forcing read anyway")
                    # Forçar aquisição - pode causar problemas mas evita deadlock total
                    await self._read_lock.acquire()
                
                try:
                    # Enviar comando
                    await self._send(f"api {command}\n\n")
                    
                    # Ler resposta diretamente (sem usar _read_response para evitar lock duplo)
                    response = await self._read_response_unlocked(timeout=timeout)
                    
                    # Extrair body da resposta
                    if "Content-Length:" in response:
                        parts = response.split("\n\n", 1)
                        if len(parts) > 1:
                            return parts[1]
                    
                    return response
                    
                finally:
                    self._read_lock.release()
                
            except asyncio.TimeoutError:
                logger.error(f"execute_api timeout ({timeout}s) for command: {command[:50]}...")
                raise ESLError(f"API command timeout: {command[:50]}")
            
            finally:
                # Restaurar flags
                self._command_in_progress = False
                self._reader_paused.set()
    
    async def execute_bgapi(self, command: str) -> str:
        """
        Executa comando em background (assíncrono).
        
        Args:
            command: Comando a executar
        
        Returns:
            Job-UUID do comando
        
        THREAD SAFETY:
        - Usa _reader_paused Event para pausar event reader
        """
        async with self._command_lock:
            if not self._connected:
                if not await self.connect():
                    raise ESLConnectionError("Failed to connect to ESL")
            
            # Pausar event reader loop durante o comando
            self._reader_paused.clear()
            self._command_in_progress = True
            try:
                await asyncio.sleep(0.05)
                
                await self._send(f"bgapi {command}\n\n")
                response = await self._read_response(discard_events=True)
                
                # Extrair Job-UUID
                match = re.search(r"Job-UUID:\s*([a-f0-9-]+)", response)
                if match:
                    return match.group(1)
                
                return response
            finally:
                self._command_in_progress = False
                self._reader_paused.set()
    
    # =========================================================================
    # API de eventos
    # =========================================================================
    
    async def subscribe_events(
        self,
        events: List[str],
        uuid: Optional[str] = None
    ) -> None:
        """
        Subscreve para eventos.
        
        Args:
            events: Lista de eventos (ex: ["CHANNEL_ANSWER", "CHANNEL_HANGUP"])
            uuid: UUID específico para filtrar (opcional)
        
        THREAD SAFETY:
        - Usa _reader_paused Event para pausar event reader
        """
        async with self._command_lock:
            self._reader_paused.clear()
            self._command_in_progress = True
            try:
                await asyncio.sleep(0.05)
                
                for event in events:
                    if event not in self._subscribed_events:
                        cmd = f"event plain {event}"
                        await self._send(f"{cmd}\n\n")
                        await self._read_response(discard_events=True)
                        self._subscribed_events.add(event)
                
                # Se uuid específico, filtrar eventos
                if uuid:
                    await self._send(f"filter Unique-ID {uuid}\n\n")
                    await self._read_response(discard_events=True)
            finally:
                self._command_in_progress = False
                self._reader_paused.set()
    
    async def unsubscribe_events(self, uuid: Optional[str] = None) -> None:
        """
        Remove filtros de eventos.
        
        THREAD SAFETY:
        - Usa _reader_paused Event para pausar event reader
        """
        if uuid:
            async with self._command_lock:
                self._reader_paused.clear()
                self._command_in_progress = True
                try:
                    await asyncio.sleep(0.05)
                    await self._send(f"filter delete Unique-ID {uuid}\n\n")
                    await self._read_response(discard_events=True)
                finally:
                    self._command_in_progress = False
                    self._reader_paused.set()
    
    def on_event(
        self,
        event_name: str,
        uuid: Optional[str],
        callback: Callable[[ESLEvent], Any],
        once: bool = False
    ) -> str:
        """
        Registra handler para evento.
        
        Args:
            event_name: Nome do evento
            uuid: UUID para filtrar (opcional)
            callback: Função a chamar quando evento ocorrer
            once: Se True, remove handler após primeira execução
        
        Returns:
            handler_id para usar em off_event
        """
        handler_id = str(uuid_module.uuid4())
        
        self._event_handlers[handler_id] = EventHandler(
            handler_id=handler_id,
            event_name=event_name,
            uuid=uuid,
            callback=callback,
            once=once
        )
        
        return handler_id
    
    def off_event(self, handler_id: str) -> None:
        """Remove handler de evento."""
        if handler_id in self._event_handlers:
            del self._event_handlers[handler_id]
    
    async def register_event_handler(
        self,
        event_name: str,
        callback: Callable[[Any], Awaitable[None]],
        uuid_filter: Optional[str] = None
    ) -> str:
        """
        Registra handler assíncrono para evento ESL.
        
        Wrapper assíncrono para on_event() com suporte a callback async.
        
        Args:
            event_name: Nome do evento (ex: "CHANNEL_HANGUP")
            callback: Função async a chamar quando evento ocorrer
            uuid_filter: UUID para filtrar (opcional)
            
        Returns:
            handler_id para usar em unregister_event_handler
        """
        return self.on_event(
            event_name=event_name,
            uuid=uuid_filter,
            callback=callback,
            once=False
        )
    
    async def unregister_event_handler(self, handler_id: str) -> None:
        """
        Remove handler de evento.
        
        Wrapper assíncrono para off_event().
        
        Args:
            handler_id: ID retornado por register_event_handler
        """
        self.off_event(handler_id)
    
    async def wait_for_event(
        self,
        event_names: List[str],
        uuid: str,
        timeout: float = 30.0
    ) -> Optional[ESLEvent]:
        """
        Aguarda evento específico.
        
        Args:
            event_names: Lista de eventos a aguardar
            uuid: UUID da chamada
            timeout: Timeout em segundos
        
        Returns:
            Evento recebido ou None se timeout
        """
        end_time = asyncio.get_event_loop().time() + timeout
        
        while asyncio.get_event_loop().time() < end_time:
            try:
                remaining = end_time - asyncio.get_event_loop().time()
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=min(remaining, 1.0)
                )
                
                if event.name in event_names and event.uuid == uuid:
                    return event
                    
            except asyncio.TimeoutError:
                continue
        
        return None
    
    # =========================================================================
    # Métodos de alto nível
    # =========================================================================
    
    async def uuid_broadcast(
        self,
        uuid: str,
        audio: str,
        leg: str = "aleg"
    ) -> bool:
        """
        Reproduz áudio em uma chamada.
        
        Args:
            uuid: UUID da chamada
            audio: Caminho do áudio ou comando de playback
                   Ex: "local_stream://moh", "/path/to/file.wav"
            leg: aleg, bleg ou both
        
        Returns:
            True se sucesso
        """
        try:
            result = await asyncio.wait_for(
                self.execute_api(f"uuid_broadcast {uuid} {audio} {leg}"),
                timeout=ESL_API_TIMEOUT
            )
            success = "+OK" in result or "Success" in result
            
            if success:
                logger.debug(f"uuid_broadcast success: {uuid} {audio}")
            else:
                logger.warning(f"uuid_broadcast failed: {result}")
            
            return success
        except asyncio.TimeoutError:
            logger.error(f"uuid_broadcast timeout after {ESL_API_TIMEOUT}s: {uuid} {audio}")
            return False
        except Exception as e:
            logger.error(f"uuid_broadcast error: {e}")
            return False
    
    async def uuid_break(self, uuid: str, all_: bool = False) -> bool:
        """
        Interrompe playback em uma chamada.
        
        Args:
            uuid: UUID da chamada
            all_: Se True, interrompe todos os playbacks
        
        Returns:
            True se sucesso
        """
        try:
            cmd = f"uuid_break {uuid}"
            if all_:
                cmd += " all"
            
            result = await self.execute_api(cmd)
            return "+OK" in result
        except Exception as e:
            logger.error(f"uuid_break error: {e}")
            return False
    
    async def uuid_audio_stream(
        self,
        uuid: str,
        action: str,
        url: Optional[str] = None,
        format: str = "mono",
        rate: str = "16k"
    ) -> bool:
        """
        Controla streaming de áudio via mod_audio_stream.
        
        Ref: https://github.com/amigniter/mod_audio_stream
        REQUER: mod_audio_stream v1.0.3+ para suporte a pause/resume
        
        Args:
            uuid: UUID da chamada
            action: start, stop, pause, resume, send_text
            url: URL do WebSocket (obrigatório para start)
            format: mono, mixed, stereo (default: mono)
                - mono: apenas áudio do chamador (caller) - RECOMENDADO
                - mixed: caller + callee em um canal
                - stereo: caller no canal esquerdo, callee no direito
            rate: 8k ou 16k (default: 16k)
        
        Returns:
            True se sucesso
        
        IMPORTANTE - Uso durante transferências:
        1. PAUSE antes de colocar em espera - evita captura de ruídos
        2. RESUME após transferência falhar - retoma captura do caller
        3. STOP após bridge sucesso - cliente agora fala com humano
        
        Isso evita loops de feedback onde o bot "ouve" a si mesmo.
        """
        try:
            if action == "start":
                if not url:
                    logger.error("uuid_audio_stream start requires URL")
                    return False
                cmd = f"uuid_audio_stream {uuid} start {url} {format} {rate}"
            elif action in ("stop", "pause", "resume"):
                cmd = f"uuid_audio_stream {uuid} {action}"
            else:
                logger.error(f"uuid_audio_stream invalid action: {action}")
                return False
            
            result = await self.execute_api(cmd)
            success = "+OK" in result
            
            if success:
                logger.debug(f"uuid_audio_stream {action} success for {uuid}")
            else:
                logger.warning(f"uuid_audio_stream {action} failed for {uuid}: {result}")
            
            return success
        except Exception as e:
            logger.error(f"uuid_audio_stream error: {e}")
            return False
    
    async def uuid_bridge(self, uuid_a: str, uuid_b: str) -> bool:
        """
        Cria bridge entre duas chamadas.
        
        Args:
            uuid_a: UUID da primeira chamada
            uuid_b: UUID da segunda chamada
        
        Returns:
            True se sucesso
        """
        try:
            logger.info(f"[DEBUG] Sending uuid_bridge command: {uuid_a} <-> {uuid_b}")
            result = await self.execute_api(f"uuid_bridge {uuid_a} {uuid_b}")
            logger.info(f"[DEBUG] uuid_bridge raw result: '{result}'")
            
            success = "+OK" in result
            
            if success:
                logger.info(f"uuid_bridge success: {uuid_a} <-> {uuid_b}")
            else:
                logger.warning(f"uuid_bridge failed: {result}")
            
            return success
        except Exception as e:
            logger.error(f"uuid_bridge error: {e}")
            return False
    
    async def uuid_kill(self, uuid: str, cause: str = "NORMAL_CLEARING") -> bool:
        """
        Encerra uma chamada.
        
        Args:
            uuid: UUID da chamada
            cause: Hangup cause
        
        Returns:
            True se sucesso
        """
        try:
            result = await self.execute_api(f"uuid_kill {uuid} {cause}")
            return "+OK" in result
        except Exception as e:
            logger.error(f"uuid_kill error: {e}")
            return False
    
    async def uuid_hold(self, uuid: str, on: bool = True) -> bool:
        """
        Coloca ou retira chamada da espera.
        
        Args:
            uuid: UUID da chamada
            on: True para colocar em espera, False para retirar
        
        Returns:
            True se sucesso
        """
        logger.info(
            f"⏸️ [UUID_HOLD] Iniciando {'HOLD' if on else 'UNHOLD'}...",
            extra={
                "uuid": uuid,
                "on": on,
                "host": self.host,
                "port": self.port,
                "connected": self._connected,
            }
        )
        
        try:
            if on:
                logger.debug(f"⏸️ [UUID_HOLD] Executando: uuid_hold {uuid}")
                result = await self.execute_api(f"uuid_hold {uuid}")
            else:
                logger.debug(f"⏸️ [UUID_HOLD] Executando: uuid_hold off {uuid}")
                result = await self.execute_api(f"uuid_hold off {uuid}")
            
            logger.info(f"⏸️ [UUID_HOLD] Resultado: {result[:200] if result else 'None'}")
            
            success = result and "+OK" in result
            if success:
                logger.info(f"⏸️ [UUID_HOLD] ✅ SUCESSO: {'HOLD' if on else 'UNHOLD'}")
            else:
                logger.warning(f"⏸️ [UUID_HOLD] ❌ FALHA: {result}")
            
            return success
        except Exception as e:
            logger.error(f"⏸️ [UUID_HOLD] ❌ ERRO: {e}", exc_info=True)
            return False
    
    async def uuid_fileman(
        self,
        uuid: str,
        command: str,
        args: str = ""
    ) -> bool:
        """
        Controla playback de arquivos (pause, resume, seek, etc).
        
        Args:
            uuid: UUID da chamada
            command: Comando (pause, truncate, restart, seek, speed, volume, etc)
            args: Argumentos do comando
        
        Returns:
            True se sucesso
        """
        try:
            cmd = f"uuid_fileman {uuid} {command}"
            if args:
                cmd += f" {args}"
            result = await self.execute_api(cmd)
            return "+OK" in result
        except Exception as e:
            logger.error(f"uuid_fileman error: {e}")
            return False
    
    async def uuid_audio(
        self,
        uuid: str,
        action: str,
        direction: str = "both"
    ) -> bool:
        """
        Controla mute/unmute de áudio.
        
        Args:
            uuid: UUID da chamada
            action: start ou stop (mute/unmute)
            direction: read, write, ou both
        
        Returns:
            True se sucesso
        """
        try:
            result = await self.execute_api(f"uuid_audio {uuid} {action} {direction}")
            return "+OK" in result
        except Exception as e:
            logger.error(f"uuid_audio error: {e}")
            return False
    
    async def originate(
        self,
        dial_string: str,
        app: str = "&park()",
        timeout: int = 30,
        variables: Optional[Dict[str, str]] = None
    ) -> OriginateResult:
        """
        Origina nova chamada.
        
        Args:
            dial_string: String de discagem (ex: "user/1000@domain.com")
            app: Aplicação a executar após atender (default: park)
            timeout: Timeout de originação em segundos
            variables: Variáveis de canal a setar
        
        Returns:
            OriginateResult com sucesso/falha e detalhes (hangup_cause, error_message)
        """
        try:
            # Gerar UUID para a nova chamada
            new_uuid = str(uuid_module.uuid4())
            
            # Construir variáveis - sempre incluir timeout e uuid
            all_vars = {
                "origination_uuid": new_uuid,
                "originate_timeout": str(timeout),
                "call_timeout": str(timeout),
            }
            
            # Adicionar variáveis do usuário
            if variables:
                all_vars.update(variables)
            
            # Formatar string de variáveis para ESL
            # NOTA: Não usar aspas simples - ESL não processa bem
            # Substituir espaços por underscores em valores problemáticos
            var_parts = []
            for k, v in all_vars.items():
                value = str(v)
                # Para variáveis de caller_id, substituir espaços por underscores
                if k in ("origination_caller_id_name", "effective_caller_id_name", 
                         "caller_id_name", "origination_callee_id_name"):
                    value = value.replace(" ", "_").replace("'", "").replace(",", "")
                var_parts.append(f"{k}={value}")
            
            # NOTA: [] são variáveis locais (por leg), {} são globais
            # Para originate via ESL, [] funciona melhor
            var_string = "[" + ",".join(var_parts) + "]"
            # O formato é: originate [vars]dial_string app (SEM espaço entre ] e dial_string)
            cmd = f"originate {var_string}{dial_string} {app}"
            
            logger.info(f"Originate command: {cmd}")
            logger.debug(f"Originate dial_string: {dial_string}")
            logger.debug(f"Originate app: {app}")
            result = await self.execute_api(cmd)
            
            if "+OK" in result:
                logger.info(f"Originate success, UUID: {new_uuid}")
                return OriginateResult(success=True, uuid=new_uuid)
            else:
                # Extrair hangup cause do erro
                # Formato: -ERR HANGUP_CAUSE ou -ERR [CAUSE] message
                hangup_cause = self._extract_hangup_cause(result)
                
                logger.warning(
                    f"Originate failed: {result}",
                    extra={"hangup_cause": hangup_cause}
                )
                
                return OriginateResult(
                    success=False,
                    hangup_cause=hangup_cause,
                    error_message=result.strip()
                )
                
        except Exception as e:
            logger.error(f"Originate error: {e}")
            return OriginateResult(
                success=False,
                error_message=str(e)
            )
    
    def _extract_hangup_cause(self, error_result: str) -> Optional[str]:
        """
        Extrai hangup cause de uma resposta de erro do FreeSWITCH.
        
        Formatos conhecidos:
        - "-ERR USER_NOT_REGISTERED"
        - "-ERR NO_ANSWER"
        - "-ERR USER_BUSY"
        - "-ERR [cause] more details"
        
        Args:
            error_result: String de erro do FreeSWITCH
            
        Returns:
            Hangup cause ou None se não identificado
        """
        if not error_result:
            return None
        
        # Remover prefixo -ERR e limpar whitespace/newlines
        clean = error_result.replace("-ERR", "").strip()
        # Remover quebras de linha e espaços extras
        clean = " ".join(clean.split())
        
        # Lista de hangup causes conhecidos do FreeSWITCH
        # Ref: https://freeswitch.org/confluence/display/FREESWITCH/Hangup+Cause+Code+Table
        known_causes = [
            "USER_NOT_REGISTERED",
            "SUBSCRIBER_ABSENT",
            "UNALLOCATED_NUMBER",
            "NO_ROUTE_DESTINATION",
            "USER_BUSY",
            "NORMAL_CIRCUIT_CONGESTION",
            "NO_ANSWER",
            "NO_USER_RESPONSE",
            "ORIGINATOR_CANCEL",
            "ALLOTTED_TIMEOUT",
            "CALL_REJECTED",
            "USER_CHALLENGE",
            "DO_NOT_DISTURB",
            "DESTINATION_OUT_OF_ORDER",
            "NETWORK_OUT_OF_ORDER",
            "TEMPORARY_FAILURE",
            "SWITCH_CONGESTION",
            "MEDIA_TIMEOUT",
            "GATEWAY_DOWN",
            "INVALID_GATEWAY",
            "NORMAL_CLEARING",
            "NORMAL_UNSPECIFIED",
            "RECOVERY_ON_TIMER_EXPIRE",
            "INCOMPATIBLE_DESTINATION",
            "INVALID_NUMBER_FORMAT",
            "FACILITY_REJECTED",
            "FACILITY_NOT_SUBSCRIBED",
            "INCOMING_CALL_BARRED",
            "OUTGOING_CALL_BARRED",
        ]
        
        # Verificar se algum cause conhecido está no erro
        for cause in known_causes:
            if cause in clean.upper():
                return cause
        
        # Se não encontrou, retornar a primeira palavra (provável cause)
        parts = clean.split()
        if parts:
            first_word = parts[0].upper()
            # Remover caracteres não alfanuméricos
            first_word = re.sub(r'[^A-Z_]', '', first_word)
            if first_word and len(first_word) > 3:
                return first_word
        
        return None
    
    async def uuid_getvar(self, uuid: str, variable: str) -> Optional[str]:
        """
        Obtém variável de canal.
        
        Args:
            uuid: UUID da chamada
            variable: Nome da variável
        
        Returns:
            Valor da variável ou None
        """
        try:
            result = await self.execute_api(f"uuid_getvar {uuid} {variable}")
            if result and not result.startswith("-ERR"):
                return result.strip()
            return None
        except Exception:
            return None
    
    async def uuid_setvar(self, uuid: str, variable: str, value: str) -> bool:
        """
        Define variável de canal.
        
        Args:
            uuid: UUID da chamada
            variable: Nome da variável
            value: Valor a definir
        
        Returns:
            True se sucesso
        """
        try:
            result = await self.execute_api(f"uuid_setvar {uuid} {variable} {value}")
            return "+OK" in result
        except Exception:
            return False
    
    async def show_channels(self) -> str:
        """Lista canais ativos."""
        return await self.execute_api("show channels")
    
    async def uuid_exists(self, uuid: str) -> bool:
        """Verifica se UUID existe."""
        try:
            result = await self.execute_api(f"uuid_exists {uuid}")
            return "true" in result.lower()
        except Exception:
            return False
    
    def _parse_sofia_registrations(self, output: str) -> List[Dict[str, str]]:
        """
        Parse robusto da saída de 'sofia status profile internal reg'.
        
        Retorna lista de dicionários com informações de cada registro:
        {
            'user': '1001@ativo.netplay.net.br',
            'contact': 'sip:1001@177.72.14.10:5060',
            'status': 'Registered(AUTO-NAT)',
            'call_id': '...',
            'ip': '...',
            'port': '...'
        }
        """
        import re
        
        registrations = []
        current_reg: Dict[str, str] = {}
        
        lines = output.split('\n')
        
        for line in lines:
            line = line.strip()
            
            # Skip linhas de cabeçalho/rodapé
            if not line or line.startswith('=') or line.startswith('Registrations') or line.startswith('Total items'):
                continue
            
            # Parse formato "Key:\tValue" ou "Key:    \tValue"
            if ':' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    
                    # Mapear campos importantes
                    if key == 'Call-ID':
                        # Call-ID é o PRIMEIRO campo de cada registro
                        # Se já temos um registro com user, salvar e começar novo
                        if current_reg.get('user'):
                            registrations.append(current_reg)
                        current_reg = {'call_id': value}
                    
                    elif key == 'User':
                        current_reg['user'] = value
                    
                    elif key == 'Contact':
                        # Extrair SIP URI do formato: "" <sip:1001@177.72.14.10:5060;...>
                        match = re.search(r'<sip:([^;>]+)', value)
                        if match:
                            current_reg['contact'] = f"sip:{match.group(1)}"
                        else:
                            current_reg['contact'] = value
                    
                    elif key == 'Status':
                        current_reg['status'] = value
                    
                    elif key == 'IP':
                        current_reg['ip'] = value
                    
                    elif key == 'Port':
                        current_reg['port'] = value
        
        # Adicionar último registro se tiver user
        if current_reg.get('user'):
            registrations.append(current_reg)
        
        return registrations
    
    async def check_extension_registered(self, extension: str, domain: str) -> tuple[bool, Optional[str], bool]:
        """
        Verifica se uma extensão está registrada (online) no FreeSWITCH.
        
        Usa o comando 'sofia status profile internal reg' para verificar
        se o ramal tem registro ativo.
        
        Args:
            extension: Número do ramal (ex: "1001")
            domain: Domínio do ramal (ex: "empresa.com.br")
        
        Returns:
            Tuple (is_registered, contact_info, check_successful)
            - is_registered: True se o ramal está registrado
            - contact_info: Endereço de contato (IP:porta) se registrado
            - check_successful: True se a verificação foi executada com sucesso
              (False em caso de timeout/erro - nesse caso, tente o originate mesmo assim)
        
        NOTA: Esta verificação é útil para dar feedback rápido ao usuário
        antes de tentar originate. Se o ramal não está registrado, o
        originate falharia com USER_NOT_REGISTERED após o timeout.
        """
        logger.info(
            f"📞 [CHECK_EXTENSION] Verificando registro de {extension}@{domain}...",
            extra={
                "extension": extension,
                "domain": domain,
                "timeout": ESL_REGISTRATION_TIMEOUT,
                "host": self.host,
                "connected": self._connected,
            }
        )
        
        try:
            # Buscar TODOS os registros
            logger.debug(f"📞 [CHECK_EXTENSION] Executando: sofia status profile internal reg")
            result = await asyncio.wait_for(
                self.execute_api("sofia status profile internal reg"),
                timeout=ESL_REGISTRATION_TIMEOUT
            )
            
            if not result:
                logger.debug(f"📞 [CHECK_EXTENSION] Resultado vazio")
                return (False, None, True)
            
            # DEBUG: Logar resultado completo para diagnóstico
            logger.debug(f"📞 [CHECK_EXTENSION] Resultado bruto:\n{result[:1000]}...")
            
            # Parser robusto dos registros
            registrations = self._parse_sofia_registrations(result)
            logger.debug(f"📞 [CHECK_EXTENSION] Encontrados {len(registrations)} registros ativos")
            
            # Procurar pelo usuário exato (MULTI-TENANT SAFE)
            # Normalizar domínio para comparação
            domain_base = domain.split(":")[0].lower()
            target_user = f"{extension}@{domain_base}"
            
            for reg in registrations:
                reg_user = reg.get('user', '').lower()
                
                # Match exato: número@domínio
                if reg_user == target_user:
                    contact = reg.get('contact', '')
                    status = reg.get('status', '')
                    
                    logger.info(f"✅ Extension {extension}@{domain} is REGISTERED")
                    logger.debug(f"   Contact: {contact}")
                    logger.debug(f"   Status: {status}")
                    
                    return (True, contact, True)
            
            # Não encontrado - logar registros disponíveis para debug
            available_users = [r.get('user', '?') for r in registrations]
            logger.debug(f"❌ Extension {extension}@{domain} NOT found")
            logger.debug(f"   Procurando: '{target_user}'")
            logger.debug(f"   Disponíveis: {available_users}")
            
            return (False, None, True)
            
        except asyncio.TimeoutError:
            logger.warning(
                f"📞 [CHECK_EXTENSION] Timeout após {ESL_REGISTRATION_TIMEOUT}s - ESL Inbound pode não estar configurado. Tentando originate mesmo assim.",
                extra={"extension": extension, "domain": domain}
            )
            # check_successful = False -> caller deve tentar originate mesmo assim
            return (False, None, False)
        except Exception as e:
            logger.warning(f"📞 [CHECK_EXTENSION] Falha: {e} - Tentando originate mesmo assim.")
            return (False, None, False)
    
    # =========================================================================
    # ANNOUNCED TRANSFER: Métodos para transferência com anúncio
    # Ref: voice-ai-ivr/openspec/changes/announced-transfer/
    # =========================================================================
    
    async def uuid_say(
        self,
        uuid: str,
        text: str,
        voice: str = "kal"
    ) -> bool:
        """
        Fala texto usando TTS do FreeSWITCH.
        
        Tenta múltiplos métodos em ordem:
        1. speak:: com mod_flite
        2. say:: com mod_say (para números/datas)
        3. playback de tom como indicação (fallback final)
        
        Args:
            uuid: UUID do canal
            text: Texto a falar
            voice: Voz do flite (kal, slt, rms, awb)
        
        Returns:
            True se algum método funcionou
        """
        logger.info(
            "🎤 [UUID_SAY] Iniciando TTS...",
            extra={
                "uuid": uuid,
                "voice": voice,
                "text_length": len(text),
                "text_preview": text[:80] if text else "",
                "host": self.host,
                "port": self.port,
                "connected": self._connected,
            }
        )
        
        try:
            # Escapar caracteres especiais que podem quebrar o comando
            text_escaped = (
                text
                .replace("'", "")
                .replace('"', "")
                .replace("\n", " ")
                .replace("|", " ")
                .replace("\\", "")
                .replace(":", " ")
                .strip()
            )
            
            if not text_escaped:
                logger.warning("🎤 [UUID_SAY] Texto vazio após escapar, retornando True")
                return True
            
            # Calcular tempo de espera baseado no texto
            word_count = len(text_escaped.split())
            wait_time = min(max(word_count * 0.4, 1.0), 10.0)
            
            # ========================================
            # MÉTODO 1: speak:: com mod_flite
            # ========================================
            logger.info(f"🎤 [UUID_SAY] Método 1: Tentando speak:: com mod_flite...")
            
            try:
                # Setar tts_engine e voice
                await self.execute_api(f"uuid_setvar {uuid} tts_engine flite")
                await self.execute_api(f"uuid_setvar {uuid} tts_voice {voice}")
                
                # Tentar speak:: direto
                result = await asyncio.wait_for(
                    self.execute_api(f"uuid_broadcast {uuid} 'speak::{text_escaped}' aleg"),
                    timeout=ESL_API_TIMEOUT
                )
                
                if result and "+OK" in result:
                    logger.info(f"🎤 [UUID_SAY] ✅ SUCESSO via speak::")
                    await asyncio.sleep(wait_time)
                    return True
                
                # Tentar formato alternativo
                result = await asyncio.wait_for(
                    self.execute_api(f"uuid_broadcast {uuid} 'speak::flite|{voice}|{text_escaped}' aleg"),
                    timeout=ESL_API_TIMEOUT
                )
                
                if result and "+OK" in result:
                    logger.info(f"🎤 [UUID_SAY] ✅ SUCESSO via speak::flite|voice|text")
                    await asyncio.sleep(wait_time)
                    return True
                    
            except asyncio.TimeoutError:
                logger.warning(f"🎤 [UUID_SAY] Método 1: Timeout")
            except Exception as e:
                logger.warning(f"🎤 [UUID_SAY] Método 1: Erro - {e}")
            
            # ========================================
            # MÉTODO 2: Tocar tom de notificação
            # ========================================
            logger.info(f"🎤 [UUID_SAY] Método 2: Tentando playback de tom...")
            
            try:
                # Tocar um tom breve para indicar que algo aconteceu
                result = await asyncio.wait_for(
                    self.execute_api(f"uuid_broadcast {uuid} 'tone_stream://%(200,100,400,450)' aleg"),
                    timeout=ESL_API_TIMEOUT
                )
                
                if result and "+OK" in result:
                    logger.info(f"🎤 [UUID_SAY] ✅ Tom de notificação tocado (mod_flite pode não estar disponível)")
                    await asyncio.sleep(0.5)
                    return True
                    
            except Exception as e:
                logger.warning(f"🎤 [UUID_SAY] Método 2: Erro - {e}")
            
            # ========================================
            # MÉTODO 3: Apenas aguardar (último recurso)
            # ========================================
            logger.warning(f"🎤 [UUID_SAY] ⚠️ Nenhum método de TTS funcionou, continuando sem áudio")
            await asyncio.sleep(0.5)  # Pequena pausa mesmo assim
            return False
            
        except Exception as e:
            logger.error(f"🎤 [UUID_SAY] ❌ ERRO GERAL: {e}", exc_info=True)
            return False
    
    async def uuid_playback(
        self,
        uuid: str,
        file_path: str,
        leg: str = "aleg"
    ) -> bool:
        """
        Toca arquivo de áudio para um canal.
        
        Args:
            uuid: UUID do canal
            file_path: Caminho do arquivo (local ou URL)
            leg: "aleg" (apenas este canal), "bleg" (outro canal), "both"
        
        Returns:
            True se comando enviado com sucesso
        """
        try:
            result = await self.execute_api(
                f"uuid_broadcast {uuid} '{file_path}' {leg}"
            )
            
            success = "+OK" in result
            if success:
                logger.debug(f"uuid_playback success: {uuid} - {file_path}")
            else:
                logger.warning(f"uuid_playback failed: {result}")
            
            return success
        except Exception as e:
            logger.error(f"uuid_playback error: {e}")
            return False
    
    async def uuid_recv_dtmf(
        self,
        uuid: str,
        timeout: float = 10.0,
        valid_digits: str = "0123456789*#"
    ) -> Optional[str]:
        """
        Aguarda DTMF de um canal específico.
        
        IMPORTANTE: Requer que eventos DTMF estejam subscritos.
        Use subscribe_events(["DTMF"]) antes.
        
        Args:
            uuid: UUID do canal
            timeout: Timeout em segundos
            valid_digits: Dígitos válidos para aceitar
        
        Returns:
            Dígito pressionado ou None se timeout/hangup
        """
        try:
            start = time.time()
            
            while time.time() - start < timeout:
                # Verificar se canal ainda existe
                if not await self.uuid_exists(uuid):
                    logger.debug(f"uuid_recv_dtmf: channel {uuid} no longer exists")
                    return None
                
                # Verificar fila de DTMFs para este UUID
                dtmf = self._get_dtmf_from_queue(uuid)
                if dtmf and dtmf in valid_digits:
                    logger.info(f"DTMF received: {dtmf} from {uuid}")
                    return dtmf
                
                await asyncio.sleep(0.1)
            
            logger.debug(f"uuid_recv_dtmf: timeout for {uuid}")
            return None
            
        except Exception as e:
            logger.error(f"uuid_recv_dtmf error: {e}")
            return None
    
    def _get_dtmf_from_queue(self, uuid: str) -> Optional[str]:
        """
        Obtém DTMF da fila interna para um UUID específico.
        
        A fila é populada pelo handler de eventos DTMF.
        
        Args:
            uuid: UUID do canal
        
        Returns:
            Dígito ou None se fila vazia
        """
        # A fila de DTMFs é mantida pelo _handle_dtmf_event
        if hasattr(self, '_dtmf_queue') and uuid in self._dtmf_queue:
            queue = self._dtmf_queue[uuid]
            if queue:
                return queue.pop(0)
        return None
    
    def _store_dtmf(self, uuid: str, digit: str) -> None:
        """
        Armazena DTMF na fila interna.
        
        Chamado pelo handler de eventos DTMF.
        
        Args:
            uuid: UUID do canal
            digit: Dígito DTMF
        """
        if not hasattr(self, '_dtmf_queue'):
            self._dtmf_queue = {}
        
        if uuid not in self._dtmf_queue:
            self._dtmf_queue[uuid] = []
        
        # Limitar tamanho da fila para evitar memory leak
        if len(self._dtmf_queue[uuid]) < 100:
            self._dtmf_queue[uuid].append(digit)
            logger.debug(f"DTMF stored: {digit} for {uuid}")
    
    async def wait_for_reject_or_timeout(
        self,
        uuid: str,
        timeout: float = 5.0
    ) -> str:
        """
        Aguarda recusa (DTMF 2 ou hangup) ou timeout (aceitar).
        
        Modelo híbrido para announced transfer:
        - Não fazer nada por X segundos = aceitar
        - Pressionar 2 = recusar
        - Desligar = recusar
        
        Args:
            uuid: UUID do canal (B-leg)
            timeout: Tempo para aceitar automaticamente (segundos)
        
        Returns:
            "accept" - Timeout (humano aguardou)
            "reject" - DTMF 2 pressionado
            "hangup" - Humano desligou
        """
        try:
            start = time.time()
            
            logger.info(f"Waiting for reject or timeout ({timeout}s) on {uuid}")
            
            while time.time() - start < timeout:
                # Verificar se canal ainda existe
                if not await self.uuid_exists(uuid):
                    logger.info(f"B-leg hangup detected: {uuid}")
                    return "hangup"
                
                # Verificar se DTMF 2 foi pressionado
                dtmf = self._get_dtmf_from_queue(uuid)
                if dtmf == "2":
                    logger.info(f"DTMF 2 (reject) received from {uuid}")
                    return "reject"
                
                await asyncio.sleep(0.1)
            
            # Timeout = aceitar
            logger.info(f"Timeout reached, accepting transfer for {uuid}")
            return "accept"
            
        except Exception as e:
            logger.error(f"wait_for_reject_or_timeout error: {e}")
            return "hangup"


# Singleton para uso global
_esl_client: Optional[AsyncESLClient] = None


def get_esl_client() -> AsyncESLClient:
    """Retorna instância singleton do ESL client."""
    global _esl_client
    if _esl_client is None:
        _esl_client = AsyncESLClient()
    return _esl_client


def create_esl_client_from_settings(settings: Dict[str, Any]) -> AsyncESLClient:
    """
    Cria cliente ESL com configurações específicas.
    
    Útil quando precisamos usar configurações do banco de dados ao invés de
    variáveis de ambiente.
    
    Args:
        settings: Dict com configurações (esl_host, esl_port, esl_password)
        
    Returns:
        Novo AsyncESLClient configurado
    """
    host = _resolve_esl_host(settings.get('esl_host', ESL_HOST))
    return AsyncESLClient(
        host=host,
        port=int(settings.get('esl_port', ESL_PORT)),
        password=settings.get('esl_password', ESL_PASSWORD)
    )


async def get_esl_for_domain(domain_uuid: str) -> AsyncESLClient:
    """
    Retorna ESL client configurado para o domínio.
    
    Busca configurações do banco de dados e cria cliente apropriado.
    Se falhar, retorna singleton com configurações padrão.
    
    NOTA: Para melhor performance em produção, considerar cache de clientes por domínio.
    
    Args:
        domain_uuid: UUID do domínio
        
    Returns:
        AsyncESLClient configurado
    """
    try:
        from services.database import db
        from uuid import UUID
        
        settings = await db.get_domain_settings(UUID(domain_uuid))
        
        # Se configurações são diferentes do singleton, criar novo cliente
        singleton = get_esl_client()
        
        db_host = _resolve_esl_host(settings.get('esl_host', ESL_HOST))
        db_port = int(settings.get('esl_port', ESL_PORT))
        
        if singleton.host != db_host or singleton.port != db_port:
            # Configurações diferentes - criar cliente específico
            client = create_esl_client_from_settings(settings)
            return client
        
        # Mesmas configurações - usar singleton
        return singleton
        
    except Exception as e:
        logger.warning(f"Failed to load ESL settings for domain {domain_uuid}, using defaults: {e}")
        return get_esl_client()
