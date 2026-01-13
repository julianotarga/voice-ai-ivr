"""
Azure OpenAI Embeddings Provider.

Uses Azure OpenAI Service for text embeddings.
"""

from typing import List, Optional

from openai import AsyncAzureOpenAI

from .base import BaseEmbeddings, EmbeddingResult


class AzureOpenAIEmbeddings(BaseEmbeddings):
    """
    Azure OpenAI Embeddings provider.
    
    Config:
        api_key: Azure OpenAI API key (required)
        endpoint: Azure OpenAI endpoint URL (required)
        deployment_name: Embeddings deployment name (required)
        api_version: API version (default: 2024-02-15-preview)
    """
    
    provider_name = "azure_openai_embeddings"
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        self._client: Optional[AsyncAzureOpenAI] = None
        
        # Fallback para env vars Azure
        self.api_key = config.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self.endpoint = config.get("endpoint") or config.get("azure_endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self.deployment_name = config.get("deployment_name") or config.get("deployment")
        self.api_version = config.get("api_version", "2024-02-15-preview")
        self.dimensions = config.get("dimensions", 1536)
    
    def _get_client(self) -> AsyncAzureOpenAI:
        """Get or create Azure OpenAI client."""
        if self._client is None:
            self._client = AsyncAzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.endpoint,
                api_version=self.api_version,
            )
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
        
        # Call Azure OpenAI API
        response = await client.embeddings.create(
            model=self.deployment_name,
            input=texts,
        )
        
        # Extract embeddings
        results = []
        total_tokens = response.usage.total_tokens if response.usage else 0
        tokens_per_text = total_tokens // len(texts) if texts else 0
        
        for embedding_data in response.data:
            results.append(EmbeddingResult(
                embedding=embedding_data.embedding,
                dimensions=len(embedding_data.embedding),
                model=self.deployment_name,
                tokens_used=tokens_per_text,
            ))
        
        return results
    
    async def is_available(self) -> bool:
        """Check if Azure OpenAI Embeddings is available."""
        if not self.api_key or not self.endpoint or not self.deployment_name:
            return False
        
        try:
            self._get_client()
            return True
        except Exception:
            return False
    
    def get_dimensions(self) -> int:
        """Get embedding dimensions."""
        return self.dimensions
