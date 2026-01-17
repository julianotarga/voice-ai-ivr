"""
Health Check para o container voice-ai-realtime.

Usado pelo Docker healthcheck para verificar se o serviço está funcionando.

Uso:
    python -m realtime.health_check
    
Exit codes:
    0 - Healthy
    1 - Unhealthy
"""

import os
import sys
import asyncio
import aiohttp

# Porta do servidor HTTP API (não WebSocket!)
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8100"))
HEALTH_URL = f"http://localhost:{HEALTH_PORT}/health"


async def check_health() -> bool:
    """
    Verifica saúde do serviço via HTTP API.
    
    IMPORTANTE: 
    - NÃO tenta conectar na porta WebSocket (8085)
    - Usa porta HTTP API (8100) que responde JSON
    """
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(HEALTH_URL) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("status") == "healthy":
                        return True
                return False
    except Exception as e:
        print(f"Health check failed: {e}", file=sys.stderr)
        return False


def main():
    """Entry point para Docker healthcheck."""
    is_healthy = asyncio.run(check_health())
    
    if is_healthy:
        print("OK")
        sys.exit(0)
    else:
        print("UNHEALTHY")
        sys.exit(1)


if __name__ == "__main__":
    main()
