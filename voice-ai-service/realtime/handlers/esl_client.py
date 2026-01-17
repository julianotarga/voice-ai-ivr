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
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING
import re

logger = logging.getLogger(__name__)

# Configurações ESL
ESL_HOST = os.getenv("ESL_HOST", "127.0.0.1")
ESL_PORT = int(os.getenv("ESL_PORT", "8021"))
ESL_PASSWORD = os.getenv("ESL_PASSWORD", "ClueCon")

# Timeouts
ESL_CONNECT_TIMEOUT = float(os.getenv("ESL_CONNECT_TIMEOUT", "5.0"))
ESL_READ_TIMEOUT = float(os.getenv("ESL_READ_TIMEOUT", "30.0"))
ESL_RECONNECT_DELAY = float(os.getenv("ESL_RECONNECT_DELAY", "2.0"))
ESL_MAX_RECONNECT_ATTEMPTS = int(os.getenv("ESL_MAX_RECONNECT_ATTEMPTS", "3"))


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
        
        # Flag para pausar event reader durante comandos
        # Evita "readuntil() called while another coroutine is already waiting"
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
        """Re-subscreve eventos após reconexão."""
        events = list(self._subscribed_events)
        self._subscribed_events.clear()
        
        # Pausar event reader durante resubscribe
        self._command_in_progress = True
        try:
            await asyncio.sleep(0.01)
            
            for event in events:
                try:
                    await self._send(f"event plain {event}\n\n")
                    await self._read_response(discard_events=True)
                    self._subscribed_events.add(event)
                except Exception as e:
                    logger.warning(f"Failed to resubscribe to {event}: {e}")
        finally:
            self._command_in_progress = False
    
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
    
    async def _read_response(self, timeout: float = ESL_READ_TIMEOUT, discard_events: bool = False) -> str:
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
        """
        if not self._reader:
            raise ESLConnectionError("Not connected")
        
        max_retries = 10  # Evitar loop infinito (quando discard_events=True)
        
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
                raise ESLError(f"Read timeout ({timeout}s)")
        
        raise ESLError("Max retries reached reading command response")
    
    async def _event_reader_loop(self) -> None:
        """Loop de leitura de eventos em background."""
        while self._connected:
            try:
                # Pausar enquanto um comando está em progresso
                # Evita "readuntil() called while another coroutine is already waiting"
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
            # NOTA: Este método NÃO usa lock porque roda no _event_reader_loop
            # O lock é usado apenas em execute_api() que pausa este loop
            response = await self._read_response(timeout=60.0, discard_events=False)
            
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
    
    async def execute_api(self, command: str) -> str:
        """
        Executa comando API do FreeSWITCH.
        
        Args:
            command: Comando a executar (ex: "show calls", "uuid_getvar uuid var")
        
        Returns:
            Resposta do comando
        """
        async with self._command_lock:
            if not self._connected:
                if not await self.connect():
                    raise ESLConnectionError("Failed to connect to ESL")
            
            # Pausar event reader loop durante o comando
            self._command_in_progress = True
            try:
                # Pequeno delay para garantir que o event reader pausou
                await asyncio.sleep(0.01)
                
                await self._send(f"api {command}\n\n")
                response = await self._read_response(discard_events=True)
                
                # Extrair body da resposta
                if "Content-Length:" in response:
                    parts = response.split("\n\n", 1)
                    if len(parts) > 1:
                        return parts[1]
                
                return response
            finally:
                self._command_in_progress = False
    
    async def execute_bgapi(self, command: str) -> str:
        """
        Executa comando em background (assíncrono).
        
        Args:
            command: Comando a executar
        
        Returns:
            Job-UUID do comando
        """
        async with self._command_lock:
            if not self._connected:
                if not await self.connect():
                    raise ESLConnectionError("Failed to connect to ESL")
            
            # Pausar event reader loop durante o comando
            self._command_in_progress = True
            try:
                await asyncio.sleep(0.01)
                
                await self._send(f"bgapi {command}\n\n")
                response = await self._read_response(discard_events=True)
                
                # Extrair Job-UUID
                match = re.search(r"Job-UUID:\s*([a-f0-9-]+)", response)
                if match:
                    return match.group(1)
                
                return response
            finally:
                self._command_in_progress = False
    
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
        """
        async with self._command_lock:
            self._command_in_progress = True
            try:
                await asyncio.sleep(0.01)
                
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
    
    async def unsubscribe_events(self, uuid: Optional[str] = None) -> None:
        """Remove filtros de eventos."""
        if uuid:
            async with self._command_lock:
                self._command_in_progress = True
                try:
                    await asyncio.sleep(0.01)
                    await self._send(f"filter delete Unique-ID {uuid}\n\n")
                    await self._read_response(discard_events=True)
                finally:
                    self._command_in_progress = False
    
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
            result = await self.execute_api(f"uuid_broadcast {uuid} {audio} {leg}")
            success = "+OK" in result or "Success" in result
            
            if success:
                logger.debug(f"uuid_broadcast success: {uuid} {audio}")
            else:
                logger.warning(f"uuid_broadcast failed: {result}")
            
            return success
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
        try:
            if on:
                result = await self.execute_api(f"uuid_hold {uuid}")
            else:
                result = await self.execute_api(f"uuid_hold off {uuid}")
            
            success = "+OK" in result
            if success:
                logger.info(f"uuid_hold {'on' if on else 'off'}: {uuid}")
            else:
                logger.warning(f"uuid_hold failed: {result}")
            
            return success
        except Exception as e:
            logger.error(f"uuid_hold error: {e}")
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
    ) -> Optional[str]:
        """
        Origina nova chamada.
        
        Args:
            dial_string: String de discagem (ex: "user/1000@domain.com")
            app: Aplicação a executar após atender (default: park)
            timeout: Timeout de originação em segundos
            variables: Variáveis de canal a setar
        
        Returns:
            UUID da nova chamada ou None se falhou
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
                return new_uuid
            else:
                logger.warning(f"Originate failed: {result}")
                return None
                
        except Exception as e:
            logger.error(f"Originate error: {e}")
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
    return AsyncESLClient(
        host=settings.get('esl_host', ESL_HOST),
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
        
        db_host = settings.get('esl_host', ESL_HOST)
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
