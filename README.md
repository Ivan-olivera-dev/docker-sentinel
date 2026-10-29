<div align="center">
  <h1>🛡️ Docker Sentinel</h1>
  <p><strong>Lightweight, Zero-Trust Auto-Healing Daemon & Crash-Loop Circuit Breaker for Docker</strong></p>
  <p>
    <a href="https://github.com/Ivan-olivera-dev/docker-sentinel/actions/workflows/ci.yml"><img src="https://github.com/Ivan-olivera-dev/docker-sentinel/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
    <a href="https://github.com/Ivan-olivera-dev/docker-sentinel/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT" /></a>
    <img src="https://img.shields.io/badge/python-3.11-3776AB.svg?logo=python&logoColor=white" alt="Python 3.11" />
    <img src="https://img.shields.io/badge/docker-socket--proxy-2496ED.svg?logo=docker&logoColor=white" alt="Docker Socket Proxy" />
  </p>
</div>

---

## 📖 Overview

**Docker Sentinel** is an event-driven Site Reliability Engineering (SRE) daemon designed for single-node VPS and self-hosted production environments.

Instead of polling container health on an interval, Sentinel subscribes directly to the Docker Engine event stream. When an opted-in container terminates unexpectedly (`exitCode != 0`), Sentinel automatically restarts the workload and dispatches an asynchronous HTML incident notification. If a service enters a **Crash Loop**, Sentinel's sliding-window **Circuit Breaker** halts automatic restarts and sends a single critical escalation alert—preventing CPU thrashing and inbox floods.

---

## ✨ Key Engineering Highlights

- 🔒 **Zero-Trust Socket Architecture:** Sentinel runs as an unprivileged user (`sentineluser`) and never mounts `/var/run/docker.sock` directly. All Docker API calls pass through a read-restricted `tecnativa/docker-socket-proxy` over an isolated bridge network with only `CONTAINERS`, `EVENTS`, and `POST` permissions enabled.
- ⚡ **Event-Driven (<15 MB RAM, ~0% Idle CPU):** Streams real-time `die` events from the Docker daemon with zero polling overhead.
- 🔁 **Sliding-Window Circuit Breaker:** Tracks failure timestamps per container. If crashes exceed `SENTINEL_MAX_RESTARTS` within `SENTINEL_TIME_WINDOW_SEC`, automatic recovery pauses and duplicate critical alerts are suppressed until the window clears.
- 🎯 **Granular Opt-In via Labels:** Protects only containers explicitly labeled `sentinel.enable=true`, with optional per-container thresholds and clean-exit policies.
- 🔑 **Docker Secrets & SMTP Hardening:** Supports both `/run/secrets/*` files and environment variables, enforces SMTP socket timeouts, and sanitizes container metadata against header and HTML injection.

---

## 🏗️ Architecture

```text
┌──────────────────────┐       Events / Restart       ┌─────────────────────────┐
│  Monitored Workloads │ ◄──────────────────────────► │   Docker Engine Socket  │
│ sentinel.enable=true │                              │   /var/run/docker.sock  │
└──────────────────────┘                              └────────────▲────────────┘
                                                                   │ (ro mount)
┌──────────────────────┐     tcp://docker-proxy:2375  ┌────────────┴────────────┐
│   Docker Sentinel    │ ───────────────────────────► │   docker-socket-proxy   │
│  (Non-Root User)     │   CONTAINERS=1, EVENTS=1     │   Least-Privilege ACL   │
└──────────┬───────────┘                              └─────────────────────────┘
           │ Async Thread (SMTP + STARTTLS)
           ▼
┌──────────────────────┐
│  Operator Incident   │
│  Alert (HTML Email)  │
└──────────────────────┘
```

---

## 🚀 Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/Ivan-olivera-dev/docker-sentinel.git
cd docker-sentinel
```

### 2. Configure environment variables
Copy the example configuration and add your SMTP credentials (e.g., Gmail App Password, Amazon SES, or Resend SMTP):
```bash
cp .env.example .env
```

### 3. Launch the stack
```bash
docker compose up -d --build
```

### 4. Simulate a crash
The compose stack includes an opted-in `sentinel-test-app` container. Trigger an unexpected crash (`SIGKILL`, exit code `137`) and watch Sentinel heal it in real time:
```bash
docker kill --signal=SIGKILL sentinel-test-app
docker compose logs -f sentinel
```

---

## ⚙️ Configuration

### Global Environment Variables (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DOCKER_HOST` | `tcp://docker-proxy:2375` | Docker API endpoint exposed by the socket proxy |
| `SENTINEL_MAX_RESTARTS` | `3` | Max automatic restarts allowed within the time window |
| `SENTINEL_TIME_WINDOW_SEC` | `300` | Sliding window in seconds for crash-loop detection |
| `SENTINEL_IGNORE_ZERO_EXIT` | `true` | Ignore graceful stops (`exitCode == 0`) by default |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SMTP_SERVER` | `smtp.gmail.com` | Outbound SMTP host |
| `SMTP_PORT` | `587` | Outbound SMTP port (STARTTLS) |
| `SMTP_TIMEOUT` | `15` | Socket timeout in seconds for SMTP connections |
| `SMTP_USER` | — | SMTP username / sender address (also supports `/run/secrets/smtp_user`) |
| `SMTP_PASSWORD` | — | SMTP password (also supports `/run/secrets/smtp_password`) |
| `ALERT_EMAIL_TO` | — | Recipient email address for incident notifications |

### Per-Container Docker Labels

Attach these labels to any service in your own `docker-compose.yml`:

```yaml
services:
  api:
    image: my-org/api:latest
    labels:
      - "sentinel.enable=true"            # Enable Sentinel protection for this container
      - "sentinel.max_restarts=5"         # Optional: Override max restarts for this service
      - "sentinel.window_sec=600"         # Optional: Override sliding window (seconds)
      - "sentinel.restart_on_zero=false"  # Optional: Restart even if exit code is 0
```

---

## 🧪 Running Tests

Sentinel includes a zero-dependency unit test suite covering label opt-in, exit-code filtering, sliding-window crash-loop circuit breaking, and SMTP payload sanitization:

```bash
python3 -m unittest discover -s tests -v
```

---

## 📝 License

Released under the [MIT License](LICENSE). Built by [Iván Olivera González](https://ivanolivera.dev).
