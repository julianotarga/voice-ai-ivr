"""
OmniPlay Webhook Service

Envia webhooks para o OmniPlay quando conversas do Voice AI terminam.

References:
- backend/src/controllers/VoiceAIWebhookController.ts
- backend/src/services/VoiceAIServices/CreateVoiceAITicketService.ts
"""

import aiohttp
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
import structlog

logger = structlog.get_logger(__name__)


class OmniPlayWebhookService:
    """Service for sending webhooks to OmniPlay."""
    
    def __init__(self, webhook_url: Optional[str] = None, timeout: int = 30):
        """
        Initialize the webhook service.
        
        Args:
            webhook_url: Base URL for OmniPlay webhook
            timeout: Request timeout in seconds
        """
        self.webhook_url = webhook_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session
    
    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def send_conversation_ended(
        self,
        *,
        secretary_uuid: str,
        secretary_name: str,
        call_uuid: str,
        caller_id: str,
        caller_name: Optional[str] = None,
        duration_seconds: int,
        start_time: datetime,
        end_time: datetime,
        messages: List[Dict[str, str]],
        summary: Optional[str] = None,
        action_type: str = "hangup",
        action_destination: Optional[str] = None,
        action_reason: Optional[str] = None,
        domain_uuid: str,
        company_id: int,
        processing_mode: str = "turn_based"
    ) -> Dict[str, Any]:
        """
        Send conversation_ended webhook to OmniPlay.
        
        Returns:
            Response from OmniPlay including ticketId and contactId
        """
        if not self.webhook_url:
            logger.warning("webhook_url_not_configured", 
                          secretary_uuid=secretary_uuid)
            return {"success": False, "error": "webhook_url not configured"}
        
        payload = {
            "event": "conversation_ended",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "secretary": {
                "uuid": secretary_uuid,
                "name": secretary_name
            },
            "call": {
                "uuid": call_uuid,
                "caller_id": caller_id,
                "caller_name": caller_name,
                "duration_seconds": duration_seconds,
                "start_time": start_time.isoformat() + "Z",
                "end_time": end_time.isoformat() + "Z"
            },
            "conversation": {
                "total_turns": len(messages),
                "messages": messages,
                "summary": summary
            },
            "action": {
                "type": action_type,
                "destination": action_destination,
                "reason": action_reason
            },
            "metadata": {
                "domain_uuid": domain_uuid,
                "company_id": company_id,
                "processing_mode": processing_mode
            }
        }
        
        logger.info("sending_webhook", 
                   webhook_url=self.webhook_url,
                   caller_id=caller_id,
                   action_type=action_type)
        
        try:
            session = await self._get_session()
            
            async with session.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                response_data = await response.json()
                
                if response.status == 200 and response_data.get("success"):
                    logger.info("webhook_sent_successfully",
                               ticket_id=response_data.get("ticketId"),
                               contact_id=response_data.get("contactId"))
                    return response_data
                else:
                    logger.error("webhook_failed",
                                status=response.status,
                                response=response_data)
                    return {
                        "success": False,
                        "error": response_data.get("error", "Unknown error"),
                        "status": response.status
                    }
                    
        except asyncio.TimeoutError:
            logger.error("webhook_timeout", webhook_url=self.webhook_url)
            return {"success": False, "error": "timeout"}
        except aiohttp.ClientError as e:
            logger.error("webhook_client_error", error=str(e))
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("webhook_unexpected_error", error=str(e))
            return {"success": False, "error": str(e)}
    
    async def send_transfer_requested(
        self,
        *,
        secretary_uuid: str,
        secretary_name: str,
        call_uuid: str,
        caller_id: str,
        destination: str,
        reason: Optional[str] = None,
        domain_uuid: str,
        company_id: int
    ) -> Dict[str, Any]:
        """
        Send transfer_requested event (optional, for logging).
        """
        if not self.webhook_url:
            return {"success": False, "error": "webhook_url not configured"}
        
        payload = {
            "event": "transfer_requested",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "secretary": {
                "uuid": secretary_uuid,
                "name": secretary_name
            },
            "call": {
                "uuid": call_uuid,
                "caller_id": caller_id
            },
            "action": {
                "type": "transfer",
                "destination": destination,
                "reason": reason
            },
            "metadata": {
                "domain_uuid": domain_uuid,
                "company_id": company_id
            }
        }
        
        try:
            session = await self._get_session()
            
            async with session.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                return await response.json()
                
        except Exception as e:
            logger.error("transfer_webhook_failed", error=str(e))
            return {"success": False, "error": str(e)}


# Singleton instance
_webhook_service: Optional[OmniPlayWebhookService] = None


def get_webhook_service(webhook_url: Optional[str] = None) -> OmniPlayWebhookService:
    """
    Get or create webhook service singleton.
    
    Args:
        webhook_url: Optional URL to configure. Only used on first call.
    """
    global _webhook_service
    
    if _webhook_service is None:
        _webhook_service = OmniPlayWebhookService(webhook_url)
    elif webhook_url and _webhook_service.webhook_url != webhook_url:
        # Update URL if different
        _webhook_service.webhook_url = webhook_url
    
    return _webhook_service


async def send_omniplay_webhook(
    webhook_url: str,
    secretary_uuid: str,
    secretary_name: str,
    call_uuid: str,
    caller_id: str,
    messages: List[Dict[str, str]],
    duration_seconds: int,
    domain_uuid: str,
    company_id: int,
    action_type: str = "hangup",
    action_destination: Optional[str] = None,
    processing_mode: str = "turn_based",
    summary: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to send webhook.
    
    This is the main entry point for sending webhooks from the Voice AI.
    
    Example:
        result = await send_omniplay_webhook(
            webhook_url="https://omniplay.example.com/api/voice-ai/webhook",
            secretary_uuid="abc-123",
            secretary_name="Atendimento",
            call_uuid="call-456",
            caller_id="+5511999998888",
            messages=[
                {"role": "assistant", "content": "Olá!"},
                {"role": "user", "content": "Oi, preciso de ajuda"}
            ],
            duration_seconds=120,
            domain_uuid="domain-789",
            company_id=1,
            action_type="transfer",
            action_destination="200"
        )
    """
    service = get_webhook_service(webhook_url)
    
    return await service.send_conversation_ended(
        secretary_uuid=secretary_uuid,
        secretary_name=secretary_name,
        call_uuid=call_uuid,
        caller_id=caller_id,
        duration_seconds=duration_seconds,
        start_time=datetime.utcnow(),  # Would be passed from actual call
        end_time=datetime.utcnow(),
        messages=messages,
        summary=summary,
        action_type=action_type,
        action_destination=action_destination,
        domain_uuid=domain_uuid,
        company_id=company_id,
        processing_mode=processing_mode
    )
