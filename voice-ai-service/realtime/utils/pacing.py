"""
Conversation Pacing - Gerencia timing natural de respostas.

Este módulo implementa "breathing room" para tornar as respostas
da IA mais humanizadas, evitando respostas instantâneas que soam artificiais.

Ref: docs/PROJECT_EVOLUTION.md - Melhorias Conversacionais (P2)

Uso:
    pacing = ConversationPacing()
    
    # Quando usuário para de falar
    pacing.mark_user_speech_ended()
    
    # Antes de começar a responder
    await pacing.apply_natural_delay()
"""

import asyncio
import random
import time
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PacingConfig:
    """Configuração do pacing conversacional."""
    
    # Range de delay natural (segundos)
    # Humanos levam 200-500ms para começar a responder
    min_delay: float = 0.2  # 200ms mínimo
    max_delay: float = 0.4  # 400ms máximo
    
    # Delay extra para perguntas complexas (detectadas automaticamente)
    complex_question_extra_delay: float = 0.3
    
    # Habilitar/desabilitar pacing
    enabled: bool = True


class ConversationPacing:
    """
    Gerencia timing natural de respostas.
    
    Adiciona pequenos delays para evitar respostas instantâneas
    que parecem artificiais. Humanos naturalmente levam 200-500ms
    para começar a responder.
    
    Uso:
        pacing = ConversationPacing()
        
        # Quando VAD detecta fim da fala do usuário
        pacing.mark_user_speech_ended()
        
        # Opcionalmente, marcar que usuário fez pergunta complexa
        pacing.set_complex_question(True)
        
        # Antes de enviar resposta
        await pacing.apply_natural_delay()
    """
    
    def __init__(self, config: Optional[PacingConfig] = None):
        """
        Inicializa o pacing.
        
        Args:
            config: Configuração opcional (usa defaults se None)
        """
        self.config = config or PacingConfig()
        
        # Estado
        self._last_user_speech_end: Optional[float] = None
        self._last_user_speech_start: Optional[float] = None
        self._is_complex_question: bool = False
        self._total_delays_applied: int = 0
        self._total_delay_time: float = 0.0
    
    def mark_user_speech_started(self) -> None:
        """Marca o momento em que usuário começou a falar."""
        self._last_user_speech_start = time.time()
    
    def mark_user_speech_ended(self) -> None:
        """
        Marca o momento em que usuário parou de falar.
        
        Deve ser chamado quando VAD detecta fim da fala.
        """
        self._last_user_speech_end = time.time()
    
    def set_complex_question(self, is_complex: bool) -> None:
        """
        Marca se a última fala foi uma pergunta complexa.
        
        Perguntas complexas merecem um delay extra para parecer
        que a IA está "pensando" antes de responder.
        
        Args:
            is_complex: True se pergunta complexa
        """
        self._is_complex_question = is_complex
    
    def detect_complexity_from_text(self, text: str) -> None:
        """
        Detecta automaticamente se texto parece pergunta complexa.
        
        Heurísticas simples:
        - Múltiplas perguntas (vários ?)
        - Perguntas longas (>50 palavras)
        - Palavras-chave de complexidade
        
        Args:
            text: Texto transcrito do usuário
        """
        if not text:
            self._is_complex_question = False
            return
        
        text_lower = text.lower()
        
        # Múltiplas perguntas
        question_count = text.count("?")
        if question_count >= 2:
            self._is_complex_question = True
            return
        
        # Pergunta longa
        word_count = len(text.split())
        if word_count > 30:
            self._is_complex_question = True
            return
        
        # Palavras-chave de complexidade
        complex_keywords = [
            "como funciona",
            "me explica",
            "qual a diferença",
            "por que",
            "não entendi",
            "pode detalhar",
            "o que significa",
        ]
        
        for keyword in complex_keywords:
            if keyword in text_lower:
                self._is_complex_question = True
                return
        
        self._is_complex_question = False
    
    async def apply_natural_delay(self, context: str = "response") -> float:
        """
        Aplica delay natural se resposta seria artificialmente rápida.
        
        Args:
            context: Contexto para logging ("response", "function_call", etc.)
        
        Returns:
            Delay aplicado em segundos (0 se nenhum delay necessário)
        """
        if not self.config.enabled:
            return 0.0
        
        if not self._last_user_speech_end:
            return 0.0
        
        # Tempo desde fim da fala do usuário
        elapsed = time.time() - self._last_user_speech_end
        
        # Se já esperou o suficiente, não adicionar delay
        if elapsed >= self.config.min_delay:
            logger.debug(
                f"[PACING] No delay needed: elapsed={elapsed:.3f}s >= min={self.config.min_delay:.3f}s"
            )
            return 0.0
        
        # Calcular delay base
        target_delay = random.uniform(
            self.config.min_delay,
            self.config.max_delay
        )
        
        # Adicionar delay extra para perguntas complexas
        if self._is_complex_question:
            target_delay += self.config.complex_question_extra_delay
            logger.debug(f"[PACING] Complex question detected, adding extra delay")
        
        # NOTA: Lógica de "long pause" removida pois a duração da fala
        # não é um bom indicador de quando responder mais rápido.
        # O pacing natural de 200-400ms é suficiente para todos os casos.
        
        # Calcular delay restante
        remaining_delay = max(0, target_delay - elapsed)
        
        if remaining_delay > 0:
            logger.debug(
                f"[PACING] Applying delay: {remaining_delay:.3f}s "
                f"(target={target_delay:.3f}s, elapsed={elapsed:.3f}s, context={context})"
            )
            
            await asyncio.sleep(remaining_delay)
            
            # Estatísticas
            self._total_delays_applied += 1
            self._total_delay_time += remaining_delay
            
            return remaining_delay
        
        return 0.0
    
    def reset(self) -> None:
        """Reset para nova conversa."""
        self._last_user_speech_end = None
        self._last_user_speech_start = None
        self._is_complex_question = False
    
    def get_stats(self) -> dict:
        """
        Retorna estatísticas de pacing.
        
        Returns:
            Dict com total de delays e tempo total
        """
        return {
            "total_delays": self._total_delays_applied,
            "total_delay_time": round(self._total_delay_time, 3),
            "avg_delay": round(
                self._total_delay_time / self._total_delays_applied, 3
            ) if self._total_delays_applied > 0 else 0,
            "enabled": self.config.enabled,
        }


# Singleton para uso global (opcional)
_global_pacing: Optional[ConversationPacing] = None


def get_pacing() -> ConversationPacing:
    """
    Retorna instância global de pacing.
    
    Útil quando múltiplos componentes precisam acessar o mesmo pacing.
    """
    global _global_pacing
    if _global_pacing is None:
        _global_pacing = ConversationPacing()
    return _global_pacing


def reset_global_pacing() -> None:
    """Reseta o pacing global."""
    global _global_pacing
    if _global_pacing:
        _global_pacing.reset()
