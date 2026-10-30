FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# C-#10：SDK 契约 schema 进入镜像，服务端校验/快照生成不依赖源码目录。
COPY schemas ./schemas
ARG EXTRAS="html,async-http,streams"
RUN python -m pip install --upgrade pip && \
    python -m pip install ".[${EXTRAS}]"

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
# 全部 console scripts：omnicrawler / omnicrawler-gui / omnicrawler-workbench /
# pdfx / pdf-process / pdf-extract。此前只 COPY 了 `omnicrawler*`，
# 镜像里因此缺了 3 个 PDF 入口（审计 report_packaging_scripts 已记录）。
COPY --from=builder /usr/local/bin/omnicrawler* /usr/local/bin/
COPY --from=builder /usr/local/bin/pdf* /usr/local/bin/
COPY --from=builder /app /app

# C-#10：tini 作为 PID 1 初始化进程，回收僵尸子进程并正确转发信号；
# 此前容器由 Python 进程直接接管，子进程回收与信号处理无保障。
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 omnicrawler && \
    mkdir -p /data && chown -R omnicrawler:omnicrawler /data /app
USER omnicrawler
WORKDIR /data

# C-#10：编排系统探活。配置经 compose 的 ./configs:/data/configs:ro 挂载；
# 裸 `docker run` 未挂配置时 doctor 会在健康检查窗口内失败，属于预期边界。
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD omnicrawler doctor -c configs/project.yaml

ENTRYPOINT ["/usr/bin/tini", "--", "omnicrawler"]
CMD ["--help"]
