"""
Cohere Embeddings Provider.

Uses Cohere Embed API for high-quality multilingual embeddings.
"""

from typing import List, Optional

import httpx

from .base import BaseEmbeddings, EmbeddingResult


class CohereEmbeddings(BaseEmbeddings):
    """
    Cohere Embeddings provider.
    
    Config:
        api_key: Cohere API key (required)
        model: Model name (default: embed-multilingual-v3.0)
        input_type: Input type (search_document, search_query, classification, clustering)
    """
    
    provider_name = "cohere"
    
    BASE_URL = "https://api.cohere.ai/v1"
    
    # Model dimensions
    MODEL_DIMENSIONS = {
        "embed-multilingual-v3.0": 1024,
        "embed-english-v3.0": 1024,
        "embed-multilingual-light-v3.0": 384,
        "embed-english-light-v3.0": 384,
    }
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env var COHERE_API_KEY
        self.api_key = config.get("api_key") or os.environ.get("COHERE_API_KEY", "")
        self.model = config.get("model", "embed-multilingual-v3.0")
        self.input_type = config.get("input_type", "search_document")
        self.dimensions = self.MODEL_DIMENSIONS.get(self.model, 1024)
    
    async def embed(self, text: str) -> EmbeddingResult:
        """
        Generate embedding for a single text.
        
        Args:
            text: Text to embed
            
        Returns:
            EmbeddingResult with embedding vector
        """
        results = await self.embed_batch([text])
        return results[0]
    
    async def embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of EmbeddingResult
        """
        if not texts:
            return []
        
        if not self.api_key:
            raise ValueError("Cohere API key is required")
        
        # Call Cohere API
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.BASE_URL}/embed",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "texts": texts,
                    "model": self.model,
                    "input_type": self.input_type,
                    "truncate": "END",
                },
            )
            response.raise_for_status()
            data = response.json()
        
        # Extract embeddings
        embeddings = data.get("embeddings", [])
        meta = data.get("meta", {})
        billed_units = meta.get("billed_units", {})
        tokens_used = billed_units.get("input_tokens", 0)
        tokens_per_text = tokens_used // len(texts) if texts else 0
        
        results = []
        for embedding in embeddings:
            results.append(EmbeddingResult(
                embedding=embedding,
                dimensions=len(embedding),
                model=self.model,
                tokens_used=tokens_per_text,
            ))
        
        return results
    
    async def is_available(self) -> bool:
        """Check if Cohere is available."""
        if not self.api_key:
            return False
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.BASE_URL}/check-api-key",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return response.status_code == 200
        except Exception:
            return False
    
    def get_dimensions(self) -> int:
        """Get embedding dimensions."""
        return self.dimensions
