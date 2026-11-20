# python:3.12-slim + ffmpeg + playwright chromium
FROM python:3.12-slim

# 国内构建加速（按构建环境传参，不传则用默认源）：
#   --build-arg DEBIAN_APT_MIRROR=mirrors.cloud.aliyuncs.com        （阿里云 ECS 内网源，最快）
#   --build-arg PIP_INDEX_URL=https://mirrors.cloud.aliyuncs.com/pypi/simple/
#   --build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
ARG PIP_INDEX_URL=""
ARG PLAYWRIGHT_DOWNLOAD_HOST=""
ENV PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONIOENCODING=utf-8

# Debian slim 基础镜像的 apt 源可选换国内镜像（deb.debian.org 国内极慢）
ARG DEBIAN_APT_MIRROR=""
RUN if [ -n "$DEBIAN_APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$DEBIAN_APT_MIRROR|g" \
            /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN PIP_INDEX=${PIP_INDEX_URL} ; \
    if [ -n "$PIP_INDEX" ]; then \
        pip install -i "$PIP_INDEX" -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi \
    && playwright install --with-deps chromium

COPY app ./app
COPY static ./static

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
