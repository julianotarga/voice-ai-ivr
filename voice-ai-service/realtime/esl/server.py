"""
ESL Outbound Server

Servidor que recebe conexões ESL outbound do FreeSWITCH.
Cada chamada que entra no dialplan com "socket" application
conecta aqui e é gerenciada pelo VoiceAIApplication.

Referências:
- https://github.com/EvoluxBR/greenswitch
- openspec/changes/refactor-esl-rtp-bridge/design.md

Uso:
    python -m realtime.esl.server
"""

import os
import logging
import signal
import sys
from typing import Optional, Callable

import gevent
from greenswitch import OutboundESLServer

from .application import VoiceAIApplication

logger = logging.getLogger(__name__)


class ESLOutboundServer:
    """
    Servidor ESL Outbound que recebe conexões do FreeSWITCH.
    
    Quando uma chamada entra no dialplan com:
        <action application="socket" data="127.0.0.1:8022 async full"/>
    
    O FreeSWITCH conecta aqui e passa controle da chamada.
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8022,
        max_connections: int = 100,
        application_class: Optional[type] = None,
    ):
        """
        Args:
            host: IP para bind do servidor
            port: Porta TCP para ESL outbound
            max_connections: Máximo de chamadas simultâneas
            application_class: Classe que herda VoiceAIApplication
        """
        self.host = host
        self.port = port
        self.max_connections = max_connections
        self.application_class = application_class or VoiceAIApplication
        
        self._server: Optional[OutboundESLServer] = None
        self._running = False
        
        logger.info(
            f"ESL Outbound Server initialized - {host}:{port} "
            f"(max_connections={max_connections})"
        )
    
    def start(self) -> None:
        """Inicia o servidor ESL em blocking mode."""
        self._running = True
        
        logger.info(f"Starting ESL Outbound Server on {self.host}:{self.port}")
        
        try:
            self._server = OutboundESLServer(
                bind_address=self.host,
                bind_port=self.port,
                application=self.application_class,
                max_connections=self.max_connections,
            )
            
            # Registrar signal handlers para graceful shutdown
            gevent.signal_handler(signal.SIGINT, self._handle_signal, signal.SIGINT)
            gevent.signal_handler(signal.SIGTERM, self._handle_signal, signal.SIGTERM)
            
            # Bloqueia aqui até stop()
            self._server.listen()
            
        except Exception as e:
            logger.exception(f"ESL Server error: {e}")
            self._running = False
            raise
    
    def stop(self) -> None:
        """Para o servidor graciosamente."""
        logger.info("Stopping ESL Outbound Server...")
        self._running = False
        
        if self._server:
            self._server.stop()
            self._server = None
        
        logger.info("ESL Outbound Server stopped")
    
    def _handle_signal(self, signum: int) -> None:
        """Handler para SIGINT/SIGTERM."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down...")
        self.stop()
    
    @property
    def is_running(self) -> bool:
        """Retorna True se servidor está rodando."""
        return self._running
    
    @property
    def connection_count(self) -> int:
        """Retorna número de conexões ativas."""
        if self._server:
            return self._server.connection_count
        return 0


def create_server(
    host: Optional[str] = None,
    port: Optional[int] = None,
    application_class: Optional[type] = None,
) -> ESLOutboundServer:
    """
    Factory function para criar servidor ESL.
    
    Args:
        host: IP para bind (default: ESL_SERVER_HOST ou 0.0.0.0)
        port: Porta (default: ESL_SERVER_PORT ou 8022)
        application_class: Classe de aplicação customizada
    
    Returns:
        ESLOutboundServer configurado
    """
    return ESLOutboundServer(
        host=host or os.getenv("ESL_SERVER_HOST", "0.0.0.0"),
        port=port or int(os.getenv("ESL_SERVER_PORT", "8022")),
        max_connections=int(os.getenv("ESL_MAX_CONNECTIONS", "100")),
        application_class=application_class,
    )


def main():
    """Entry point para rodar servidor standalone."""
    logging.basicConfig(
        level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    logger.info("=" * 60)
    logger.info("Voice AI ESL Outbound Server")
    logger.info("=" * 60)
    
    server = create_server()
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
