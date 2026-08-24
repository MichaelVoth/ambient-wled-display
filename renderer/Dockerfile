FROM python:3.12-alpine

WORKDIR /app
COPY ambient_renderer /app/ambient_renderer
COPY static /app/static

RUN addgroup -S renderer && adduser -S renderer -G renderer \
    && mkdir -p /data && chown -R renderer:renderer /data /app

USER renderer
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2)"
ENTRYPOINT ["python", "-m", "ambient_renderer"]
CMD ["--config", "/config/renderer.json", "--port", "8090"]
