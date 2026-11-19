# LiveClipper Backend Dockerfile (Optimized)
# Multi-stage build: ~5GB → ~1.5-2GB estimated

# ============================================
# Stage 1: BUILD — compile Python C-extensions
# ============================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Only build-essential needed here (never ships to final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

# Install CPU-only PyTorch first (prevents 2GB+ CUDA wheel download)
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch torchvision

# Install remaining dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ============================================
# Stage 2: RUNTIME — lean production image
# ============================================
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    FFMPEG_THREADS=2

# Runtime-only system deps (no build-essential, no curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fontconfig \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-color-emoji \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy all installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

# Register fonts + create data dirs in a single layer
RUN mkdir -p /usr/local/share/fonts/liveclipper /app/clips /app/shorts /app/logs /app/data /app/.buffer && \
    cp /app/app/video/fonts/*.ttf /usr/local/share/fonts/liveclipper/ 2>/dev/null || true && \
    cp /app/app/video/fonts/openshorts-fontmap.conf /etc/fonts/conf.d/60-liveclipper.conf 2>/dev/null || true && \
    fc-cache -f

EXPOSE 8000

# Healthcheck using Python (no curl needed = smaller image)
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]