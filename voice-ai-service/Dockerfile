# =============================================================================
# Voice AI Service - Dockerfile
# =============================================================================
# Multi-stage build for optimized production image
#
# Build:   docker build -t voice-ai-service .
# Run:     docker run -p 8100:8100 --env-file .env voice-ai-service
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder - Install dependencies
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: Production - Minimal runtime image
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS production

LABEL maintainer="OmniPlay <contato@omniplay.com.br>"
LABEL description="Voice AI Service - Secretária Virtual com IA"
LABEL version="1.0.0"

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Audio processing
    ffmpeg \
    libsndfile1 \
    # For health checks
    curl \
    # Cleanup
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user for security
RUN groupadd -r voiceai && useradd -r -g voiceai voiceai

# Create directories
RUN mkdir -p /app/data /app/logs /app/temp && \
    chown -R voiceai:voiceai /app

# Copy application code
COPY --chown=voiceai:voiceai . .

# Switch to non-root user
USER voiceai

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOST=0.0.0.0 \
    PORT=8100 \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs \
    TEMP_DIR=/app/temp

# Expose port
EXPOSE 8100

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]

# -----------------------------------------------------------------------------
# Stage 3: Development - With dev tools
# -----------------------------------------------------------------------------
FROM production AS development

USER root

# Install dev dependencies
RUN pip install --no-cache-dir \
    pytest \
    pytest-asyncio \
    pytest-cov \
    black \
    ruff \
    mypy \
    ipdb

# Enable hot reload
ENV DEBUG=true

USER voiceai

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100", "--reload"]
