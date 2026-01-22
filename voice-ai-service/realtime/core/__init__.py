"""
Core - Infraestrutura de controle interno do Voice AI.

Este módulo contém componentes para gerenciamento de estado e eventos
sem dependência direta do FreeSWITCH para lógica de negócio.

Componentes:
- VoiceEvent, VoiceEventType: Sistema de eventos internos
- EventBus: Publicação/assinatura de eventos
- CallStateMachine, CallState: Máquina de estados para chamadas
- HeartbeatMonitor, ConnectionHealth: Monitor de saúde da conexão
- TimeoutManager, TimeoutConfig: Gerenciamento de timeouts

Referência: voice-ai-ivr/docs/PLANO-ARQUITETURA-INTERNA.md
"""

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus
from .state_machine import CallStateMachine, CallState
from .heartbeat import HeartbeatMonitor, ConnectionHealth
from .timeout_manager import TimeoutManager, TimeoutConfig

__all__ = [
    # Eventos
    'VoiceEvent',
    'VoiceEventType',
    'EventBus',
    
    # Estado
    'CallStateMachine',
    'CallState',
    
    # Monitoramento
    'HeartbeatMonitor',
    'ConnectionHealth',
    
    # Timeouts
    'TimeoutManager',
    'TimeoutConfig',
]
