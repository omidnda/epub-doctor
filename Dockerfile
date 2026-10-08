FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 epubdoctor \
    && useradd --uid 10001 --gid 10001 --no-create-home epubdoctor \
    && install -d -o 10001 -g 10001 -m 0700 /data

WORKDIR /app
# Match the repository's tested MCP SDK; no new runtime dependency.
RUN pip install --no-cache-dir 'mcp==2.3.0'
COPY src/ ./src/
COPY vendor/epubcheck-5.4.0/ ./vendor/epubcheck-5.4.0/

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"
ENTRYPOINT ["python", "-m", "src.chatgpt_mcp_server"]
CMD ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000", "--download-directory", "/data/downloads"]
