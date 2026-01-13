"""
Structured Logging Configuration.

Referências:
- openspec/changes/voice-ai-realtime/tasks.md (6.2)
- .context/docs/architecture.md: Observabilidade

Features:
- Logging estruturado com structlog
- Contexto por sessão
- Métricas de latência
- Log rotation
"""

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import structlog
from structlog.types import Processor


def add_timestamp(
    logger: logging.Logger,
    method_name: str,
    event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Adiciona timestamp ISO."""
    event_dict["timestamp"] = datetime.utcnow().isoformat() + "Z"
    return event_dict


def add_service_info(
    logger: logging.Logger,
    method_name: str,
    event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Adiciona informações do serviço."""
    event_dict["service"] = "voice-ai-realtime"
    event_dict["version"] = "2.0.0"
    return event_dict


def extract_from_record(
    logger: logging.Logger,
    method_name: str,
    event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Extrai dados do record do logging padrão."""
    record = event_dict.get("_record")
    if record:
        event_dict["logger"] = record.name
        event_dict["level"] = record.levelname
        if record.exc_info:
            event_dict["exception"] = structlog.processors.format_exc_info(record.exc_info)
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    log_dir: Optional[str] = None,
    json_format: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
) -> None:
    """
    Configura logging estruturado.
    
    Args:
        log_level: Nível de log (DEBUG, INFO, WARNING, ERROR)
        log_dir: Diretório de logs (None = stdout apenas)
        json_format: Usar formato JSON (True para produção)
        max_bytes: Tamanho máximo do arquivo de log
        backup_count: Número de backups a manter
    """
    
    # Processors para structlog
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        add_timestamp,
        add_service_info,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    
    if json_format:
        # Formato JSON para produção
        shared_processors.append(structlog.processors.JSONRenderer())
    else:
        # Formato legível para desenvolvimento
        shared_processors.append(structlog.dev.ConsoleRenderer(colors=True))
    
    # Configurar structlog
    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Configurar logging padrão para bibliotecas
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Handler para stdout
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    
    if json_format:
        console_handler.setFormatter(
            logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}')
        )
    else:
        console_handler.setFormatter(
            logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
        )
    
    root_logger.addHandler(console_handler)
    
    # Handler para arquivo (com rotation)
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / "voice-ai-realtime.log",
            maxBytes=max_bytes,
            backupCount=backup_count
        )
        file_handler.setLevel(getattr(logging, log_level.upper()))
        file_handler.setFormatter(
            logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}')
        )
        root_logger.addHandler(file_handler)
    
    # Silenciar logs verbose de bibliotecas
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """
    Obtém logger com contexto.
    
    Uso:
        logger = get_logger(__name__)
        logger.info("message", key="value")
    """
    return structlog.get_logger(name)


class SessionLogger:
    """
    Logger com contexto de sessão.
    
    Uso:
        with SessionLogger(call_uuid, domain_uuid) as logger:
            logger.info("Session started")
            logger.info("Audio received", bytes=1234)
    """
    
    def __init__(
        self,
        call_uuid: str,
        domain_uuid: str,
        secretary_name: Optional[str] = None
    ):
        self.call_uuid = call_uuid
        self.domain_uuid = domain_uuid
        self.secretary_name = secretary_name
        self._logger = structlog.get_logger()
        self._start_time = datetime.now()
    
    def __enter__(self) -> 'SessionLogger':
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            call_uuid=self.call_uuid,
            domain_uuid=self.domain_uuid,
            secretary_name=self.secretary_name,
        )
        self.info("Session context initialized")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self._start_time).total_seconds()
        self.info("Session context ended", duration_seconds=duration)
        structlog.contextvars.clear_contextvars()
    
    def debug(self, message: str, **kwargs):
        self._logger.debug(message, **kwargs)
    
    def info(self, message: str, **kwargs):
        self._logger.info(message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        self._logger.warning(message, **kwargs)
    
    def error(self, message: str, **kwargs):
        self._logger.error(message, **kwargs)
    
    def log_latency(self, operation: str, latency_ms: float, **kwargs):
        """Log de latência de operação."""
        self._logger.info(
            f"Latency: {operation}",
            operation=operation,
            latency_ms=latency_ms,
            **kwargs
        )
    
    def log_audio(
        self,
        direction: str,
        bytes_count: int,
        duration_ms: Optional[float] = None
    ):
        """Log de áudio processado."""
        self._logger.debug(
            f"Audio {direction}",
            direction=direction,
            bytes=bytes_count,
            duration_ms=duration_ms
        )
    
    def log_turn(
        self,
        turn_number: int,
        user_text: Optional[str] = None,
        ai_text: Optional[str] = None,
        latency_ms: Optional[float] = None
    ):
        """Log de turno de conversa."""
        self._logger.info(
            f"Turn {turn_number}",
            turn_number=turn_number,
            user_text=user_text[:100] if user_text else None,
            ai_text=ai_text[:100] if ai_text else None,
            latency_ms=latency_ms
        )
    
    def log_transfer(
        self,
        destination: str,
        success: bool,
        error: Optional[str] = None
    ):
        """Log de transferência."""
        self._logger.info(
            "Call transfer",
            destination=destination,
            success=success,
            error=error
        )
    
    def log_error(self, error: Exception, context: Optional[str] = None):
        """Log de erro com contexto."""
        self._logger.error(
            f"Error: {context or 'Unknown'}",
            error_type=type(error).__name__,
            error_message=str(error),
            context=context
        )
