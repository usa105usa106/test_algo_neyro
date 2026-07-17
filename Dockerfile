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

# Make the Gmail callback listener discoverable by Coolify/Traefik.
EXPOSE 80

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "run.py"]
