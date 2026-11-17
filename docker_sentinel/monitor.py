"""Docker event loop: watch container exits, restart them and send alerts."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import docker
import docker.errors

from .breaker import CrashBreaker
from .config import ContainerPolicy, Settings

logger = logging.getLogger(__name__)

Notify = Callable[[str, str, bool], object]
"""Callback ``(container_name, exit_code, is_critical)`` that sends an alert."""


class ContainerLike(Protocol):
    labels: Mapping[str, str]

    def restart(self) -> None: ...


def is_monitored(container: Any) -> bool:
    """Return True if the container opted in with the ``sentinel.enable=true`` label."""
    try:
        return ContainerPolicy.from_labels(container.labels, Settings()).enabled
    except Exception:
        return False


def should_handle_exit(container: Any, exit_code: str, settings: Settings) -> bool:
    """Return True if an exit with ``exit_code`` should be handled.

    Clean exits (code 0) are ignored when ``settings.ignore_zero_exit`` is set,
    unless the container sets ``sentinel.restart_on_zero=true``.
    """
    if str(exit_code).strip() == "0" and settings.ignore_zero_exit:
        return ContainerPolicy.from_labels(container.labels, settings).restart_on_zero
    return True


def notify_in_background(send: Callable[[str, str, bool], object]) -> Notify:
    """Wrap ``send`` so each alert runs on a daemon thread and never blocks the loop."""

    def dispatch(container_name: str, exit_code: str, is_critical: bool) -> None:
        threading.Thread(
            target=send,
            args=(container_name, exit_code, is_critical),
            daemon=True,
        ).start()

    return dispatch


class Monitor:
    """Watch Docker ``die`` events and recover opted-in containers."""

    def __init__(
        self,
        settings: Settings,
        notify: Notify,
        client_factory: Callable[[], Any] = docker.from_env,
        breaker: CrashBreaker | None = None,
        shutdown_event: threading.Event | None = None,
        restart_delay: float = 1.0,
        retry_delay: float = 10.0,
    ) -> None:
        self.settings = settings
        self.notify = notify
        self.client_factory = client_factory
        self.breaker = breaker or CrashBreaker()
        self.shutdown_event = shutdown_event or threading.Event()
        self.restart_delay = restart_delay
        self.retry_delay = retry_delay

    def stop(self) -> None:
        self.shutdown_event.set()

    def restart_container(
        self, container: ContainerLike, container_name: str, exit_code: str
    ) -> None:
        """Restart the container, or trip the crash-loop circuit breaker."""
        policy = ContainerPolicy.from_labels(container.labels, self.settings)
        state = self.breaker.record_crash(container_name, policy.max_restarts, policy.window_sec)

        if state.is_crash_loop:
            if state.should_alert:
                logger.critical(
                    "Crash loop detected on %s (more than %d crashes in %ds); "
                    "automatic restarts paused",
                    container_name,
                    policy.max_restarts,
                    policy.window_sec,
                )
                self.notify(container_name, exit_code, True)
            else:
                logger.warning(
                    "Container %s is still in a crash loop; duplicate alert suppressed",
                    container_name,
                )
            return

        try:
            logger.info("Restarting container %s (exit code %s)", container_name, exit_code)
            container.restart()
            logger.info("Container %s restarted", container_name)
            if state.should_alert:
                self.notify(container_name, exit_code, False)
        except Exception as exc:
            logger.error("Failed to restart container %s: %s", container_name, exc)

    def handle_event(self, client: Any, event: Mapping[str, Any]) -> None:
        """Process a single Docker ``die`` event."""
        actor = event.get("Actor", {}) or {}
        container_id = event.get("id") or actor.get("ID")
        attrs = actor.get("Attributes", {}) or {}
        container_name = attrs.get("name", "Unknown")
        exit_code = str(attrs.get("exitCode", "Unknown"))

        if not container_id:
            return

        try:
            container = client.containers.get(container_id)
        except docker.errors.NotFound:
            return

        if not is_monitored(container):
            return

        if not should_handle_exit(container, exit_code, self.settings):
            logger.info("Ignoring clean exit (code 0) of container %s", container_name)
            return

        logger.warning(
            "Container %s exited unexpectedly (exit code %s)",
            container_name,
            exit_code,
        )
        if self.shutdown_event.wait(self.restart_delay):
            return
        self.restart_container(container, container_name, exit_code)

    def run(self) -> None:
        """Run the event loop until :meth:`stop` is called."""
        logger.info(
            "Starting Docker Sentinel (max_restarts=%d, window=%ds, ignore_zero_exit=%s)",
            self.settings.max_restarts,
            self.settings.time_window_sec,
            self.settings.ignore_zero_exit,
        )

        while not self.shutdown_event.is_set():
            try:
                client = self.client_factory()
                client.ping()
                logger.info("Connected to the Docker daemon")

                filters = {"type": "container", "event": "die"}
                for event in client.events(filters=filters, decode=True):
                    if self.shutdown_event.is_set():
                        break
                    self.handle_event(client, event)

            except docker.errors.APIError as exc:
                logger.warning(
                    "Lost connection to the Docker API: %s; retrying in %gs", exc, self.retry_delay
                )
                self.shutdown_event.wait(self.retry_delay)
            except KeyboardInterrupt:
                logger.info("Interrupted, stopping")
                break
            except Exception as exc:
                logger.error("Unexpected error: %s; retrying in %gs", exc, self.retry_delay)
                self.shutdown_event.wait(self.retry_delay)
