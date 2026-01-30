"""
Tools de Callback (Retorno de Ligação).

Implementa o fluxo completo de callback:
1. accept_callback - Cliente aceita receber ligação de retorno
2. provide_callback_number - Cliente fornece número diferente
3. use_current_extension - Cliente escolhe usar ramal/número atual
4. confirm_callback_number - Cliente confirma o número
5. schedule_callback - Cliente agenda horário preferido

Multi-tenant: domain_uuid obrigatório em todas as operações.

Regras de validação:
- Ramal interno: 2-5 dígitos (ex: 1001, 10001)
- Fixo com DDD: 10 dígitos (ex: 1831720011)
- Celular com DDD: 11 dígitos (ex: 11997751073)
"""

from typing import Any, Dict, Optional
from .base import VoiceAITool, ToolCategory, ToolContext, ToolResult, ValidationResult
import logging
import re

logger = logging.getLogger(__name__)


class PhoneNumberValidator:
    """Utilitários para validação de números de telefone brasileiros."""
    
    @staticmethod
    def normalize(number: str) -> str:
        """
        Normaliza número brasileiro para formato E.164.
        
        Exemplos:
        - "18997751073" → "5518997751073"
        - "5518997751073" → "5518997751073"
        - "997751073" → "" (inválido, sem DDD)
        """
        if not number:
            return ""
        
        # Remover não-dígitos
        clean = re.sub(r'\D', '', number)
        
        # Já tem +55
        if clean.startswith("55") and len(clean) in (12, 13):
            return clean
        
        # Número brasileiro (10-11 dígitos = DDD + número)
        if len(clean) in (10, 11):
            return f"55{clean}"
        
        return ""
    
    @staticmethod
    def validate(number: str) -> tuple[str, bool]:
        """
        Valida e normaliza número brasileiro.
        
        Returns:
            Tuple (normalized_number, is_valid)
        """
        normalized = PhoneNumberValidator.normalize(number)
        
        if not normalized:
            return ("", False)
        
        # Validar formato: 55 + DDD (2) + número (8-9)
        if len(normalized) == 12:
            # Fixo: 55 + DDD + 8 dígitos
            ddd = normalized[2:4]
        elif len(normalized) == 13:
            # Celular: 55 + DDD + 9 + 8 dígitos
            ddd = normalized[2:4]
            numero = normalized[4:]
            if not numero.startswith("9"):
                return ("", False)
        else:
            return ("", False)
        
        # Validar DDD (11-99)
        try:
            ddd_num = int(ddd)
            if not (11 <= ddd_num <= 99):
                return ("", False)
        except ValueError:
            return ("", False)
        
        return (normalized, True)
    
    @staticmethod
    def format_for_speech(number: str) -> str:
        """
        Formata número para TTS (fala natural).
        
        Exemplo: "5518997751073" → "18, 9, 9, 7, 7, 5, 1, 0, 7, 3"
        """
        if not number:
            return ""
        
        # Remover código do país
        clean = number
        if clean.startswith("55"):
            clean = clean[2:]
        
        # Formatar com pausas para TTS
        if len(clean) == 11:
            # Celular: DDD - 9XXXX - XXXX
            return f"{clean[:2]}, {clean[2]}, {', '.join(clean[3:7])}, {', '.join(clean[7:])}"
        elif len(clean) == 10:
            # Fixo: DDD - XXXX - XXXX
            return f"{clean[:2]}, {', '.join(clean[2:6])}, {', '.join(clean[6:])}"
        else:
            return ", ".join(clean)
    
    @staticmethod
    def is_internal_extension(number: str) -> bool:
        """Verifica se é ramal interno (2-5 dígitos)."""
        if not number:
            return False  # String vazia não é ramal
        clean = re.sub(r'\D', '', number)
        return 2 <= len(clean) <= 5
    
    @staticmethod
    def format_for_speech_smart(number: str) -> str:
        """
        Formata número para TTS, detectando automaticamente se é ramal.
        
        Exemplos:
        - "1001" → "ramal 1001"
        - "5518997751073" → "18, 9, 9, 7, 7, 5, 1, 0, 7, 3"
        """
        if not number:
            return ""
        
        if PhoneNumberValidator.is_internal_extension(number):
            # Ramal - falar diretamente sem pausas
            clean = re.sub(r'\D', '', number)
            return f"ramal {clean}"
        
        # Número externo - usar formatação padrão
        return PhoneNumberValidator.format_for_speech(number)


class AcceptCallbackTool(VoiceAITool):
    """
    Tool para quando cliente aceita receber callback.
    
    Uso: Cliente diz "sim, podem me ligar de volta" ou similar.
    
    Este tool inicia o fluxo de callback:
    1. Verifica se caller_id é válido
    2. Se válido, pergunta se quer usar o mesmo número
    3. Se não, a IA deve pedir outro número
    """
    
    name = "accept_callback"
    description = (
        "Cliente ACEITOU receber uma ligação de retorno (callback). "
        "Use quando o cliente concordar com 'podem me ligar', 'prefiro que liguem', etc. "
        "IMPORTANTE: Após chamar esta função, pergunte ao cliente se o número está correto."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "use_current_number": {
                "type": "boolean",
                "description": (
                    "True se o cliente quer usar o mesmo número que está ligando. "
                    "False se o cliente quer fornecer outro número."
                )
            },
            "reason": {
                "type": "string",
                "description": "Motivo do callback - resumo do que o cliente precisa"
            }
        },
        "required": ["use_current_number"]
    }
    
    category = ToolCategory.MESSAGE
    requires_response = True
    filler_phrases = []  # Sem filler - fluxo conversacional
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Processa aceitação de callback."""
        use_current_number = kwargs.get("use_current_number", True)
        reason = kwargs.get("reason", "")
        
        caller_id = context.caller_id
        
        logger.info(
            "📞 [CALLBACK] Cliente aceitou callback",
            extra={
                "call_uuid": context.call_uuid,
                "use_current_number": use_current_number,
                "caller_id": caller_id,
                "reason_length": len(reason) if reason else 0
            }
        )
        
        # Salvar na sessão para uso posterior
        if context._session:
            context._session._callback_reason = reason
            context._session._callback_accepted = True
        
        if use_current_number:
            # Verificar se caller_id é válido
            if PhoneNumberValidator.is_internal_extension(caller_id):
                # Ramal interno - OFERECER OPÇÃO ao cliente
                # O cliente pode querer receber no próprio ramal ou em outro número
                return ToolResult.ok(
                    data={
                        "status": "ask_preference",
                        "action": "ask_callback_preference",
                        "current_number": caller_id,
                        "is_internal": True
                    },
                    instruction=(
                        f"O número atual é o ramal {caller_id}. "
                        f"Pergunte ao cliente: 'Devo retornar a ligação no ramal {caller_id} "
                        f"ou você prefere informar outro número?'"
                    ),
                    should_respond=True
                )
            
            normalized, is_valid = PhoneNumberValidator.validate(caller_id)
            
            if is_valid:
                # Número válido - confirmar com cliente
                formatted = PhoneNumberValidator.format_for_speech(normalized)
                
                # Salvar número na sessão
                if context._session:
                    context._session._callback_number = normalized
                
                return ToolResult.ok(
                    data={
                        "status": "confirm_number",
                        "action": "confirm_phone_number",
                        "number": normalized,
                        "formatted": formatted
                    },
                    instruction=(
                        f"Confirme o número com o cliente. Diga: "
                        f"'Vou anotar para retornarem no número {formatted}. Está correto?'"
                    ),
                    should_respond=True
                )
            else:
                # Número inválido - pedir outro
                return ToolResult.ok(
                    data={
                        "status": "need_number",
                        "action": "ask_phone_number",
                        "reason": "invalid_caller_id"
                    },
                    instruction=(
                        "O número atual não é válido para retorno. "
                        "Pergunte: 'Para qual número posso retornar a ligação? "
                        "Por favor, informe com o DDD.'"
                    ),
                    should_respond=True
                )
        else:
            # Cliente quer usar outro número
            return ToolResult.ok(
                data={
                    "status": "need_number",
                    "action": "ask_phone_number",
                    "reason": "customer_preference"
                },
                instruction=(
                    "Pergunte: 'Qual número devo ligar? "
                    "Por favor, informe com o DDD.'"
                ),
                should_respond=True
            )


class ProvideCallbackNumberTool(VoiceAITool):
    """
    Tool para quando cliente fornece número de callback.
    
    Uso: Cliente diz "18 99775 1073" ou similar.
    """
    
    name = "provide_callback_number"
    description = (
        "Cliente forneceu um número de telefone para callback. "
        "Use quando o cliente disser um número (ex: '18 99775 1073', 'dezoito nove nove...'). "
        "Após validar, peça confirmação."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "phone_number": {
                "type": "string",
                "description": (
                    "Número de telefone fornecido pelo cliente. "
                    "Pode estar em qualquer formato."
                )
            }
        },
        "required": ["phone_number"]
    }
    
    category = ToolCategory.MESSAGE
    requires_response = True
    filler_phrases = []
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Processa número fornecido."""
        phone_number = kwargs.get("phone_number", "")
        
        logger.info(
            "📞 [CALLBACK] Número fornecido",
            extra={
                "call_uuid": context.call_uuid,
                "phone_number": phone_number
            }
        )
        
        # Limpar número (remover "ramal", espaços, etc.)
        clean_number = re.sub(r'[^\d]', '', phone_number)
        
        # Verificar se é um ramal (2-5 dígitos)
        if PhoneNumberValidator.is_internal_extension(clean_number):
            # Aceitar ramal como número de callback válido
            if context._session:
                context._session._callback_number = clean_number
                context._session._callback_is_extension = True
            
            formatted = f"ramal {clean_number}"
            
            return ToolResult.ok(
                data={
                    "status": "confirm_number",
                    "action": "confirm_phone_number",
                    "number": clean_number,
                    "is_extension": True,
                    "formatted": formatted
                },
                instruction=(
                    f"Confirme o ramal. Diga: "
                    f"'Anotei o {formatted}. Está correto?'"
                ),
                should_respond=True
            )
        
        # Validar número externo (10-11 dígitos)
        normalized, is_valid = PhoneNumberValidator.validate(phone_number)
        
        if is_valid:
            formatted = PhoneNumberValidator.format_for_speech(normalized)
            
            # Salvar na sessão
            if context._session:
                context._session._callback_number = normalized
                context._session._callback_is_extension = False
            
            return ToolResult.ok(
                data={
                    "status": "confirm_number",
                    "action": "confirm_phone_number",
                    "number": normalized,
                    "is_extension": False,
                    "formatted": formatted
                },
                instruction=(
                    f"Confirme o número. Diga: "
                    f"'Anotei o número {formatted}. Está correto?'"
                ),
                should_respond=True
            )
        else:
            return ToolResult.ok(
                data={
                    "status": "invalid_number",
                    "action": "ask_again"
                },
                instruction=(
                    "Número inválido. Diga: "
                    "'Desculpe, não consegui entender o número. "
                    "Pode repetir com o DDD, por favor?'"
                ),
                should_respond=True
            )


class UseCurrentExtensionTool(VoiceAITool):
    """
    Tool para quando cliente escolhe usar o ramal/número atual.
    
    Uso: Cliente diz "pode ser no ramal", "no ramal mesmo", "nesse número" ou similar.
    """
    
    name = "use_current_extension"
    description = (
        "Cliente escolheu receber callback no ramal/número atual. "
        "Use quando o cliente disser algo como 'pode ser no ramal', "
        "'no ramal mesmo', 'nesse número', 'pode ser aí', 'no mesmo'."
    )
    
    parameters = {
        "type": "object",
        "properties": {},
        "required": []
    }
    
    category = ToolCategory.MESSAGE
    requires_response = True
    filler_phrases = []
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Processa escolha de usar ramal/número atual."""
        caller_id = context.caller_id
        
        logger.info(
            "📞 [CALLBACK] Cliente escolheu usar número/ramal atual",
            extra={
                "call_uuid": context.call_uuid,
                "caller_id": caller_id
            }
        )
        
        # Verificar se é ramal ou número externo
        is_extension = PhoneNumberValidator.is_internal_extension(caller_id)
        
        # Salvar o ramal/número na sessão
        if context._session:
            context._session._callback_number = caller_id
            context._session._callback_is_extension = is_extension
        
        # Formatar para fala
        if is_extension:
            formatted = f"ramal {caller_id}"
        else:
            normalized, _ = PhoneNumberValidator.validate(caller_id)
            formatted = PhoneNumberValidator.format_for_speech(normalized or caller_id)
        
        return ToolResult.ok(
            data={
                "status": "number_confirmed",
                "action": "ask_schedule",
                "number": caller_id,
                "is_extension": is_extension
            },
            instruction=(
                f"Perfeito! Vamos retornar no {formatted}. "
                f"Agora pergunte: 'Prefere que liguemos assim que possível, ou em um horário específico?'"
            ),
            should_respond=True
        )


class ConfirmCallbackNumberTool(VoiceAITool):
    """
    Tool para quando cliente confirma o número de callback.
    
    Uso: Cliente diz "sim", "correto", "isso" ou similar.
    """
    
    name = "confirm_callback_number"
    description = (
        "Cliente CONFIRMOU ou NEGOU que o número de callback está correto. "
        "Use quando o cliente responder 'sim', 'correto', 'isso' (confirmou=true) "
        "ou 'não', 'errado', 'outro' (confirmou=false)."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "confirmed": {
                "type": "boolean",
                "description": "True se confirmou o número, False se quer corrigir"
            }
        },
        "required": ["confirmed"]
    }
    
    category = ToolCategory.MESSAGE
    requires_response = True
    filler_phrases = []
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Processa confirmação do número."""
        confirmed = kwargs.get("confirmed", True)
        
        logger.info(
            "📞 [CALLBACK] Confirmação de número",
            extra={
                "call_uuid": context.call_uuid,
                "confirmed": confirmed
            }
        )
        
        if confirmed:
            # Número confirmado - perguntar horário
            return ToolResult.ok(
                data={
                    "status": "number_confirmed",
                    "action": "ask_schedule"
                },
                instruction=(
                    "Número confirmado! Agora pergunte sobre o horário: "
                    "'Prefere que liguemos assim que possível, ou em um horário específico?'"
                ),
                should_respond=True
            )
        else:
            # Cliente quer corrigir
            return ToolResult.ok(
                data={
                    "status": "need_correction",
                    "action": "ask_phone_number"
                },
                instruction=(
                    "Peça o número novamente: "
                    "'Sem problemas! Qual é o número correto com DDD?'"
                ),
                should_respond=True
            )


class ScheduleCallbackTool(VoiceAITool):
    """
    Tool para agendar horário preferido do callback.
    
    Uso: Cliente diz "às 14h", "amanhã de manhã", "agora", etc.
    """
    
    name = "schedule_callback"
    description = (
        "Agenda o horário preferido para o callback. "
        "Use quando o cliente mencionar um horário (ex: 'às 14h', 'amanhã', 'agora'). "
        "Se cliente disser 'assim que possível' ou 'agora', use preferred_time='asap'."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "preferred_time": {
                "type": "string",
                "description": (
                    "Horário preferido: 'asap' para imediato, "
                    "ou descrição do horário (ex: 'às 14h', 'amanhã às 10h')"
                )
            }
        },
        "required": ["preferred_time"]
    }
    
    category = ToolCategory.MESSAGE
    requires_response = True
    filler_phrases = ["Anotando..."]
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Processa agendamento e cria o callback."""
        preferred_time = kwargs.get("preferred_time", "asap")
        
        # Obter dados salvos na sessão
        callback_number = None
        callback_reason = None
        
        if context._session:
            callback_number = getattr(context._session, '_callback_number', None)
            callback_reason = getattr(context._session, '_callback_reason', None)
        
        if not callback_number:
            return ToolResult.fail(
                error="Número de callback não encontrado",
                instruction="Houve um problema. Pergunte o número novamente."
            )
        
        logger.info(
            "📞 [CALLBACK] Criando callback",
            extra={
                "call_uuid": context.call_uuid,
                "callback_number": callback_number,
                "preferred_time": preferred_time,
                "reason": callback_reason
            }
        )
        
        # Determinar mensagem de confirmação baseada no horário
        is_asap = preferred_time.lower() in ('asap', 'agora', 'possível', 'já', 'imediato')
        
        if is_asap:
            time_message = "assim que possível"
            scheduled_at = None
        else:
            time_message = preferred_time
            # TODO: Parsear horário para datetime
            scheduled_at = preferred_time
        
        # Enviar webhook para OmniPlay
        webhook_success = False
        ticket_id = None
        
        if context.webhook_url:
            try:
                import aiohttp
                
                # Formatar número para exibição (detecta ramal automaticamente)
                formatted_number = PhoneNumberValidator.format_for_speech_smart(callback_number)
                
                # IMPORTANTE: OmniPlay espera "ticket" não "callback"
                # O formato deve ser compatível com VoiceMessageTicketPayload
                payload = {
                    "event": "voice_ai_callback",
                    "domain_uuid": context.domain_uuid,
                    "call_uuid": context.call_uuid,
                    "caller_id": context.caller_id,
                    "secretary_uuid": context.secretary_uuid,
                    "company_id": context.company_id,
                    # OmniPlay espera o campo "ticket", não "callback"
                    "ticket": {
                        "type": "callback",
                        "callback_number": callback_number,
                        "callback_number_formatted": formatted_number,
                        "preferred_time": preferred_time,
                        "is_asap": is_asap,
                        "scheduled_at": scheduled_at,
                        "message": callback_reason or "",  # Motivo do callback
                        "caller_name": context.caller_name,
                        "caller_phone": context.caller_id,
                        "priority": "normal"
                    }
                }
                
                logger.info(f"📞 [CALLBACK] Enviando para {context.webhook_url}")
                
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        context.webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        resp_text = await resp.text()
                        if resp.status in (200, 201):
                            logger.info(f"📞 [CALLBACK] Callback criado: {resp_text}")
                            webhook_success = True
                            try:
                                import json
                                resp_data = json.loads(resp_text)
                                ticket_id = resp_data.get("ticket_id") or resp_data.get("id")
                            except:
                                pass
                        else:
                            logger.warning(f"📞 [CALLBACK] Webhook retornou {resp.status}: {resp_text}")
                            
            except Exception as e:
                logger.warning(f"📞 [CALLBACK] Erro ao enviar webhook: {e}")
        else:
            logger.warning("📞 [CALLBACK] Nenhum webhook_url configurado")
        
        # Agendar encerramento da chamada
        if context._session:
            import asyncio
            logger.info("📞 [CALLBACK] Agendando encerramento em 10s")
            asyncio.create_task(context._session._delayed_stop(10.0, "callback_scheduled"))
        
        # Formatar número para fala (detecta ramal automaticamente)
        formatted = PhoneNumberValidator.format_for_speech_smart(callback_number)
        
        # Determinar preposição correta: "no ramal X" vs "para o número X"
        if PhoneNumberValidator.is_internal_extension(callback_number):
            numero_phrase = f"no {formatted}"
        else:
            numero_phrase = f"para o número {formatted}"
        
        return ToolResult.ok(
            data={
                "status": "success" if webhook_success else "saved_locally",
                "action": "callback_scheduled",
                "ticket_id": ticket_id,
                "callback_number": callback_number,
                "preferred_time": time_message
            },
            instruction=(
                f"Confirme o callback. Diga: "
                f"'Perfeito! Vamos retornar {numero_phrase} {time_message}. "
                f"Obrigada pela ligação e tenha um ótimo dia!'"
            ),
            should_respond=True,
            side_effects=["callback_scheduled", "call_ending_scheduled"]
        )


# Exportar todas as tools
__all__ = [
    "AcceptCallbackTool",
    "ProvideCallbackNumberTool",
    "UseCurrentExtensionTool",
    "ConfirmCallbackNumberTool",
    "ScheduleCallbackTool",
    "PhoneNumberValidator",
]
