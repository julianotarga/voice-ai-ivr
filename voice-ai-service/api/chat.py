"""
Chat API endpoint (LLM with RAG).

⚠️ MULTI-TENANT: domain_uuid é OBRIGATÓRIO em todas as requisições.
"""

import logging
import re

from fastapi import APIRouter, HTTPException, status

from models.request import ChatRequest
from models.response import ChatResponse
from services.provider_manager import provider_manager
from services.database import db
from services.llm.base import Message

router = APIRouter()
logger = logging.getLogger(__name__)


# Default system prompt for the AI secretary
DEFAULT_SYSTEM_PROMPT = """Você é uma secretária virtual chamada {secretary_name}, da empresa {company_name}.

{personality_prompt}

REGRAS IMPORTANTES:
1. Seja sempre educada, simpática e profissional
2. Responda de forma concisa, adequada para voz (máximo 2-3 frases)
3. Use APENAS informações da base de conhecimento fornecida
4. Se não souber a resposta, diga: "Não tenho essa informação, posso transferir você para um atendente?"
5. NUNCA invente informações

AÇÕES ESPECIAIS (use quando apropriado):
- Para transferir: adicione [TRANSFERIR:RAMAL] ao final (ex: [TRANSFERIR:100])
- Para encerrar: adicione [ENCERRAR] ao final

CONTEXTO DA EMPRESA:
{rag_context}
"""


@router.post("/chat", response_model=ChatResponse)
async def chat_with_secretary(request: ChatRequest) -> ChatResponse:
    """
    Chat with the AI secretary.
    
    Args:
        request: ChatRequest with domain_uuid, session_id, secretary_id, user_message
        
    Returns:
        ChatResponse with AI response and action
        
    Raises:
        HTTPException: If chat fails
    """
    # MULTI-TENANT: Validar domain_uuid
    if not request.domain_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="domain_uuid is required for multi-tenant isolation",
        )
    
    try:
        secretary_row = await db.get_secretary_config(
            domain_uuid=request.domain_uuid,
            secretary_uuid=request.secretary_id,
        )
        if not secretary_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Secretaria nao encontrada ou desabilitada para este dominio",
            )

        secretary_config = {
            "name": secretary_row.get("secretary_name") or "Secretaria",
            "company": secretary_row.get("company_name") or "Empresa",
            "personality_prompt": secretary_row.get("personality_prompt") or "",
            "transfer_extension": secretary_row.get("transfer_extension") or "200",
        }
        
        # Get LLM provider from ProviderManager (loads from DB with fallback)
        provider = await provider_manager.get_llm_provider(
            domain_uuid=request.domain_uuid,
            provider_name=request.provider,
        )
        
        # TODO: Get RAG context if enabled
        rag_context = ""
        rag_sources = []
        if request.use_rag:
            # Get embeddings provider and retriever
            # embeddings_provider = await provider_manager.get_embeddings_provider(request.domain_uuid)
            # retriever = Retriever(embeddings_provider)
            # rag_context, rag_sources = await retriever.get_context(
            #     domain_uuid=request.domain_uuid,
            #     query=request.user_message,
            #     secretary_uuid=request.secretary_id,
            # )
            rag_context = "[Base de conhecimento não configurada]"
        
        # Build system prompt
        system_prompt = DEFAULT_SYSTEM_PROMPT.format(
            secretary_name=secretary_config["name"],
            company_name=secretary_config["company"],
            personality_prompt=secretary_config["personality_prompt"],
            rag_context=rag_context,
        )
        
        # Build messages
        messages = [Message(role="system", content=system_prompt)]
        
        # Add conversation history if provided
        if request.conversation_history:
            for msg in request.conversation_history:
                messages.append(Message(role=msg.role, content=msg.content))
        
        # Add user message
        messages.append(Message(role="user", content=request.user_message))
        
        # Generate response
        result = await provider.chat(
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )
        
        # Parse action from response
        action, extension, department = provider.parse_action(result.text)
        
        # Clean response text (remove action markers)
        clean_text = result.text
        clean_text = re.sub(
            r'\[TRANSFER(?:IR)?[:\s]+\d+(?:[,\s]+.+?)?\]', 
            '', 
            clean_text, 
            flags=re.IGNORECASE
        )
        clean_text = re.sub(
            r'\[(?:ENCERRAR|HANGUP|DESLIGAR)\]', 
            '', 
            clean_text, 
            flags=re.IGNORECASE
        )
        
        logger.info(
            f"Chat response for domain {request.domain_uuid} using {provider.provider_name}: "
            f"action={action}, tokens={result.tokens_used}"
        )
        
        return ChatResponse(
            text=clean_text.strip(),
            action=action,
            transfer_extension=extension or secretary_config.get("transfer_extension"),
            transfer_department=department,
            intent=result.intent,
            rag_sources=rag_sources if rag_sources else None,
            provider=provider.provider_name,
            tokens_used=result.tokens_used,
        )
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"Chat failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat failed: {str(e)}",
        )
