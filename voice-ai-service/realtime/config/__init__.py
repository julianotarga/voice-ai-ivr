"""
Config module for Voice AI Realtime.

Centraliza configurações de prompts e regras conversacionais.
"""

from .prompts import (
    CONVERSATIONAL_RULES,
    EMOTIONAL_ADAPTATION,
    CONTEXT_COHERENCE,
    PROACTIVE_ASSISTANCE,
    get_enhanced_prompt,
    get_minimal_prompt_rules,
)

__all__ = [
    "CONVERSATIONAL_RULES",
    "EMOTIONAL_ADAPTATION",
    "CONTEXT_COHERENCE",
    "PROACTIVE_ASSISTANCE",
    "get_enhanced_prompt",
    "get_minimal_prompt_rules",
]
