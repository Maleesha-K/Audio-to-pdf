# Notebook+ Annotator - Cloud Run Container
FROM python:3.12-slim-bookworm

# System dependencies for PDF parsing, image processing, OCR, and MP4 video export
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download Whisper and FastEmbed models so Cloud Run never hits Hugging Face at runtime
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', download_root='/app/data/models')" && \
    python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/data/models')"

# Copy source code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/

# Copy pre-packaged flagship demo lesson into image
COPY data/sample-optimization-template/ ./data/lessons/sample-optimization/
COPY data/sample-optimization-template/ ./data/sample-optimization-template/

# Environment defaults
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
