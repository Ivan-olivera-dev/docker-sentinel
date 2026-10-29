# Docker Sentinel

[![CI](https://github.com/Ivan-olivera-dev/docker-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/Ivan-olivera-dev/docker-sentinel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Ivan-olivera-dev/docker-sentinel/blob/main/LICENSE)

Docker Sentinel is a small Python daemon that watches the Docker event stream, restarts crashed containers, and sends an email alert when something goes wrong.

I built it for single-node VPS setups where running a full monitoring stack is overkill, but you still want containers to recover from random crashes without getting stuck in an infinite restart loop.

## How it works

Sentinel listens for container `die` events from the Docker daemon. It only acts on containers that have the `sentinel.enable=true` label.

When a monitored container exits with a non-zero code, Sentinel restarts it and sends an HTML email report in a background thread. Clean exits (`exitCode == 0`, such as a manual `docker stop`) are ignored by default unless you set `sentinel.restart_on_zero=true` on that container.

If a container keeps crashing (by default, more than 3 times within 5 minutes), a sliding-window circuit breaker trips. Sentinel stops restarting that container and sends a single critical email so your server does not burn CPU or flood your inbox.

```text
┌──────────────────────┐       Events / Restart       ┌─────────────────────────┐
│  Monitored Workloads │ <--------------------------> │   Docker Engine Socket  │
│ sentinel.enable=true │                              │   /var/run/docker.sock  │
└──────────────────────┘                              └────────────^────────────┘
                                                                   │ (ro mount)
┌──────────────────────┐     tcp://docker-proxy:2375  ┌────────────┴────────────┐
│   Docker Sentinel    │ ---------------------------> │   docker-socket-proxy   │
│  (Non-Root User)     │   CONTAINERS=1, EVENTS=1     │   Read-restricted ACL   │
└──────────┬───────────┘                              └─────────────────────────┘
           │ Async Thread (SMTP + STARTTLS)
           v
┌──────────────────────┐
│  Incident Alert      │
│  (HTML Email)        │
└──────────────────────┘
```

For security, the Sentinel container runs as a non-root user (`sentineluser`) and never mounts `/var/run/docker.sock` directly. It talks to Docker through `tecnativa/docker-socket-proxy` over an internal bridge network with only `CONTAINERS=1`, `EVENTS=1`, and `POST=1` enabled.

## Quick start

1. Clone the repository:
```bash
git clone https://github.com/Ivan-olivera-dev/docker-sentinel.git
cd docker-sentinel
```

2. Copy the environment file and add your SMTP credentials:
```bash
cp .env.example .env
```

3. Start the stack:
```bash
docker compose up -d --build
```

4. Test the recovery flow by killing the included `sentinel-test-app` container (`SIGKILL`, exit code `137`):
```bash
docker kill --signal=SIGKILL sentinel-test-app
docker compose logs -f sentinel
```

## Configuration

### Environment variables (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DOCKER_HOST` | `tcp://docker-proxy:2375` | Docker API endpoint exposed by the socket proxy |
| `SENTINEL_MAX_RESTARTS` | `3` | Max automatic restarts allowed within the time window |
| `SENTINEL_TIME_WINDOW_SEC` | `300` | Sliding window in seconds for crash-loop detection |
| `SENTINEL_IGNORE_ZERO_EXIT` | `true` | Ignore graceful stops (`exitCode == 0`) by default |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SMTP_SERVER` | `smtp.gmail.com` | Outbound SMTP host |
| `SMTP_PORT` | `587` | Outbound SMTP port (STARTTLS) |
| `SMTP_TIMEOUT` | `15` | Socket timeout in seconds for SMTP connections |
| `SMTP_USER` | None | SMTP username or sender address (also reads `/run/secrets/smtp_user`) |
| `SMTP_PASSWORD` | None | SMTP password (also reads `/run/secrets/smtp_password`) |
| `ALERT_EMAIL_TO` | None | Recipient email address for incident notifications |

### Container labels

Add these labels to any service you want Sentinel to watch in its `docker-compose.yml`:

```yaml
services:
  api:
    image: my-org/api:latest
    labels:
      - "sentinel.enable=true"            # Enable Sentinel for this container
      - "sentinel.max_restarts=5"         # Optional: override max restarts
      - "sentinel.window_sec=600"         # Optional: override sliding window (seconds)
      - "sentinel.restart_on_zero=false"  # Optional: restart even if exit code is 0
```

## Running tests

The unit tests use Python's standard `unittest` module and run without external dependencies:

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT License. Created by [Iván Olivera González](https://ivanolivera.dev).
