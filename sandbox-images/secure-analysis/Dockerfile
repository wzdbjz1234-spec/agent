# 构建调用方必须传入不可变的上游镜像引用，例如：
# python:3.12-slim@sha256:<64 lowercase hex>。不提供标签回退，避免构建漂移。
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# 受限网络部署可覆盖 pip 镜像源；缺省使用官方 PyPI。
ARG PIP_INDEX_URL=https://pypi.org/simple

USER root
COPY requirements.lock /tmp/requirements.lock
# 安装仅发生在镜像构建期；运行时镜像不含包管理器入口或安装脚本。
RUN python -m pip install --no-cache-dir --index-url ${PIP_INDEX_URL} --requirement /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

# 移除构建后不再需要的 pip 本体（含其历史漏洞面）；镜像内已无任何包管理器入口。
RUN python -m pip uninstall -y pip \
    && rm -rf /root/.cache/pip /tmp/pip-* /usr/local/lib/python3.12/site-packages/pip* \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12

# 镜像内置 SQL runner：/project 上的 DuckDB 查询执行与 schema sidecar。
COPY sql_runner.py /usr/local/bin/dataharness-sql-runner.py

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin sandbox \
    && mkdir -p /project /task/working /task/staging \
    && chown -R sandbox:sandbox /task

USER sandbox
WORKDIR /task/working
ENTRYPOINT ["python"]
