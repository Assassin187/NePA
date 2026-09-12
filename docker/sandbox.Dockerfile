FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        make \
        libc6-dev \
        libasan8 \
        libubsan1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
