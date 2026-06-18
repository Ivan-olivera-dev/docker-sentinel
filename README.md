<div align="center">
  <h1>🛡️ Docker Sentinel (Auto-Healer)</h1>
  <p>A lightweight, zero-dependency Docker monitoring daemon that auto-restarts crashed containers and sends beautiful HTML email alerts. Built for resilience and production environments.</p>
</div>

---

## 📖 Overview

**Docker Sentinel** acts as an automated Site Reliability Engineer (SRE) for your VPS. It listens to the live Docker event stream and instantly detects when a container exits unexpectedly (Exit Code != 0 or generic `die` events). 

Instead of waking you up at 3 AM for a random memory spike, Sentinel will automatically restart the container. If a container enters a **Crash Loop** (fails repeatedly), Sentinel's built-in Rate Limiter will stop the restart cycle and send a CRITICAL alert to your email.

## ✨ Features

- ⚡ **Real-Time Monitoring:** Hooks directly into `/var/run/docker.sock` for zero-latency event detection.
- 🎯 **Opt-in Surveillance:** Only monitors containers explicitly labeled with `sentinel.enable=true`.
- 🔁 **Anti-Crash Loop (Rate Limiter):** Prevents infinite restart loops and email spam if a container is fundamentally broken (e.g., > 3 crashes in 5 minutes).
- 📧 **Asynchronous Email Alerts:** Sends beautifully formatted HTML emails via SMTP in a background thread without blocking the monitoring loop.
- 🛡️ **Daemon Resilience:** Auto-reconnects safely if the host Docker daemon restarts or updates.

## 🚀 Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/Ivan-olivera-dev/docker-sentinel.git
cd docker-sentinel
```

### 2. Configure Environment Variables
Copy the example file and fill in your SMTP credentials (e.g., Gmail App Passwords):
```bash
cp .env.example .env
```

### 3. Deploy Sentinel
Start the Sentinel daemon using Docker Compose:
```bash
docker-compose up -d --build
```
*Note: A dummy `test-app` container is included in the `docker-compose.yml` so you can instantly test the auto-healing functionality by running `docker kill sentinel-test-app`.*

## 🏷️ How to Monitor Your Containers

Sentinel uses an **opt-in** approach. To protect a specific container in your infrastructure, simply add the following label to its `docker-compose.yml`:

```yaml
services:
  my-production-app:
    image: my-app:latest
    labels:
      - "sentinel.enable=true" # <--- Sentinel will now protect this container
```

## 🛠️ Architecture Highlights

- **Base Image:** `python:3.11-alpine` (Minimal footprint, ~50MB).
- **Core Library:** Official `docker` SDK for Python.
- **Resource Usage:** < 15MB RAM and 0% Idle CPU.

## 📝 License

MIT License. Created by [Iván Olivera González](https://ivanolivera.dev).
