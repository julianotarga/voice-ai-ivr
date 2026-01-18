"""
Documents API endpoint (Knowledge Base / RAG).

⚠️ MULTI-TENANT: domain_uuid é OBRIGATÓRIO em todas as requisições.
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from typing import Optional, List
from uuid import UUID, uuid4
from pydantic import BaseModel
import structlog

from models.request import DocumentUploadRequest
from models.response import DocumentUploadResponse
from services.database import db

logger = structlog.get_logger()

router = APIRouter()


class ChunkInfo(BaseModel):
    """Information about a document chunk."""
    chunk_uuid: str
    chunk_index: int
    content: str
    token_count: Optional[int] = None
    similarity_score: Optional[float] = None


class ChunksResponse(BaseModel):
    """Response for document chunks endpoint."""
    document_id: str
    document_name: Optional[str] = None
    chunks: List[ChunkInfo]
    total_chunks: int


@router.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(request: DocumentUploadRequest) -> DocumentUploadResponse:
    """
    Upload a document to the knowledge base.
    
    Args:
        request: DocumentUploadRequest with domain_uuid, document details
        
    Returns:
        DocumentUploadResponse with document ID
        
    Raises:
        HTTPException: If upload fails
    """
    # MULTI-TENANT: Validar domain_uuid
    if not request.domain_uuid:
        raise HTTPException(
            status_code=400,
            detail="domain_uuid is required for multi-tenant isolation",
        )
    
    try:
        if not request.content and not request.file_path:
            raise HTTPException(
                status_code=400,
                detail="content or file_path is required to upload a document",
            )

        # TODO: Implement full document processing (chunking + embeddings)
        # For now, persist metadata and raw content for later processing.
        document_id = uuid4()

        pool = await db.get_pool()
        await pool.execute(
            """
            INSERT INTO v_voice_documents (
                voice_document_uuid,
                domain_uuid,
                voice_secretary_uuid,
                document_name,
                document_type,
                file_path,
                content,
                chunk_count,
                processing_status,
                is_enabled
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, 0, 'pending', true)
            """,
            document_id,
            request.domain_uuid,
            request.secretary_id,
            request.document_name,
            request.document_type,
            request.file_path,
            request.content,
        )

        return DocumentUploadResponse(
            document_id=document_id,
            document_name=request.document_name,
            chunk_count=0,
            status="pending",
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/documents")
async def list_documents(domain_uuid: UUID):
    """
    List documents for a domain.
    
    Args:
        domain_uuid: Domain UUID for multi-tenant isolation
        
    Returns:
        List of documents
    """
    # MULTI-TENANT: Validar domain_uuid
    if not domain_uuid:
        raise HTTPException(
            status_code=400,
            detail="domain_uuid is required for multi-tenant isolation",
        )
    
    try:
        pool = await db.get_pool()
        rows = await pool.fetch(
            """
            SELECT
                voice_document_uuid,
                voice_secretary_uuid,
                document_name,
                document_type,
                file_path,
                file_size,
                mime_type,
                chunk_count,
                processing_status,
                is_enabled,
                insert_date,
                update_date
            FROM v_voice_documents
            WHERE domain_uuid = $1
            ORDER BY insert_date DESC
            """,
            domain_uuid,
        )
        documents = [
            {
                "document_id": str(row["voice_document_uuid"]),
                "secretary_id": str(row["voice_secretary_uuid"]) if row["voice_secretary_uuid"] else None,
                "document_name": row["document_name"],
                "document_type": row["document_type"],
                "file_path": row["file_path"],
                "file_size": row["file_size"],
                "mime_type": row["mime_type"],
                "chunk_count": row["chunk_count"],
                "processing_status": row["processing_status"],
                "is_enabled": row["is_enabled"],
                "insert_date": row["insert_date"],
                "update_date": row["update_date"],
            }
            for row in rows
        ]
        return {"documents": documents, "total": len(documents)}
    except Exception as e:
        logger.error("Failed to list documents", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")


@router.get("/documents/{document_id}/chunks", response_model=ChunksResponse)
async def get_document_chunks(
    document_id: UUID,
    domain_uuid: UUID = Query(..., description="Domain UUID for multi-tenant isolation"),
    limit: int = Query(50, ge=1, le=200, description="Maximum chunks to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Get chunks of a document (for debugging RAG).
    
    Args:
        document_id: Document UUID
        domain_uuid: Domain UUID for multi-tenant isolation
        limit: Maximum number of chunks to return
        offset: Pagination offset
        
    Returns:
        ChunksResponse with chunk details
    """
    # MULTI-TENANT: Validar domain_uuid
    if not domain_uuid:
        raise HTTPException(
            status_code=400,
            detail="domain_uuid is required for multi-tenant isolation",
        )
    
    logger.info(
        "Fetching document chunks",
        document_id=str(document_id),
        domain_uuid=str(domain_uuid),
        limit=limit,
        offset=offset,
    )
    
    try:
        pool = await db.get_pool()
        
        # First, verify document exists and belongs to domain
        document = await pool.fetchrow(
            """
            SELECT voice_document_uuid, document_name, processing_status
            FROM v_voice_documents
            WHERE voice_document_uuid = $1 AND domain_uuid = $2
            """,
            document_id,
            domain_uuid,
        )
        
        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Document {document_id} not found in domain {domain_uuid}",
            )
        
        # Get chunks
        chunks = await pool.fetch(
            """
            SELECT 
                c.chunk_uuid,
                c.chunk_index,
                c.content,
                c.token_count
            FROM v_voice_document_chunks c
            JOIN v_voice_documents d
              ON d.voice_document_uuid = c.voice_document_uuid
            WHERE c.voice_document_uuid = $1
              AND d.domain_uuid = $2
            ORDER BY c.chunk_index
            LIMIT $3 OFFSET $4
            """,
            document_id,
            domain_uuid,
            limit,
            offset,
        )
        
        # Get total count
        total = await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM v_voice_document_chunks c
            JOIN v_voice_documents d
              ON d.voice_document_uuid = c.voice_document_uuid
            WHERE c.voice_document_uuid = $1
              AND d.domain_uuid = $2
            """,
            document_id,
            domain_uuid,
        )
        
        return ChunksResponse(
            document_id=str(document_id),
            document_name=document["document_name"],
            chunks=[
                ChunkInfo(
                    chunk_uuid=str(row["chunk_uuid"]),
                    chunk_index=row["chunk_index"],
                    content=row["content"],
                    token_count=row["token_count"],
                )
                for row in chunks
            ],
            total_chunks=total or 0,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch chunks", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch chunks: {str(e)}")


@router.delete("/documents/{document_id}")
async def delete_document(document_id: UUID, domain_uuid: UUID):
    """
    Delete a document from the knowledge base.
    
    Args:
        document_id: Document UUID
        domain_uuid: Domain UUID for multi-tenant isolation
        
    Returns:
        Success message
    """
    # MULTI-TENANT: Validar domain_uuid
    if not domain_uuid:
        raise HTTPException(
            status_code=400,
            detail="domain_uuid is required for multi-tenant isolation",
        )
    
    logger.info(
        "Deleting document",
        document_id=str(document_id),
        domain_uuid=str(domain_uuid),
    )
    
    try:
        pool = await db.get_pool()
        
        # MULTI-TENANT: SEMPRE verificar domain_uuid antes de deletar
        result = await pool.execute(
            """
            DELETE FROM v_voice_documents
            WHERE voice_document_uuid = $1 AND domain_uuid = $2
            """,
            document_id,
            domain_uuid,
        )
        
        # Chunks são deletados automaticamente via ON DELETE CASCADE
        
        if result == "DELETE 0":
            raise HTTPException(
                status_code=404,
                detail=f"Document {document_id} not found in domain {domain_uuid}",
            )
        
        return {"status": "deleted", "document_id": str(document_id)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete document", error=str(e))
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
