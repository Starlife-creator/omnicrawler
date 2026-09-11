FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --upgrade pip && \
    python -m pip install ".[html,async-http,streams]"

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

RUN useradd --create-home --uid 10001 omnicrawler && \
    mkdir -p /data && chown -R omnicrawler:omnicrawler /data /app
USER omnicrawler
WORKDIR /data

ENTRYPOINT ["omnicrawler"]
CMD ["--help"]
