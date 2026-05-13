# syntax=docker/dockerfile:1.7

# ── Builder ────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# Pull uv binary from the official Astral image — no install step, no pip bootstrap.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY requirements.txt .
# Create venv and install in one uv invocation (much faster than pip on ARM).
# BuildKit cache mount keeps the uv wheel cache across builds — second build
# of the same requirements is nearly instant. The cache is NOT baked into the
# final image (mount lives only during this RUN).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv --python 3.13 \
 && uv pip install --python /opt/venv/bin/python -r requirements.txt

# ── Runtime ────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Tools the running service needs:
#   ffmpeg          → transcode + RTSP push
#   iproute2        → macvlan mode only (`ip link`)
#   isc-dhcp-client → macvlan DHCP mode only
#
# BuildKit cache mounts keep apt's deb cache across builds. The default
# python:3.13-slim image runs `apt-get clean` after every install via
# /etc/apt/apt.conf.d/docker-clean — remove it so the cache mount is actually
# usable when the layer DOES need to rerun.
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
 && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' \
        > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        iproute2 \
        isc-dhcp-client

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
