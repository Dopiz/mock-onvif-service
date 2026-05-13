# syntax=docker/dockerfile:1.7

# ── Builder ────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Runtime ────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Tools the running service needs:
#   ffmpeg          → transcode + RTSP push
#   iproute2        → macvlan mode only (`ip link`)
#   isc-dhcp-client → macvlan DHCP mode only
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        iproute2 \
        isc-dhcp-client \
 && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv

# Run as non-root by default. Macvlan mode overrides this with `user: "0"` in
# its compose file because `ip link add` requires root inside the netns.
RUN groupadd --system --gid 10001 mockcam \
 && useradd  --system --uid 10001 --gid mockcam \
            --no-create-home --shell /usr/sbin/nologin mockcam

WORKDIR /app
COPY --chown=mockcam:mockcam . .
RUN mkdir -p data/videos data/snapshots logs/onvif logs/ffmpeg static \
 && chown -R mockcam:mockcam data logs

USER mockcam

EXPOSE 9999
EXPOSE 12000-12999

# Health check uses urllib so the image needs no extra binary.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
        port=os.environ.get('SERVER_PORT','9999'); \
        r=urllib.request.urlopen(f'http://localhost:{port}/health',timeout=5); \
        sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["python", "run.py"]
