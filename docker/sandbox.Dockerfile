# NePA 沙箱镜像（设计文档 8.5，交付项 M0-8）
#
# 构建：   docker build -t nepa-sandbox:latest -f docker/sandbox.Dockerfile docker
# digest： docker image inspect nepa-sandbox:latest --format '{{.Id}}'
#          （M0-8 要求构建后记录 digest；9.3 要求按 digest 固定并记入配置快照）
# 自验：   docker run --rm --network=none nepa-sandbox:latest sh -c \
#            'gcc --version && make --version && python3 -m pytest --version \
#             && mosquitto -h | head -1 && mosquitto_pub --help | head -1 \
#             && python3 -c "import paho.mqtt; print(paho.mqtt.__version__)" && id -u'
#          （最后 id -u 应输出非 0，验证非 root）
#
# 用途：生成代码（不可信）的构建与测试全部在本镜像容器内执行，默认 --network=none（8.5）。
# 注意：pytest 经 Debian 包安装，统一以 `python3 -m pytest` 调用（不依赖 /usr/bin/pytest）。

FROM debian:bookworm-slim

# gcc + libc6-dev + make：7.4 构建契约（-std=c99 -Werror；SAN=1 所需的
#   libasan/libubsan 随 gcc 的硬依赖 libgcc-*-dev 一并安装）
# python3 + python3-pytest：gold 测试 harness（5.3）
# mosquitto + mosquitto-clients + python3-paho-mqtt：仅供测试夹具自验与 L3 互操作（8.5）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        make \
        python3 \
        python3-pytest \
        python3-paho-mqtt \
        mosquitto \
        mosquitto-clients \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户执行不可信代码（8.5）
RUN useradd --create-home --shell /bin/bash --uid 1000 nepa
USER nepa

# workspace 挂载点（8.5：-v <宿主 workspace>:/w -w /w）
WORKDIR /w
