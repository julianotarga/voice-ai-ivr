"""
Transfer Handler - Gerencia transferências de chamadas.

Referências:
- openspec/changes/add-realtime-handoff-omni/tasks.md (4.1-4.4)
- openspec/changes/add-realtime-handoff-omni/design.md (Decision 1, 3)

Funcionalidades:
- Resolução de destino (ramal, departamento, fila)
- Carregamento dinâmico de transfer_rules do banco
- Transferência via ESL (Event Socket Library)
- Transferência com anúncio
- Logging de transferências
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import socket

if TYPE_CHECKING:
    from ..config_loader import ConfigLoader

logger = logging.getLogger(__name__)


class TransferType(Enum):
    """Tipos de transferência."""
    BLIND = "blind"            # Transferência cega
    ATTENDED = "attended"      # Transferência com anúncio
    QUEUE = "queue"            # Para fila de atendimento


class DestinationType(Enum):
    """Tipos de destino."""
    EXTENSION = "extension"     # Ramal
    DEPARTMENT = "department"   # Departamento
    QUEUE = "queue"            # Fila
    EXTERNAL = "external"      # Número externo
    VOICEMAIL = "voicemail"    # Caixa postal


@dataclass
class TransferDestination:
    """Destino de transferência resolvido."""
    type: DestinationType
    value: str
    display_name: str
    dialplan_extension: str
    context: str = "default"


@dataclass
class TransferResult:
    """Resultado de uma transferência."""
    success: bool
    destination: Optional[TransferDestination]
    error: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class ESLClient:
    """
    Cliente para FreeSWITCH Event Socket Library.
    
    Usado para controlar chamadas remotamente.
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8021,
        password: str = "ClueCon"
    ):
        self.host = host
        self.port = port
        self.password = password
        self._socket: Optional[socket.socket] = None
        self._connected = False
    
    async def connect(self) -> bool:
        """Conecta ao ESL."""
        try:
            loop = asyncio.get_event_loop()
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.setblocking(False)
            
            await loop.sock_connect(self._socket, (self.host, self.port))
            
            # Ler banner
            await self._recv()
            
            # Autenticar
            await self._send(f"auth {self.password}\n\n")
            response = await self._recv()
            
            if "+OK" in response:
                self._connected = True
                logger.info("Connected to FreeSWITCH ESL")
                return True
            else:
                logger.error(f"ESL auth failed: {response}")
                return False
                
        except Exception as e:
            logger.error(f"ESL connection error: {e}")
            return False
    
    async def _send(self, data: str) -> None:
        """Envia comando."""
        if self._socket:
            loop = asyncio.get_event_loop()
            await loop.sock_sendall(self._socket, data.encode())
    
    async def _recv(self, size: int = 4096) -> str:
        """Recebe resposta."""
        if self._socket:
            loop = asyncio.get_event_loop()
            data = await loop.sock_recv(self._socket, size)
            return data.decode()
        return ""
    
    async def execute(self, command: str) -> str:
        """Executa comando ESL."""
        if not self._connected:
            if not await self.connect():
                raise RuntimeError("Not connected to ESL")
        
        await self._send(f"api {command}\n\n")
        return await self._recv()
    
    async def uuid_transfer(
        self,
        uuid: str,
        destination: str,
        context: str = "default"
    ) -> bool:
        """
        Transfere chamada por UUID.
        
        Args:
            uuid: UUID da chamada
            destination: Destino (extensão)
            context: Contexto do dialplan
        """
        command = f"uuid_transfer {uuid} {destination} XML {context}"
        response = await self.execute(command)
        
        success = "+OK" in response
        if success:
            logger.info(f"Transfer success: {uuid} → {destination}")
        else:
            logger.error(f"Transfer failed: {response}")
        
        return success
    
    async def uuid_broadcast(
        self,
        uuid: str,
        audio_file: str,
        leg: str = "aleg"
    ) -> bool:
        """
        Reproduz áudio em uma chamada.
        
        Args:
            uuid: UUID da chamada
            audio_file: Caminho do arquivo de áudio
            leg: Leg da chamada (aleg/bleg/both)
        """
        command = f"uuid_broadcast {uuid} {audio_file} {leg}"
        response = await self.execute(command)
        return "+OK" in response
    
    async def close(self) -> None:
        """Fecha conexão."""
        if self._socket:
            self._socket.close()
            self._connected = False


class TransferHandler:
    """
    Handler para transferências de chamadas.
    
    Multi-tenant: Respeita domain_uuid em todas operações.
    
    Ref: openspec/changes/add-realtime-handoff-omni/design.md
    """
    
    def __init__(
        self,
        esl_host: str = "127.0.0.1",
        esl_port: int = 8021,
        config_loader: Optional["ConfigLoader"] = None
    ):
        self.esl = ESLClient(host=esl_host, port=esl_port)
        self._transfer_log: List[Dict[str, Any]] = []
        self._config_loader = config_loader
        
        # Cache local de department_map por domain (fallback se config_loader não disponível)
        self._department_map_cache: Dict[str, Dict[str, str]] = {}
    
    async def resolve_destination(
        self,
        domain_uuid: str,
        destination: str,
        destination_type: str = "extension"
    ) -> Optional[TransferDestination]:
        """
        Resolve destino de transferência.
        
        Args:
            domain_uuid: UUID do tenant
            destination: Destino solicitado (número, nome de dept, etc)
            destination_type: Tipo de destino
        
        Returns:
            TransferDestination resolvido ou None
        """
        dtype = DestinationType(destination_type)
        
        if dtype == DestinationType.EXTENSION:
            # Ramal direto
            return TransferDestination(
                type=dtype,
                value=destination,
                display_name=f"Ramal {destination}",
                dialplan_extension=destination,
                context="default"
            )
        
        elif dtype == DestinationType.DEPARTMENT:
            # Mapear departamento para extensão
            dept_map = await self._load_department_map(domain_uuid)
            if destination.lower() in dept_map:
                ext = dept_map[destination.lower()]
                return TransferDestination(
                    type=dtype,
                    value=destination,
                    display_name=f"Departamento {destination}",
                    dialplan_extension=ext,
                    context="default"
                )
            logger.warning(f"Department not found: {destination}")
            return None
        
        elif dtype == DestinationType.QUEUE:
            # Fila de atendimento
            return TransferDestination(
                type=dtype,
                value=destination,
                display_name=f"Fila {destination}",
                dialplan_extension=f"queue_{destination}",
                context="default"
            )
        
        elif dtype == DestinationType.VOICEMAIL:
            # Caixa postal
            return TransferDestination(
                type=dtype,
                value=destination,
                display_name=f"Caixa Postal {destination}",
                dialplan_extension=f"*99{destination}",
                context="default"
            )
        
        elif dtype == DestinationType.EXTERNAL:
            # Número externo (cuidado com permissões!)
            return TransferDestination(
                type=dtype,
                value=destination,
                display_name=f"Externo {destination}",
                dialplan_extension=destination,
                context="external"  # Contexto para chamadas externas
            )
        
        return None
    
    async def _load_department_map(
        self,
        domain_uuid: str,
        secretary_uuid: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Carrega mapeamento de departamentos a partir das transfer_rules.
        
        Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (4.1-4.4)
        
        Args:
            domain_uuid: UUID do tenant
            secretary_uuid: UUID da secretária (opcional)
        
        Returns:
            Dict mapeando nome de departamento (lowercase) para extensão
        """
        # Tentar usar ConfigLoader se disponível
        if self._config_loader:
            try:
                rules = await self._config_loader.get_transfer_rules(
                    domain_uuid=domain_uuid,
                    secretary_uuid=secretary_uuid
                )
                
                if rules:
                    dept_map: Dict[str, str] = {}
                    
                    for rule in rules:
                        # Adicionar nome do departamento
                        dept_map[rule.department_name.lower()] = rule.transfer_extension
                        
                        # Adicionar keywords como aliases
                        for keyword in rule.intent_keywords:
                            dept_map[keyword.lower()] = rule.transfer_extension
                    
                    logger.debug(f"Department map loaded from DB: {len(dept_map)} entries", extra={
                        "domain_uuid": domain_uuid,
                        "secretary_uuid": secretary_uuid
                    })
                    return dept_map
                    
            except Exception as e:
                logger.warning(f"Failed to load department map from DB, using fallback: {e}")
        
        # Fallback para mapeamento estático (compatibilidade)
        logger.debug("Using fallback department map (no config_loader or no rules)")
        return {
            "vendas": "200",
            "sales": "200",
            "suporte": "300",
            "support": "300",
            "financeiro": "400",
            "finance": "400",
            "rh": "500",
            "hr": "500",
            "recepção": "100",
            "reception": "100",
        }
    
    async def transfer_call(
        self,
        call_uuid: str,
        domain_uuid: str,
        destination: str,
        transfer_type: TransferType = TransferType.BLIND,
        announce_message: Optional[str] = None
    ) -> TransferResult:
        """
        Executa transferência de chamada.
        
        Args:
            call_uuid: UUID da chamada
            domain_uuid: UUID do tenant
            destination: Destino (extensão ou departamento)
            transfer_type: Tipo de transferência
            announce_message: Mensagem de anúncio (para attended)
        
        Returns:
            TransferResult com status
        """
        # Resolver destino
        resolved = await self.resolve_destination(
            domain_uuid,
            destination,
            self._infer_destination_type(destination)
        )
        
        if not resolved:
            return TransferResult(
                success=False,
                destination=None,
                error=f"Could not resolve destination: {destination}"
            )
        
        try:
            if transfer_type == TransferType.ATTENDED and announce_message:
                # Reproduzir anúncio antes de transferir
                await self.esl.uuid_broadcast(
                    call_uuid,
                    f"say:Transferindo para {resolved.display_name}"
                )
                await asyncio.sleep(2)  # Esperar anúncio
            
            # Executar transferência
            success = await self.esl.uuid_transfer(
                call_uuid,
                resolved.dialplan_extension,
                resolved.context
            )
            
            result = TransferResult(
                success=success,
                destination=resolved,
                error=None if success else "Transfer command failed"
            )
            
            # Log de auditoria
            self._log_transfer(call_uuid, domain_uuid, result)
            
            return result
            
        except Exception as e:
            logger.error(f"Transfer error: {e}")
            return TransferResult(
                success=False,
                destination=resolved,
                error=str(e)
            )
    
    def _infer_destination_type(self, destination: str) -> str:
        """Infere tipo de destino baseado no formato."""
        # Numérico simples = extensão
        if destination.isdigit() and len(destination) <= 5:
            return "extension"
        
        # Número longo = externo
        if destination.isdigit() and len(destination) > 5:
            return "external"
        
        # Começa com queue_ = fila
        if destination.startswith("queue_"):
            return "queue"
        
        # Começa com vm_ = voicemail
        if destination.startswith("vm_") or destination.startswith("*99"):
            return "voicemail"
        
        # Default = departamento (texto)
        return "department"
    
    def _log_transfer(
        self,
        call_uuid: str,
        domain_uuid: str,
        result: TransferResult
    ) -> None:
        """Registra transferência para auditoria."""
        log_entry = {
            "timestamp": result.timestamp.isoformat(),
            "call_uuid": call_uuid,
            "domain_uuid": domain_uuid,
            "success": result.success,
            "destination_type": result.destination.type.value if result.destination else None,
            "destination_value": result.destination.value if result.destination else None,
            "destination_dialplan": result.destination.dialplan_extension if result.destination else None,
            "error": result.error,
        }
        
        self._transfer_log.append(log_entry)
        
        # Limitar tamanho do log em memória
        if len(self._transfer_log) > 1000:
            self._transfer_log = self._transfer_log[-500:]
        
        logger.info(f"Transfer logged: {log_entry}")
    
    def get_transfer_log(
        self,
        domain_uuid: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Retorna log de transferências."""
        logs = self._transfer_log
        
        if domain_uuid:
            logs = [l for l in logs if l["domain_uuid"] == domain_uuid]
        
        return logs[-limit:]
    
    async def close(self) -> None:
        """Fecha conexão ESL."""
        await self.esl.close()


# Funções auxiliares para uso em function calls
async def handle_transfer_call(
    call_uuid: str,
    domain_uuid: str,
    destination: str,
    transfer_type: str = "blind",
    announce: bool = False
) -> Dict[str, Any]:
    """
    Handler para function call de transferência.
    
    Usado pelo LLM para executar transferências.
    """
    handler = TransferHandler()
    
    try:
        result = await handler.transfer_call(
            call_uuid=call_uuid,
            domain_uuid=domain_uuid,
            destination=destination,
            transfer_type=TransferType(transfer_type),
            announce_message="Transferindo sua chamada" if announce else None
        )
        
        return {
            "success": result.success,
            "destination": result.destination.display_name if result.destination else None,
            "error": result.error,
        }
    finally:
        await handler.close()
