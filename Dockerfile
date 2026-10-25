# AutoShorts for Linux and Windows (Docker Desktop / WSL2). On a Mac, use the native app: it runs on Apple silicon (MLX),
# which containers can't reach.
#
#   docker compose up -d      then open http://localhost:3000
#
# The LLM runs in the Ollama container next to this one (see docker-compose.yml).

# ---- dashboard
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- engine
FROM python:3.12-slim

# ffmpeg for media; libegl/libgl/fontconfig for the skia text renderer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libegl1 libgl1 libfontconfig1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Extra feature sets to install: "mcp" by default; "all" adds the faceless studio (local voices + image generation).
ARG EXTRAS=mcp
# CPU PyTorch keeps the image small. For an NVIDIA GPU build: --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

WORKDIR /app
COPY backend/ ./backend/
RUN pip install --no-cache-dir --extra-index-url "$TORCH_INDEX" "./backend[$EXTRAS]"
COPY --from=web /web/dist ./web

ENV AUTOSHORTS_HOME=/data \
    AUTOSHORTS_HOST=0.0.0.0 \
    AUTOSHORTS_PORT=3000 \
    AUTOSHORTS_FRONTEND=/app/web \
    AUTOSHORTS_LLM_BACKEND=server \
    AUTOSHORTS_LLM_URL=http://ollama:11434/v1 \
    HF_HOME=/data/models/hf \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/api/ping')"
CMD ["autoshorts", "up"]
