# Base image pinned by tag and by multi-arch index digest (amd64 and arm64
# included). Update both together when moving to a newer patch release.
FROM python:3.13-alpine@sha256:9ef1a599f863231c3d91e941389bfdc4a2d6070557243d54df1a4e57015b960e

ARG VERSION=dev
ARG REVISION=unknown

LABEL org.opencontainers.image.title="docker-sentinel" \
      org.opencontainers.image.description="Watch Docker containers, restart crashed ones and email an alert when one enters a crash loop." \
      org.opencontainers.image.source="https://github.com/Ivan-olivera-dev/docker-sentinel" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Unprivileged runtime user with a fixed UID/GID, so file ownership and
# securityContext/user settings are predictable across hosts.
RUN addgroup -S -g 10001 sentinel \
    && adduser -S -D -H -u 10001 -G sentinel sentinel

WORKDIR /app

# Runtime dependencies first, so this layer is cached while only the code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code stays owned by root: the unprivileged runtime user can read it but not modify it.
COPY docker_sentinel ./docker_sentinel

USER 10001:10001

# No HEALTHCHECK: the daemon exposes no endpoint, and a fatal error ends the
# process, which Docker already reports through the container state and the
# restart policy. A process-alive probe would add nothing.
CMD ["python", "-m", "docker_sentinel"]
