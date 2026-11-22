# syntax=docker/dockerfile:1.7
# LiveClipper Backend Dockerfile — fully optimized
# Multi-stage: build deps isolated, BuildKit cache mounts, non-root runtime, pruned site-packages.

ARG PYTHON_VERSION=3.11

# =============================================================
# Stage 1: BUILDER — compile wheels into an isolated prefix
# =============================================================
FROM python:${PYTHON_VERSION}-slim AS builder

# Re-declare ARG so ${PYTHON_VERSION} is usable INSIDE this stage.
ARG PYTHON_VERSION

# CRITICAL: PYTHONPATH must point at the prefix so the SECOND pip install
# sees CPU-torch as already-satisfied and does not re-resolve it against
# PyPI (which would pull the ~2 GB GPU wheel + full CUDA toolchain).
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_COMPILE=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=5 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/install/lib/python3.11/site-packages

WORKDIR /build

# Build-only system deps (never ship to final image)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install everything into /install so the runtime image can copy a single tree.
# CPU-only PyTorch first — prevents ~2 GB CUDA wheels from being pulled.
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p "${PYTHONPATH}" && \
    pip install --prefix=/install \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision && \
    echo ">>> VERIFY: CPU torch installed to ${PYTHONPATH}" && \
    python -c "import sys; sys.path.insert(0, '${PYTHONPATH}'); \
import torch; print('torch', torch.__version__, 'cuda?', torch.cuda.is_available())"

# App dependencies. PYTHONPATH (set above) makes pip see the CPU torch from
# the previous layer, so `torch>=2.0.0,<3.0.0` is already satisfied here.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    echo ">>> pip sees:" && pip list --path "${PYTHONPATH}" | grep -iE 'torch|vision' && \
    pip install --prefix=/install -r requirements.txt && \
    echo ">>> VERIFY: no CUDA wheels got pulled" && \
    if ls "${PYTHONPATH}" | grep -qiE 'nvidia_|cuda_toolkit|triton'; then \
        echo "FAIL: CUDA deps leaked into /install:" && ls "${PYTHONPATH}" | grep -iE 'nvidia_|cuda_|triton' && exit 1; \
    fi

# Prune: strip bytecode caches, tests, docs, type stubs we don't need at runtime.
# Saves hundreds of MB on torch / mediapipe / faster-whisper.
RUN set -eux; \
    find /install -depth \
        \( -type d -a \( -name __pycache__ -o -name tests -o -name test \
                       -o -name testing -o -name examples -o -name docs \) \) \
        -exec rm -rf '{}' + ; \
    find /install -type f \( -name '*.pyc' -o -name '*.pyo' \
                           -o -name '*.pyi' -o -name '*.md' \
                           -o -name '*.txt' -o -name '*.rst' \) -delete ; \
    # Strip debug symbols from shared libraries
    find /install -type f -name '*.so*' -exec strip --strip-unneeded {} + 2>/dev/null || true


# =============================================================
# Stage 2: RUNTIME — lean production image
# =============================================================
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FFMPEG_THREADS=2 \
    PATH=/usr/local/bin:$PATH

# Runtime-only system deps. Noto core is heavy but required for subtitle rendering
# across scripts; emoji font is required for emoji in hook banners.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-liberation \
        fonts-noto-core \
        fonts-noto-color-emoji \
        libgl1 \
        libglib2.0-0 \
        nodejs \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Pull the pre-built Python stack from the builder stage.
COPY --from=builder /install /usr/local

# Create non-root user and all writable data dirs up front, owned by it.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid ${APP_GID} app \
 && useradd  --system --uid ${APP_UID} --gid app --home /app --shell /sbin/nologin app \
 && mkdir -p /app /app/clips /app/shorts /app/imports /app/logs /app/data /app/.buffer \
 && chown -R app:app /app

WORKDIR /app

# ---- Fonts: copy and register BEFORE the main source copy so edits to app code
#      don't bust the font-cache layer. ----
COPY --chown=app:app app/video/fonts/ /tmp/fonts/
RUN mkdir -p /usr/local/share/fonts/liveclipper \
 && cp /tmp/fonts/*.ttf /usr/local/share/fonts/liveclipper/ \
 && if [ -f /tmp/fonts/openshorts-fontmap.conf ]; then \
        cp /tmp/fonts/openshorts-fontmap.conf /etc/fonts/conf.d/60-liveclipper.conf ; \
    fi \
 && fc-cache -f \
 && rm -rf /tmp/fonts

# Application source — last so unrelated edits don't invalidate earlier layers.
COPY --chown=app:app . .

USER app

EXPOSE 8000

# Python-based healthcheck with an explicit socket timeout so a hung backend
# can't outlive the --timeout boundary.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys;\
import socket; socket.setdefaulttimeout(3);\
urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()" || exit 1

# tini reaps zombies (ffmpeg/whisper subprocesses) and forwards signals cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]
