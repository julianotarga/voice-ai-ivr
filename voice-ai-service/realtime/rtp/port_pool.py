"""
RTP Port Pool

Pool de portas UDP para alocação dinâmica de streams RTP.
Cada chamada aloca um par de portas (RTP/RTCP).

Referências:
- RFC 3550: RTP port allocation
- openspec/changes/refactor-esl-rtp-bridge/design.md
"""

import os
import logging
import threading
import socket
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)


class PortPool:
    """
    Pool de portas UDP para RTP.
    
    Por convenção, RTP usa portas pares e RTCP usa ímpares.
    Ex: RTP=10000, RTCP=10001
    
    O pool gerencia alocação e liberação de portas.
    """
    
    def __init__(
        self,
        start_port: int = 10000,
        end_port: int = 10100,
        bind_address: str = "0.0.0.0",
    ):
        """
        Args:
            start_port: Primeira porta do range (deve ser par)
            end_port: Última porta do range
            bind_address: IP para bind dos sockets
        """
        # Garantir que start é par
        if start_port % 2 != 0:
            start_port += 1
        
        self.start_port = start_port
        self.end_port = end_port
        self.bind_address = bind_address
        
        # Portas disponíveis (apenas pares para RTP)
        self._available: Set[int] = set(range(start_port, end_port, 2))
        
        # Portas em uso
        self._in_use: Set[int] = set()
        
        # Lock para thread safety
        self._lock = threading.Lock()
        
        logger.info(
            f"PortPool initialized: {start_port}-{end_port} "
            f"({len(self._available)} ports available)"
        )
    
    def allocate(self) -> Optional[Tuple[int, int]]:
        """
        Aloca um par de portas (RTP, RTCP).
        
        Returns:
            Tuple (rtp_port, rtcp_port) ou None se não há portas
        """
        with self._lock:
            if not self._available:
                logger.error("No ports available in pool")
                return None
            
            # Tentar portas até encontrar uma que funcione
            for rtp_port in sorted(self._available):
                rtcp_port = rtp_port + 1
                
                # Verificar se podemos fazer bind
                if self._can_bind(rtp_port) and self._can_bind(rtcp_port):
                    self._available.remove(rtp_port)
                    self._in_use.add(rtp_port)
                    
                    logger.debug(
                        f"Allocated ports RTP={rtp_port}, RTCP={rtcp_port} "
                        f"({len(self._available)} remaining)"
                    )
                    return (rtp_port, rtcp_port)
            
            logger.error("All ports are in use or unavailable")
            return None
    
    def release(self, rtp_port: int) -> bool:
        """
        Libera um par de portas.
        
        Args:
            rtp_port: Porta RTP a liberar
            
        Returns:
            True se liberado, False se não estava alocado
        """
        with self._lock:
            if rtp_port not in self._in_use:
                logger.warning(f"Port {rtp_port} not in use")
                return False
            
            self._in_use.remove(rtp_port)
            self._available.add(rtp_port)
            
            logger.debug(
                f"Released port {rtp_port} "
                f"({len(self._available)} available)"
            )
            return True
    
    def _can_bind(self, port: int) -> bool:
        """
        Verifica se porta pode ser usada (não está em uso).
        
        Args:
            port: Porta a verificar
            
        Returns:
            True se pode fazer bind
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.bind_address, port))
            sock.close()
            return True
        except OSError:
            return False
    
    @property
    def available_count(self) -> int:
        """Número de portas disponíveis."""
        with self._lock:
            return len(self._available)
    
    @property
    def in_use_count(self) -> int:
        """Número de portas em uso."""
        with self._lock:
            return len(self._in_use)
    
    @property
    def total_ports(self) -> int:
        """Total de pares de portas no pool."""
        return (self.end_port - self.start_port) // 2


# Singleton global
_global_pool: Optional[PortPool] = None
_pool_lock = threading.Lock()


def get_port_pool() -> PortPool:
    """
    Retorna pool global de portas.
    
    Configurável via environment variables:
    - RTP_PORT_MIN: Primeira porta (default: 10000)
    - RTP_PORT_MAX: Última porta (default: 10100)
    - RTP_BIND_ADDRESS: IP para bind (default: 0.0.0.0)
    """
    global _global_pool
    
    with _pool_lock:
        if _global_pool is None:
            _global_pool = PortPool(
                start_port=int(os.getenv("RTP_PORT_MIN", "10000")),
                end_port=int(os.getenv("RTP_PORT_MAX", "10100")),
                bind_address=os.getenv("RTP_BIND_ADDRESS", "0.0.0.0"),
            )
        return _global_pool
