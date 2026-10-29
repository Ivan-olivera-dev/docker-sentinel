import os
import signal
import time
import threading
import logging
import docker
from notifier import send_alert_email

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sentinel")

# --- RATE LIMITING & CIRCUIT BREAKER DEFAULTS ---
MAX_RESTARTS = int(os.getenv("SENTINEL_MAX_RESTARTS", "3"))
TIME_WINDOW_SEC = int(os.getenv("SENTINEL_TIME_WINDOW_SEC", "300"))
IGNORE_ZERO_EXIT = os.getenv("SENTINEL_IGNORE_ZERO_EXIT", "true").lower() == "true"
# ------------------------------------------------

crash_tracker: dict[str, list[float]] = {}
tripped_containers: set[str] = set()
shutdown_event = threading.Event()


def is_monitored(container) -> bool:
    """Return True if the container opted into Sentinel protection via labels."""
    try:
        return container.labels.get("sentinel.enable", "false").lower() == "true"
    except Exception:
        return False


def should_handle_exit(container, exit_code: str) -> bool:
    """
    Determine whether an exited container should be auto-restarted.
    Clean exits (exitCode == '0') are ignored by default unless the container
    sets label sentinel.restart_on_zero=true or SENTINEL_IGNORE_ZERO_EXIT=false.
    """
    code_str = str(exit_code).strip()
    if code_str == "0" and IGNORE_ZERO_EXIT:
        restart_on_zero = container.labels.get("sentinel.restart_on_zero", "false").lower() == "true"
        return restart_on_zero
    return True


def get_container_limits(container) -> tuple[int, int]:
    """Read optional per-container rate-limit overrides from Docker labels."""
    try:
        max_restarts = int(container.labels.get("sentinel.max_restarts", MAX_RESTARTS))
        window_sec = int(container.labels.get("sentinel.window_sec", TIME_WINDOW_SEC))
        return max_restarts, window_sec
    except (ValueError, AttributeError):
        return MAX_RESTARTS, TIME_WINDOW_SEC


def evaluate_crash_state(
    container_name: str,
    max_restarts: int = MAX_RESTARTS,
    window_sec: int = TIME_WINDOW_SEC,
) -> tuple[bool, bool]:
    """
    Record a container crash and evaluate circuit breaker state.
    Returns (is_crash_loop, should_send_alert).
    When a container exceeds max_restarts within window_sec, the circuit breaker
    trips once and suppresses duplicate critical alert emails while active.
    """
    now = time.time()
    history = crash_tracker.get(container_name, [])
    history = [t for t in history if now - t <= window_sec]
    history.append(now)
    crash_tracker[container_name] = history

    is_loop = len(history) > max_restarts
    if is_loop:
        if container_name in tripped_containers:
            return True, False
        tripped_containers.add(container_name)
        return True, True

    tripped_containers.discard(container_name)
    return False, True


def check_crash_loop(container_name: str) -> bool:
    """Backward-compatible helper returning whether the container is in a crash loop."""
    is_loop, _ = evaluate_crash_state(container_name)
    return is_loop


def async_send_email(container_name: str, exit_code: str, is_critical: bool) -> None:
    """Dispatch alert email on a daemon thread so the event loop never blocks."""
    thread = threading.Thread(
        target=send_alert_email,
        args=(container_name, exit_code, is_critical),
        daemon=True,
    )
    thread.start()


def restart_container(client, container, container_id: str, container_name: str, exit_code: str) -> None:
    """Attempt container recovery or trip the crash-loop circuit breaker."""
    max_restarts, window_sec = get_container_limits(container)
    is_loop, should_alert = evaluate_crash_state(container_name, max_restarts, window_sec)

    if is_loop:
        if should_alert:
            logger.critical(
                "🛑 [SENTINEL] CRASH LOOP detected on '%s' (>%d crashes in %ds). Circuit breaker OPEN.",
                container_name,
                max_restarts,
                window_sec,
            )
            async_send_email(container_name, exit_code, is_critical=True)
        else:
            logger.warning(
                "🛑 [SENTINEL] '%s' remains in crash loop; duplicate alert suppressed.",
                container_name,
            )
        return

    try:
        logger.info("🔄 [SENTINEL] Restarting '%s' (exit code: %s)...", container_name, exit_code)
        container.restart()
        logger.info("✅ [SENTINEL] '%s' restarted successfully.", container_name)
        if should_alert:
            async_send_email(container_name, exit_code, is_critical=False)
    except Exception as exc:
        logger.error("❌ [SENTINEL] Failed to restart '%s': %s", container_name, exc)


def _handle_shutdown(signum, _frame):
    logger.info("🛑 [SENTINEL] Signal %s received. Shutting down gracefully...", signum)
    shutdown_event.set()


def start_monitoring() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info(
        "🛡️ [SENTINEL] Starting active container protection (max_restarts=%d, window=%ds)...",
        MAX_RESTARTS,
        TIME_WINDOW_SEC,
    )

    while not shutdown_event.is_set():
        try:
            client = docker.from_env()
            client.ping()
            logger.info("🔗 [SENTINEL] Connected to Docker daemon.")

            filters = {"type": "container", "event": "die"}

            for event in client.events(filters=filters, decode=True):
                if shutdown_event.is_set():
                    break

                container_id = event.get("id") or event.get("Actor", {}).get("ID")
                attrs = event.get("Actor", {}).get("Attributes", {})
                container_name = attrs.get("name", "Unknown")
                exit_code = str(attrs.get("exitCode", "Unknown"))

                if not container_id:
                    continue

                try:
                    container = client.containers.get(container_id)
                except docker.errors.NotFound:
                    continue

                if not is_monitored(container):
                    continue

                if not should_handle_exit(container, exit_code):
                    logger.info(
                        "ℹ️ [SENTINEL] Ignoring clean exit (code 0) for '%s'.",
                        container_name,
                    )
                    continue

                logger.warning(
                    "🚨 [SENTINEL] Container '%s' died unexpectedly (Exit Code: %s).",
                    container_name,
                    exit_code,
                )
                time.sleep(1)
                restart_container(client, container, container_id, container_name, exit_code)

        except docker.errors.APIError as exc:
            logger.warning("⚠️ [SENTINEL] Lost connection to Docker API: %s. Retrying in 10s...", exc)
            shutdown_event.wait(10)
        except KeyboardInterrupt:
            logger.info("🛑 [SENTINEL] Stopping Sentinel...")
            break
        except Exception as exc:
            logger.error("❌ [SENTINEL] Unexpected error: %s. Retrying in 10s...", exc)
            shutdown_event.wait(10)


if __name__ == "__main__":
    start_monitoring()
