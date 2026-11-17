# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-17

First versioned release.

### Added

- Multi-architecture container image (`linux/amd64`, `linux/arm64`) published to
  `ghcr.io/ivan-olivera-dev/docker-sentinel` on every version tag, with build
  provenance and an SBOM attached.
- Release workflow that runs the CI checks, verifies that the tag matches the
  package version and creates the GitHub Release from this changelog.
- Alert emails now include a `text/plain` part alongside the HTML one.
- `pyproject.toml` with project metadata, a `docker-sentinel` entry point, and
  ruff and mypy configuration.
- CI runs ruff, mypy and the unit tests on Python 3.11, 3.12 and 3.13 before
  building the image.

### Changed

- The daemon is now the `docker_sentinel` package (config, breaker, notifier,
  monitor) and runs with `python -m docker_sentinel`. Crash tracking is a class
  instead of module-level state, and settings are typed.
- Containers with an `always`, `unless-stopped` or `on-failure` restart policy
  are no longer restarted by Sentinel as well as by Docker. Sentinel records the
  crash and sends the crash-loop alert, which notes that it cannot pause
  Docker's own restarts. Because `on-failure` does not cover exit code 0,
  Sentinel still handles clean exits of those containers itself.
- The Docker socket proxy is now `linuxserver/socket-proxy`, pinned by digest,
  with `ALLOW_RESTARTS=1` and `POST=0`. The previous proxy only honoured
  restarts with `POST=1`, which also allowed creating containers and running
  exec sessions.
- The socket proxy sits on an internal-only network. Sentinel joins a separate
  network for outbound SMTP.
- Logs and alert emails no longer contain emojis.
- The image uses `python:3.13-alpine` pinned by digest, runs as a fixed
  unprivileged user (UID/GID 10001) and carries OCI labels.

### Fixed

- `SIGTERM` and `SIGINT` close the open Docker events stream, so the daemon
  stops immediately instead of waiting for the next event and being killed by
  `docker stop` after the timeout.
- An invalid `sentinel.max_restarts` or `sentinel.window_sec` label now falls
  back to the default for that label only, instead of resetting both limits.

[Unreleased]: https://github.com/Ivan-olivera-dev/docker-sentinel/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Ivan-olivera-dev/docker-sentinel/releases/tag/v1.0.0
