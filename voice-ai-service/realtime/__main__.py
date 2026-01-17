"""
Realtime package entrypoint - Dual Mode Server

Suporta dois modos de operação:
1. WebSocket (default): Via mod_audio_stream - porta 8085
2. ESL + RTP: Via greenswitch - porta 8022 + UDP 10000-10100

Modo controlado por AUDIO_MODE env var:
- AUDIO_MODE=websocket (default)
- AUDIO_MODE=rtp
- AUDIO_MODE=dual (ambos simultaneamente)
"""

import asyncio
import logging
import os
import sys
import signal
import threading
from typing import Optional

# Logger
logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configura logging baseado em DEBUG env."""
    level = logging.DEBUG if os.getenv("DEBUG") else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    # Reduzir verbosidade de algumas libs
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def run_websocket_server(host: str, port: int) -> None:
    """Inicia servidor WebSocket (asyncio)."""
    from .server import run_server
    
    logger.info(f"Starting WebSocket server on {host}:{port}")
    asyncio.run(run_server(host=host, port=port))


def run_esl_server(host: str, port: int) -> None:
    """Inicia servidor ESL (gevent)."""
    from .esl import create_server
    
    logger.info(f"Starting ESL server on {host}:{port}")
    server = create_server(host=host, port=port)
    server.start()


def run_dual_mode(
    ws_host: str,
    ws_port: int,
    esl_host: str,
    esl_port: int,
) -> None:
    """
    Executa ambos os servidores simultaneamente (MODO DUAL).
    
    Arquitetura:
    - WebSocket Server (8085): Processa áudio via mod_audio_stream
    - ESL Outbound Server (8022): Recebe eventos (HANGUP, DTMF) e correlaciona com sessões
    
    O ESL NÃO processa áudio no modo dual - apenas eventos.
    
    Ref: openspec/changes/dual-mode-esl-websocket/proposal.md
    """
    from .esl import create_server, DualModeEventRelay, set_main_asyncio_loop
    from .server import run_server
    
    # Criar servidor ESL com DualModeEventRelay (não VoiceAIApplication)
    # O DualModeEventRelay só processa eventos, não áudio
    esl_server = create_server(
        host=esl_host,
        port=esl_port,
        application_class=DualModeEventRelay,  # ← Usar EventRelay no modo dual
    )
    
    # Thread para ESL (gevent)
    esl_thread = threading.Thread(
        target=esl_server.start,
        name="ESLEventRelay",
        daemon=True,
    )
    
    # Handler para shutdown gracioso
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        esl_server.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Iniciar ESL em thread separada
    logger.info(f"Starting ESL EventRelay server on {esl_host}:{esl_port}")
    esl_thread.start()
    
    # Rodar WebSocket na thread principal (asyncio)
    logger.info(f"Starting WebSocket server on {ws_host}:{ws_port}")
    
    async def run_with_loop_registration():
        """Executa servidor e registra o loop para o ESL EventRelay."""
        # Registrar o asyncio loop para que o ESL EventRelay possa despachar eventos
        loop = asyncio.get_running_loop()
        set_main_asyncio_loop(loop)
        logger.info("Asyncio loop registered for ESL event dispatching")
        
        # Importar e executar servidor
        from .server import RealtimeServer
        server = RealtimeServer(host=ws_host, port=ws_port)
        await server.start()
        
        try:
            await server.serve_forever()
        finally:
            await server.stop()
    
    try:
        asyncio.run(run_with_loop_registration())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        esl_server.stop()
        esl_thread.join(timeout=5.0)
        logger.info("Dual mode servers stopped")


def main() -> None:
    """Entry point principal."""
    setup_logging()
    
    # Configuração
    audio_mode = os.getenv("AUDIO_MODE", "websocket").lower()
    
    # WebSocket config
    ws_host = os.getenv("REALTIME_HOST", "0.0.0.0")
    ws_port = int(os.getenv("REALTIME_PORT", "8085"))
    
    # ESL config
    esl_host = os.getenv("ESL_SERVER_HOST", "0.0.0.0")
    esl_port = int(os.getenv("ESL_SERVER_PORT", "8022"))
    
    logger.info("=" * 60)
    logger.info("Voice AI Realtime Server")
    logger.info(f"Mode: {audio_mode.upper()}")
    logger.info("=" * 60)
    
    if audio_mode == "websocket":
        # Modo WebSocket apenas (via mod_audio_stream)
        run_websocket_server(ws_host, ws_port)
        
    elif audio_mode == "rtp" or audio_mode == "esl":
        # Modo ESL + RTP apenas
        run_esl_server(esl_host, esl_port)
        
    elif audio_mode == "dual":
        # Ambos os modos simultaneamente
        run_dual_mode(ws_host, ws_port, esl_host, esl_port)
        
    else:
        logger.error(f"Unknown AUDIO_MODE: {audio_mode}")
        logger.error("Valid modes: websocket, rtp, esl, dual")
        sys.exit(1)


if __name__ == "__main__":
    main()
