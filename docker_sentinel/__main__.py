"""Command-line entry point: ``python -m docker_sentinel`` or ``docker-sentinel``."""

from __future__ import annotations

import logging
import signal
from functools import partial
from types import FrameType

from .config import Settings, SmtpSettings
from .monitor import Monitor, notify_in_background
from .notifier import send_alert_email

logger = logging.getLogger("docker_sentinel")


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    send = partial(send_alert_email, smtp_settings=SmtpSettings.load())
    monitor = Monitor(settings, notify=notify_in_background(send))

    def handle_shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s, shutting down", signum)
        monitor.stop()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    monitor.run()


if __name__ == "__main__":
    main()
