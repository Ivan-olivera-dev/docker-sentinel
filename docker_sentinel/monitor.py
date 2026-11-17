"""Docker event loop: watch container exits, restart them and send alerts."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import docker
import docker.errors

from .breaker import CrashBreaker, CrashState
from .config import ContainerPolicy, Settings

logger = logging.getLogger(__name__)

DOCKER_MANAGED_POLICIES = frozenset({"always", "unless-stopped", "on-failure"})
"""Docker restart policies under which Docker itself restarts a crashed container."""


class Notify(Protocol):
    """Callback that sends an alert.

    ``docker_restart_policy`` is passed only when Docker, not Sentinel, restarts
    the container; it names the policy so the alert can say so.
    """

    def __call__(
        self,
        container_name: str,
        exit_code: str,
        is_critical: bool,
        docker_restart_policy: str | None = None,
    ) -> object: ...


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


def docker_restart_policy(container: Any, exit_code: str) -> str | None:
    """Return the Docker restart policy that will restart this container, if any.

    Reads ``HostConfig.RestartPolicy.Name`` from the container attributes. Returns
    None when the policy is ``no``, empty, unknown or missing, and for ``on-failure``
    when the exit code is 0 (Docker does not restart clean exits under that policy).
    """
    try:
        name = container.attrs["HostConfig"]["RestartPolicy"]["Name"]
    except (AttributeError, KeyError, TypeError):
        return None
    policy = str(name or "").strip().lower()
    if policy not in DOCKER_MANAGED_POLICIES:
        return None
    if policy == "on-failure" and str(exit_code).strip() == "0":
        return None
    return policy


def notify_in_background(send: Notify) -> Notify:
    """Wrap ``send`` so each alert runs on a daemon thread and never blocks the loop."""

    def dispatch(
        container_name: str,
        exit_code: str,
        is_critical: bool,
        docker_restart_policy: str | None = None,
    ) -> None:
        kwargs = {}
        if docker_restart_policy is not None:
            kwargs["docker_restart_policy"] = docker_restart_policy
        threading.Thread(
            target=send,
            args=(container_name, exit_code, is_critical),
            kwargs=kwargs,
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
        # The open events stream, so stop() can unblock the loop. Plain attribute
        # access (no lock) because stop() runs inside a signal handler.
        self._events_stream: Any = None

    def stop(self) -> None:
        """Stop the loop. Safe to call from a signal handler or another thread.

        Closing the events stream unblocks the loop immediately instead of
        waiting for the next Docker event.
        """
        self.shutdown_event.set()
        stream = self._events_stream
        if stream is not None:
            _close_stream(stream)

    def restart_container(
        self, container: ContainerLike, container_name: str, exit_code: str
    ) -> None:
        """Restart the container, or trip the crash-loop circuit breaker."""
        policy = ContainerPolicy.from_labels(container.labels, self.settings)
        state = self.breaker.record_crash(container_name, policy.max_restarts, policy.window_sec)
        docker_policy = docker_restart_policy(container, exit_code)

        if docker_policy is not None:
            self._handle_docker_managed(container_name, exit_code, docker_policy, state, policy)
            return

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

    def _handle_docker_managed(
        self,
        container_name: str,
        exit_code: str,
        docker_policy: str,
        state: CrashState,
        policy: ContainerPolicy,
    ) -> None:
        """Alert on a crash that Docker's own restart policy will handle."""
        if state.is_crash_loop:
            if state.should_alert:
                logger.critical(
                    "Crash loop detected on %s (more than %d crashes in %ds); Docker keeps "
                    "restarting it (restart policy %s) and Docker Sentinel cannot pause "
                    "Docker's restarts, manual intervention required",
                    container_name,
                    policy.max_restarts,
                    policy.window_sec,
                    docker_policy,
                )
                self.notify(container_name, exit_code, True, docker_restart_policy=docker_policy)
            else:
                logger.warning(
                    "Container %s is still in a crash loop; duplicate alert suppressed",
                    container_name,
                )
            return

        logger.info(
            "Container %s has Docker restart policy %s; leaving the restart to Docker",
            container_name,
            docker_policy,
        )
        if state.should_alert:
            self.notify(container_name, exit_code, False, docker_restart_policy=docker_policy)

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
                stream = client.events(filters=filters, decode=True)
                self._events_stream = stream
                try:
                    # stop() may have run before the stream was registered.
                    if self.shutdown_event.is_set():
                        break
                    for event in stream:
                        if self.shutdown_event.is_set():
                            break
                        self.handle_event(client, event)
                finally:
                    self._events_stream = None
                    _close_stream(stream)

            except Exception as exc:
                if self.shutdown_event.is_set():
                    # Closing the stream on shutdown can surface as a read error.
                    logger.debug("Events stream ended during shutdown: %s", exc)
                    break
                self._log_and_wait(exc)
            except KeyboardInterrupt:
                logger.info("Interrupted, stopping")
                break

        logger.info("Docker Sentinel stopped")

    def _log_and_wait(self, exc: Exception) -> None:
        """Log a loop failure and wait before reconnecting (interrupted by stop())."""
        if isinstance(exc, docker.errors.APIError):
            logger.warning(
                "Lost connection to the Docker API: %s; retrying in %gs", exc, self.retry_delay
            )
        else:
            logger.error("Unexpected error: %s; retrying in %gs", exc, self.retry_delay)
        self.shutdown_event.wait(self.retry_delay)


def _close_stream(stream: Any) -> None:
    """Close a Docker events stream, ignoring streams without close() and close errors."""
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception as exc:
        logger.debug("Ignoring error while closing the events stream: %s", exc)
