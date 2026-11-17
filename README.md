# Docker Sentinel

[![CI](https://github.com/Ivan-olivera-dev/docker-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/Ivan-olivera-dev/docker-sentinel/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Ivan-olivera-dev/docker-sentinel)](https://github.com/Ivan-olivera-dev/docker-sentinel/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Ivan-olivera-dev/docker-sentinel/blob/main/LICENSE)

Docker Sentinel is a small Python daemon that watches the Docker event stream, restarts crashed containers and sends an email when one of them gets stuck in a crash loop.

I built it for single-node VPS setups where a full monitoring stack is overkill, but you still want to know when a container keeps falling over instead of finding out days later.

## Why not just use Docker restart policies?

Docker restart policies bring a container back up, but they never tell you anything, and they have no notion of a crash loop over time:

- `always` and `unless-stopped` restart the container indefinitely. Docker adds a growing delay between attempts (capped at one minute), so a broken container keeps restarting forever without anyone noticing.
- `on-failure:N` stops after N restart attempts in a row. That is a count of attempts, not a rate: there is no time window, so it cannot tell "crashed 5 times in a minute" apart from "crashed 5 times in a month". When the limit is reached Docker simply leaves the container stopped, again without notice.

See [Start containers automatically](https://docs.docker.com/engine/containers/start-containers-automatically/) and the [`docker run --restart` reference](https://docs.docker.com/reference/cli/docker/container/run/#restart) for the exact semantics.

Sentinel covers what Docker does not: it counts crashes per container in a sliding time window, trips a circuit breaker when a container crashes too often, and emails you. It works alongside existing restart policies rather than replacing them:

- Containers with an `always`, `unless-stopped` or `on-failure` policy are left to Docker. Sentinel only records the crash and sends the alerts; it does not restart them a second time.
- Containers without a restart policy (or with `no`) are restarted by Sentinel until the circuit breaker trips.
- `on-failure` does not cover exit code 0, so if you opt in to handling clean exits (see `sentinel.restart_on_zero`), Sentinel restarts those itself.

## How it works

Sentinel subscribes to container `die` events and only acts on containers with the `sentinel.enable=true` label.

1. When a monitored container exits with a non-zero code, Sentinel waits one second and records the crash. Clean exits (code 0) are ignored unless the container sets `sentinel.restart_on_zero=true`.
2. If the container has no Docker restart policy, Sentinel restarts it and sends a "container restarted" email. If it has one, Sentinel leaves the restart to Docker and sends a "container crashed" email.
3. If the container crashes more than `max_restarts` times within `window_sec` seconds (3 times in 300 seconds by default), the circuit breaker trips. Sentinel stops restarting it and sends a single crash-loop email. Further crashes inside the loop do not send more emails. For containers that Docker restarts, the email says that Sentinel cannot pause Docker's own restarts and that manual intervention is required.

Emails are sent from a background thread, as multipart messages with a plain-text and an HTML part.

```text
┌──────────────────────┐        die events / restart        ┌─────────────────────────┐
│  Monitored workloads │ <--------------------------------> │      Docker Engine      │
│ sentinel.enable=true │                                    │  /var/run/docker.sock   │
└──────────────────────┘                                    └────────────^────────────┘
                                                                         │ (ro mount)
┌──────────────────────┐   tcp://docker-proxy:2375          ┌────────────┴────────────┐
│    Docker Sentinel   │ ---------------------------------> │ linuxserver/socket-proxy│
│   (UID/GID 10001)    │   network: sentinel-internal       │ CONTAINERS=1, EVENTS=1  │
└──────────┬───────────┘   (internal, no outside access)    │ ALLOW_RESTARTS=1, POST=0│
           │                                                └─────────────────────────┘
           │ network: sentinel-egress
           │ SMTP + STARTTLS (background thread)
           v
┌──────────────────────┐
│  Alert email         │
│  (text + HTML)       │
└──────────────────────┘
```

Sentinel never mounts the Docker socket. It talks to Docker through a socket proxy that sits on an internal-only network, and reaches your SMTP server through a separate egress network.

## Quick start

The image is published to GitHub Container Registry for `linux/amd64` and `linux/arm64`:

```bash
docker pull ghcr.io/ivan-olivera-dev/docker-sentinel:1.0.0
```

1. Get the compose file. It runs the socket proxy, Sentinel (from the published image) and an optional `test-app` container to try things out. You can clone the repository or download just the file:

```bash
mkdir docker-sentinel && cd docker-sentinel
curl -fsSLO https://raw.githubusercontent.com/Ivan-olivera-dev/docker-sentinel/main/docker-compose.yml
```

2. Create a `.env` file next to it with your SMTP settings. If you cloned the repository, start from the example with `cp .env.example .env`. A minimal file looks like this:

```dotenv
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASSWORD=your-app-password
ALERT_EMAIL_TO=you@example.com
```

3. Start the stack:

```bash
docker compose up -d
```

4. Kill the test container (`SIGKILL`, exit code 137) and watch Sentinel restart it and send the email:

```bash
docker kill --signal=SIGKILL sentinel-test-app
docker compose logs -f sentinel
```

To watch your own services, add the `sentinel.enable=true` label to them (see below). They do not need to be in the same compose file.

## Configuration

### Environment variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DOCKER_HOST` | `tcp://docker-proxy:2375` (set in `docker-compose.yml`) | Docker API endpoint, normally the socket proxy |
| `SENTINEL_MAX_RESTARTS` | `3` | Crashes allowed within the window before the circuit breaker trips |
| `SENTINEL_TIME_WINDOW_SEC` | `300` | Length of the sliding window, in seconds |
| `SENTINEL_IGNORE_ZERO_EXIT` | `true` | Ignore clean exits (code 0) unless the container sets `sentinel.restart_on_zero=true`. Set to `false` to handle every exit |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `SMTP_SERVER` | `smtp.gmail.com` | SMTP host |
| `SMTP_PORT` | `587` | SMTP port; the connection is upgraded with STARTTLS |
| `SMTP_TIMEOUT` | `15` | SMTP socket timeout, in seconds |
| `SMTP_USER` | none | SMTP username, also used as the sender address |
| `SMTP_PASSWORD` | none | SMTP password |
| `ALERT_EMAIL_TO` | none | Recipient of the alerts |

If `SMTP_USER`, `SMTP_PASSWORD` or `ALERT_EMAIL_TO` is missing, Sentinel still restarts containers but logs a warning instead of sending email.

The SMTP variables and `ALERT_EMAIL_TO` can also be provided as [Docker secrets](https://docs.docker.com/compose/how-tos/use-secrets/). Sentinel reads `/run/secrets/<name in lowercase>` first (for example `/run/secrets/smtp_password`) and falls back to the environment variable when the file does not exist.

### Container labels

| Label | Default | Description |
| :--- | :--- | :--- |
| `sentinel.enable` | `false` | Set to `true` to have Sentinel watch the container |
| `sentinel.max_restarts` | `SENTINEL_MAX_RESTARTS` | Per-container crash limit within the window |
| `sentinel.window_sec` | `SENTINEL_TIME_WINDOW_SEC` | Per-container window, in seconds |
| `sentinel.restart_on_zero` | `false` | Set to `true` to treat clean exits (code 0) as crashes for this container |

Each numeric label falls back to its global default on its own: an invalid `sentinel.window_sec` uses `SENTINEL_TIME_WINDOW_SEC` and does not affect a valid `sentinel.max_restarts`, and the other way round.

```yaml
services:
  api:
    image: my-org/api:latest
    labels:
      - "sentinel.enable=true"
      - "sentinel.max_restarts=5"
      - "sentinel.window_sec=600"
      - "sentinel.restart_on_zero=false"
```

## Security

- **Non-root container.** Sentinel runs as a fixed unprivileged user (UID/GID 10001). The application code in the image is owned by root, so the runtime user can read it but not modify it.
- **No direct socket access.** Only the socket proxy mounts `/var/run/docker.sock` (read-only mount, read-only container filesystem). The proxy image is `linuxserver/socket-proxy`, pinned by digest.
- **Least-privilege API access.** The proxy allows container reads (`CONTAINERS=1`), the event stream (`EVENTS=1`) and container restarts (`ALLOW_RESTARTS=1`). All other write requests are denied (`POST=0`), so Sentinel cannot create containers, start exec sessions, or touch images, volumes or networks.
- **Network isolation.** The proxy is only attached to `sentinel-internal`, an internal network with no outside access, and publishes no ports. Sentinel joins a second network, `sentinel-egress`, for SMTP.

Residual risk: whoever controls the Sentinel container can still use what the proxy allows. `ALLOW_RESTARTS` also permits stopping and killing containers, and `CONTAINERS=1` permits reading container details and logs, including the environment variables shown by `docker inspect`. That allows disruption and information disclosure, but not container creation or host access.

## Limitations

- **Single Docker host.** Sentinel watches the daemon it is connected to. It does not support Swarm services or multiple hosts.
- **Email only.** There are no other notification channels.
- **No replay of missed events.** Containers that exit while Sentinel is down or reconnecting to Docker are not picked up afterwards.
- **In-memory state.** Crash history and tripped circuit breakers live in memory and reset when Sentinel restarts.
- **Cannot pause Docker's restarts.** For containers with a Docker restart policy, Sentinel can only alert. Stopping the loop is up to you.
- **Manual stops can look like crashes.** Sentinel decides from the exit code alone. A container that does not exit cleanly on `docker stop` (for example exit code 137 after the stop timeout) is treated as a crash and restarted.
- **Shutdown during a hung Docker call.** `SIGTERM` stops Sentinel immediately while it waits for events, but if it arrives during a Docker API call that hangs, shutdown waits for the Docker client timeout (60 seconds). An alert email that is still being sent at that moment may be cut off.

## Development

Requires Python 3.11 or newer.

```bash
git clone https://github.com/Ivan-olivera-dev/docker-sentinel.git
cd docker-sentinel
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the same checks as CI:

```bash
ruff check .
ruff format --check .
mypy docker_sentinel
python -m unittest discover -s tests -v
```

Run the daemon outside Docker with `python -m docker_sentinel` (or the `docker-sentinel` entry point). It uses `DOCKER_HOST` or the local Docker socket.

To build the image locally, replace `image:` with `build: .` in `docker-compose.yml` as described in the file, then run `docker compose up -d --build`.

### Releases

Releases are cut by pushing a `vX.Y.Z` tag that matches `__version__` in `docker_sentinel/__init__.py`. The release workflow runs the CI checks, verifies the tag, publishes the multi-architecture image to `ghcr.io/ivan-olivera-dev/docker-sentinel` and creates the GitHub Release from the matching section of [CHANGELOG.md](CHANGELOG.md).

## License

MIT License, see [LICENSE](LICENSE). Created by [Iván Olivera González](https://github.com/Ivan-olivera-dev).
