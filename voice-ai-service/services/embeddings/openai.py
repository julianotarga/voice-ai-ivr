"""
OpenAI Embeddings Provider.

Uses OpenAI's text-embedding-3 models for semantic embeddings.
Supports text-embedding-3-small and text-embedding-3-large.
"""

from typing import List, Optional

from openai import AsyncOpenAI

from .base import BaseEmbeddings, EmbeddingResult


class OpenAIEmbeddings(BaseEmbeddings):
    """
    OpenAI Embeddings provider.
    
    Config:
        api_key: OpenAI API key (required)
        model: Embedding model (default: text-embedding-3-small)
        dimensions: Output dimensions (default: depends on model)
    """
    
    provider_name = "openai"
    
    # Default dimensions per model
    MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }
    
    def __init__(self, config: dict):
        super().__init__(config)
        self._client: Optional[AsyncOpenAI] = None
        
        self.model = config.get("model", "text-embedding-3-small")
        self.dimensions = config.get(
            "dimensions", 
            self.MODEL_DIMENSIONS.get(self.model, 1536)
        )
    
    def _get_client(self) -> AsyncOpenAI:
        """Get or create OpenAI client."""
        if self._client is None:
            # Fallback para env var OPENAI_API_KEY (auto-detectado pelo client se None)
            api_key = self.config.get("api_key") or None
            self._client = AsyncOpenAI(api_key=api_key)
        return self._client
    
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
        
        client = self._get_client()
        
        # Call OpenAI API
        response = await client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions if "text-embedding-3" in self.model else None,
        )
        
        # Extract embeddings
        results = []
        total_tokens = response.usage.total_tokens if response.usage else 0
        tokens_per_text = total_tokens // len(texts) if texts else 0
        
        for embedding_data in response.data:
            results.append(EmbeddingResult(
                embedding=embedding_data.embedding,
                dimensions=len(embedding_data.embedding),
                model=self.model,
                tokens_used=tokens_per_text,
            ))
        
        return results
    
    async def is_available(self) -> bool:
        """Check if OpenAI Embeddings is available."""
        if not self.config.get("api_key"):
            return False
        
        try:
            client = self._get_client()
            await client.models.list()
            return True
        except Exception:
            return False
    
    def get_dimensions(self) -> int:
        """Get embedding dimensions."""
        return self.dimensions
