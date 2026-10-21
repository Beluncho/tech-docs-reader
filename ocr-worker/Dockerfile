FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG PADDLE_MODEL_SOURCE=BOS

ENV LANG=ru_RU.UTF-8 \
    LANGUAGE=ru_RU:ru \
    LC_ALL=ru_RU.UTF-8 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_PDX_CACHE_HOME=/opt/paddlex-cache \
    PADDLE_MODEL_ROOT=/opt/paddlex-cache/official_models \
    PADDLE_PDX_MODEL_SOURCE=${PADDLE_MODEL_SOURCE} \
    XDG_CACHE_HOME=/tmp/.cache \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0
# ^ MKLDNN (OneDNN) требует AVX2 и включён в Paddle 3.x по умолчанию — на CPU без
#   AVX2 (i3-2100) инференс падал. Отключаем: работает на любом x86, медленнее
#   (в 2-4 раза). На сервере с AVX2 можно вернуть "1" через environment в compose.

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        locales \
        python3 \
        python3-pip \
        python3-venv \
    && sed -i 's/^# *\(ru_RU.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen ru_RU.UTF-8 \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip==25.1.1 \
    && groupadd --system --gid 10001 ocr \
    && useradd --system --uid 10001 --gid ocr --home-dir /home/ocr --create-home ocr \
    && mkdir -p /service /opt/paddlex-cache \
    && chown -R ocr:ocr /opt/paddlex-cache \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service

COPY requirements-paddle-cpu.txt requirements.txt ./
# --resume-retries: 189-МБ wheel paddlepaddle с cdn.bcebos.com рвётся на медленном
# канале — докачиваем вместо полного рестарта загрузки
RUN python -m pip install --no-cache-dir --resume-retries 10 \
        --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/ \
        -r requirements-paddle-cpu.txt \
    && python -m pip install --no-cache-dir --resume-retries 10 -r requirements.txt \
    && python -m pip check

COPY --chown=ocr:ocr app ./app
COPY --chown=ocr:ocr scripts ./scripts

USER ocr
RUN true
# RUN python -m scripts.prefetch_models  # DEBUG: отложено для интерактивной отладки

USER root
RUN chown -R root:root /opt/paddlex-cache \
    && chmod -R a-w /opt/paddlex-cache

USER ocr

EXPOSE 8300

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('SERVICE_PORT', '8300') + '/health', timeout=3).read()"

CMD ["python", "-m", "app"]
