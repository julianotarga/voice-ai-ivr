"""
Voyage AI Embeddings Provider.

Uses Voyage AI for high-quality text embeddings.
Excellent for semantic search and RAG applications.
"""

from typing import List, Optional

import httpx

from .base import BaseEmbeddings, EmbeddingResult


class VoyageAIEmbeddings(BaseEmbeddings):
    """
    Voyage AI Embeddings provider.
    
    Config:
        api_key: Voyage AI API key (required)
        model: Model name (default: voyage-multilingual-2)
        input_type: Input type (document, query)
    """
    
    provider_name = "voyage"
    
    BASE_URL = "https://api.voyageai.com/v1"
    
    # Model dimensions
    MODEL_DIMENSIONS = {
        "voyage-3": 1024,
        "voyage-3-lite": 512,
        "voyage-multilingual-2": 1024,
        "voyage-code-2": 1536,
        "voyage-finance-2": 1024,
        "voyage-law-2": 1024,
    }
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env var VOYAGE_API_KEY
        self.api_key = config.get("api_key") or os.environ.get("VOYAGE_API_KEY", "")
        self.model = config.get("model", "voyage-multilingual-2")
        self.input_type = config.get("input_type", "document")
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
            raise ValueError("Voyage AI API key is required")
        
        # Call Voyage API
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.BASE_URL}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "input": texts,
                    "model": self.model,
                    "input_type": self.input_type,
                },
            )
            response.raise_for_status()
            data = response.json()
        
        # Extract embeddings
        embeddings_data = data.get("data", [])
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        tokens_per_text = total_tokens // len(texts) if texts else 0
        
        results = []
        for emb_data in embeddings_data:
            embedding = emb_data.get("embedding", [])
            results.append(EmbeddingResult(
                embedding=embedding,
                dimensions=len(embedding),
                model=self.model,
                tokens_used=tokens_per_text,
            ))
        
        return results
    
    async def is_available(self) -> bool:
        """Check if Voyage AI is available."""
        if not self.api_key:
            return False
        
        # Voyage doesn't have a simple ping endpoint, assume available if key exists
        return True
    
    def get_dimensions(self) -> int:
        """Get embedding dimensions."""
        return self.dimensions
