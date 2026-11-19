# LiveClipper Backend Dockerfile
# Single container running the LiveClipper backend API and video processing pipeline.
# Base: python:3.11-slim for compatibility with torch, faster-whisper, and mediapipe.

FROM python:3.11-slim

WORKDIR /app

# Ensure Python unbuffered logging, bytecode suppression, and module import path
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    FFMPEG_THREADS=2

# Install system dependencies:
# - ffmpeg (built with libass support for animated karaoke subtitles)
# - fontconfig and fonts for subtitle rendering and text overlays
# - libgl1 and libglib2.0-0 for OpenCV / MediaPipe
# - build-essential and curl for package builds and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fontconfig \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-color-emoji \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install CPU version of PyTorch first to prevent downloading large CUDA wheels
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision

# Copy requirements and install all Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application source code
COPY . .

# Register bundled fonts with fontconfig so libass resolves Impact/Anton and custom fonts
RUN mkdir -p /usr/local/share/fonts/liveclipper && \
    cp /app/app/video/fonts/*.ttf /usr/local/share/fonts/liveclipper/ 2>/dev/null || true && \
    cp /app/app/video/fonts/openshorts-fontmap.conf /etc/fonts/conf.d/60-liveclipper.conf 2>/dev/null || true && \
    fc-cache -f

# Create data directories with appropriate permissions
RUN mkdir -p /app/clips /app/shorts /app/logs /app/data /app/.buffer

# Expose FastAPI server port
EXPOSE 8000

# Health check probe against API health endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -sf http://localhost:8000/api/health || exit 1

# Default command starts the FastAPI server
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]
