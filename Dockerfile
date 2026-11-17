FROM python:3.11-alpine

# No bytecode files, unbuffered output for container logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Unprivileged runtime user
RUN addgroup -S sentinelgroup && adduser -S sentineluser -G sentinelgroup

WORKDIR /app

# Runtime dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application package
COPY docker_sentinel ./docker_sentinel

# Hand the app directory to the runtime user
RUN chown -R sentineluser:sentinelgroup /app

# Drop root
USER sentineluser

# Start the monitor
CMD ["python", "-m", "docker_sentinel"]
