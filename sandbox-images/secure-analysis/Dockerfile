# 构建调用方必须传入不可变的上游镜像引用，例如：
# python:3.12-slim@sha256:<64 lowercase hex>。不提供标签回退，避免构建漂移。
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
COPY requirements.lock /tmp/requirements.lock
# 安装仅发生在镜像构建期；运行时镜像不含包管理器入口或安装脚本。
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin sandbox \
    && mkdir -p /project /task/working /task/staging \
    && chown -R sandbox:sandbox /task

USER sandbox
WORKDIR /task/working
ENTRYPOINT ["python"]
