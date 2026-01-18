"""
Vector Store for RAG - Embeddings storage and similarity search.

Supports multiple backends:
- pgvector (PostgreSQL extension)
- chromadb (local)
- sqlite-vec (lightweight local)

MULTI-TENANT: All operations require domain_uuid for isolation.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

import numpy as np


@dataclass
class SearchResult:
    """Result from similarity search."""
    chunk_id: str
    document_id: str
    content: str
    score: float
    metadata: Dict[str, Any]


class BaseVectorStore(ABC):
    """Abstract base class for vector stores."""
    
    @abstractmethod
    async def add_embeddings(
        self,
        domain_uuid: str,
        document_id: str,
        chunks: List[str],
        embeddings: List[List[float]],
        metadata: Optional[List[Dict]] = None,
    ) -> List[str]:
        """
        Add embeddings to the store.
        
        Args:
            domain_uuid: Tenant identifier (REQUIRED)
            document_id: Parent document ID
            chunks: List of text chunks
            embeddings: List of embedding vectors
            metadata: Optional metadata for each chunk
            
        Returns:
            List of chunk IDs
        """
        pass
    
    @abstractmethod
    async def search(
        self,
        domain_uuid: str,
        query_embedding: List[float],
        top_k: int = 5,
        filter_document_ids: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        """
        Search for similar chunks.
        
        Args:
            domain_uuid: Tenant identifier (REQUIRED)
            query_embedding: Query vector
            top_k: Number of results to return
            filter_document_ids: Optional filter by document IDs
            
        Returns:
            List of SearchResult ordered by similarity
        """
        pass
    
    @abstractmethod
    async def delete_document(
        self,
        domain_uuid: str,
        document_id: str,
    ) -> int:
        """
        Delete all chunks for a document.
        
        Args:
            domain_uuid: Tenant identifier (REQUIRED)
            document_id: Document to delete
            
        Returns:
            Number of chunks deleted
        """
        pass


class PgVectorStore(BaseVectorStore):
    """
    PostgreSQL with pgvector extension.
    
    Best for production with FusionPBX's existing PostgreSQL.
    Requires: CREATE EXTENSION vector;
    """
    
    def __init__(self, pool):
        """
        Initialize with asyncpg connection pool.
        
        Args:
            pool: asyncpg pool from database.py
        """
        self.pool = pool
        self._pgvector_checked = False

    async def _ensure_pgvector_ready(self) -> None:
        if self._pgvector_checked:
            return
        async with self.pool.acquire() as conn:
            has_ext = await conn.fetchval(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            )
            if not has_ext:
                raise RuntimeError("pgvector extension nao instalada (CREATE EXTENSION vector;)")
            col = await conn.fetchrow(
                """
                SELECT data_type, udt_name
                FROM information_schema.columns
                WHERE table_name = 'v_voice_document_chunks'
                  AND column_name = 'embedding'
                """
            )
            if not col or col["udt_name"] != "vector":
                raise RuntimeError(
                    "v_voice_document_chunks.embedding precisa ser tipo vector para PgVectorStore"
                )
        self._pgvector_checked = True
    
    async def add_embeddings(
        self,
        domain_uuid: str,
        document_id: str,
        chunks: List[str],
        embeddings: List[List[float]],
        metadata: Optional[List[Dict]] = None,
    ) -> List[str]:
        """Add embeddings to PostgreSQL with pgvector."""
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have same length")
        
        metadata = metadata or [{} for _ in chunks]
        chunk_ids = []
        
        await self._ensure_pgvector_ready()

        async with self.pool.acquire() as conn:
            for i, (chunk, embedding, meta) in enumerate(zip(chunks, embeddings, metadata)):
                chunk_id = str(uuid.uuid4())
                chunk_ids.append(chunk_id)
                
                # Store embedding as vector
                embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
                
                await conn.execute(
                    """
                    INSERT INTO v_voice_document_chunks
                    (chunk_uuid, voice_document_uuid, domain_uuid, chunk_index, content, embedding, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6::vector, $7::jsonb)
                    """,
                    chunk_id,
                    document_id,
                    domain_uuid,
                    i,
                    chunk,
                    embedding_str,
                    meta,
                )
        
        return chunk_ids
    
    async def search(
        self,
        domain_uuid: str,
        query_embedding: List[float],
        top_k: int = 5,
        filter_document_ids: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        """Search using cosine similarity in pgvector."""
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        
        await self._ensure_pgvector_ready()

        query = """
            SELECT 
                chunk_uuid,
                voice_document_uuid,
                content,
                1 - (embedding <=> $1::vector) as similarity,
                metadata
            FROM v_voice_document_chunks
            WHERE domain_uuid = $2
        """
        
        params = [embedding_str, domain_uuid]
        
        if filter_document_ids:
            query += " AND voice_document_uuid = ANY($3)"
            params.append(filter_document_ids)
        
        query += " ORDER BY embedding <=> $1::vector LIMIT $" + str(len(params) + 1)
        params.append(top_k)
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        
        return [
            SearchResult(
                chunk_id=str(row["chunk_uuid"]),
                document_id=str(row["voice_document_uuid"]),
                content=row["content"],
                score=float(row["similarity"]),
                metadata=row["metadata"] or {},
            )
            for row in rows
        ]
    
    async def delete_document(
        self,
        domain_uuid: str,
        document_id: str,
    ) -> int:
        """Delete document chunks."""
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM v_voice_document_chunks
                WHERE domain_uuid = $1 AND voice_document_uuid = $2
                """,
                domain_uuid,
                document_id,
            )
            # Parse "DELETE N" to get count
            return int(result.split()[-1])


class ChromaVectorStore(BaseVectorStore):
    """
    ChromaDB vector store.
    
    Good for development and smaller deployments.
    Each domain gets its own collection for isolation.
    """
    
    def __init__(self, persist_directory: str = "./chroma_data"):
        """Initialize ChromaDB."""
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError("chromadb not installed. pip install chromadb")
        
        self.client = chromadb.Client(Settings(
            chroma_db_impl="duckdb+parquet",
            persist_directory=persist_directory,
            anonymized_telemetry=False,
        ))
        self._collections: Dict[str, Any] = {}
    
    def _get_collection(self, domain_uuid: str):
        """Get or create collection for domain."""
        if domain_uuid not in self._collections:
            # Collection name must be valid identifier
            collection_name = f"domain_{domain_uuid.replace('-', '_')}"
            self._collections[domain_uuid] = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"domain_uuid": domain_uuid},
            )
        return self._collections[domain_uuid]
    
    async def add_embeddings(
        self,
        domain_uuid: str,
        document_id: str,
        chunks: List[str],
        embeddings: List[List[float]],
        metadata: Optional[List[Dict]] = None,
    ) -> List[str]:
        """Add embeddings to ChromaDB."""
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        collection = self._get_collection(domain_uuid)
        
        chunk_ids = [str(uuid.uuid4()) for _ in chunks]
        metadatas = metadata or [{} for _ in chunks]
        
        # Add document_id to all metadata
        for meta in metadatas:
            meta["document_id"] = document_id
        
        collection.add(
            ids=chunk_ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        
        return chunk_ids
    
    async def search(
        self,
        domain_uuid: str,
        query_embedding: List[float],
        top_k: int = 5,
        filter_document_ids: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        """Search in ChromaDB."""
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        collection = self._get_collection(domain_uuid)
        
        where_filter = None
        if filter_document_ids:
            where_filter = {"document_id": {"$in": filter_document_ids}}
        
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
        
        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                search_results.append(SearchResult(
                    chunk_id=chunk_id,
                    document_id=results["metadatas"][0][i].get("document_id", ""),
                    content=results["documents"][0][i],
                    score=1.0 - results["distances"][0][i],  # Convert distance to similarity
                    metadata=results["metadatas"][0][i],
                ))
        
        return search_results
    
    async def delete_document(
        self,
        domain_uuid: str,
        document_id: str,
    ) -> int:
        """Delete document from ChromaDB."""
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        collection = self._get_collection(domain_uuid)
        
        # Get all chunks for document
        results = collection.get(
            where={"document_id": document_id},
            include=[],
        )
        
        if results["ids"]:
            collection.delete(ids=results["ids"])
            return len(results["ids"])
        
        return 0


class InMemoryVectorStore(BaseVectorStore):
    """
    Simple in-memory vector store for testing.
    Data is lost when service restarts.
    """
    
    def __init__(self):
        # domain_uuid -> list of (chunk_id, document_id, content, embedding, metadata)
        self._data: Dict[str, List[tuple]] = {}
    
    async def add_embeddings(
        self,
        domain_uuid: str,
        document_id: str,
        chunks: List[str],
        embeddings: List[List[float]],
        metadata: Optional[List[Dict]] = None,
    ) -> List[str]:
        if not domain_uuid:
            raise ValueError("domain_uuid is required")
        
        if domain_uuid not in self._data:
            self._data[domain_uuid] = []
        
        metadata = metadata or [{} for _ in chunks]
        chunk_ids = []
        
        for chunk, embedding, meta in zip(chunks, embeddings, metadata):
            chunk_id = str(uuid.uuid4())
            chunk_ids.append(chunk_id)
            self._data[domain_uuid].append((
                chunk_id,
                document_id,
                chunk,
                np.array(embedding),
                meta,
            ))
        
        return chunk_ids
    
    async def search(
        self,
        domain_uuid: str,
        query_embedding: List[float],
        top_k: int = 5,
        filter_document_ids: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        if not domain_uuid:
            raise ValueError("domain_uuid is required")
        
        if domain_uuid not in self._data:
            return []
        
        query_vec = np.array(query_embedding)
        results = []
        
        for chunk_id, doc_id, content, embedding, meta in self._data[domain_uuid]:
            if filter_document_ids and doc_id not in filter_document_ids:
                continue
            
            # Cosine similarity
            similarity = np.dot(query_vec, embedding) / (
                np.linalg.norm(query_vec) * np.linalg.norm(embedding)
            )
            
            results.append(SearchResult(
                chunk_id=chunk_id,
                document_id=doc_id,
                content=content,
                score=float(similarity),
                metadata=meta,
            ))
        
        # Sort by score descending
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]
    
    async def delete_document(
        self,
        domain_uuid: str,
        document_id: str,
    ) -> int:
        if not domain_uuid or domain_uuid not in self._data:
            return 0
        
        original_len = len(self._data[domain_uuid])
        self._data[domain_uuid] = [
            item for item in self._data[domain_uuid]
            if item[1] != document_id
        ]
        return original_len - len(self._data[domain_uuid])


def create_vector_store(backend: str = "pgvector", **kwargs) -> BaseVectorStore:
    """
    Factory to create vector store.
    
    Args:
        backend: "pgvector", "chromadb", or "memory"
        **kwargs: Backend-specific arguments
        
    Returns:
        Configured vector store
    """
    if backend == "pgvector":
        if "pool" not in kwargs:
            raise ValueError("pgvector requires 'pool' argument")
        return PgVectorStore(kwargs["pool"])
    
    elif backend == "chromadb":
        persist_dir = kwargs.get("persist_directory", "./chroma_data")
        return ChromaVectorStore(persist_dir)
    
    elif backend == "memory":
        return InMemoryVectorStore()
    
    else:
        raise ValueError(f"Unknown backend: {backend}")
