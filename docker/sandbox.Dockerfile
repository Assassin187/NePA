FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        make \
        libc6-dev \
        libasan8 \
        libubsan1 \
        mosquitto \
        mosquitto-clients \
    && python -m pip install --no-cache-dir pytest paho-mqtt \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
