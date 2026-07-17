FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    DATA_ROOT=/app/storage

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /app/storage /tmp/matplotlib

VOLUME ["/app/storage"]

# Public HTTPS terminates at Coolify/Traefik and is forwarded here.
# The Python callback server listens on the same container port.
EXPOSE 80

# Real Docker healthcheck. Coolify waits for Docker's .State.Health.Status
# during rolling updates, so the healthcheck must create a real status.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=10 \
    CMD python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:80/healthz', timeout=3); raise SystemExit(0 if r.status == 200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "run.py"]
