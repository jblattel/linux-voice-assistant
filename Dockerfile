ARG LVA_IMAGE=ghcr.io/ohf-voice/linux-voice-assistant
ARG LVA_IMAGE_TAG=latest
FROM ${LVA_IMAGE}:${LVA_IMAGE_TAG}

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends mpg123 && \
    rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --break-system-packages wyoming
