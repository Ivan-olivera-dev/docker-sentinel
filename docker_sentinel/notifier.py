"""Email alerts sent over SMTP."""

from __future__ import annotations

import html
import logging
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import SmtpSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AlertCopy:
    subject: str
    heading: str
    summary: str
    status: str
    header_color: str
    status_color: str


def _single_line(value: object) -> str:
    """Remove line breaks so values cannot inject email headers."""
    return "".join(str(value).splitlines())


def _alert_copy(
    container_name: str, is_critical: bool, docker_restart_policy: str | None = None
) -> _AlertCopy:
    if docker_restart_policy is not None:
        return _docker_managed_copy(container_name, is_critical, docker_restart_policy)
    if is_critical:
        return _AlertCopy(
            subject=f"Docker Sentinel: crash loop detected on {container_name}",
            heading="Crash loop detected",
            summary=(
                "The container crashed repeatedly within the configured time window. "
                "Docker Sentinel has paused automatic restarts for this container."
            ),
            status="Automatic restarts paused, manual intervention required",
            header_color="#991b1b",
            status_color="#ef4444",
        )
    return _AlertCopy(
        subject=f"Docker Sentinel: container {container_name} restarted",
        heading="Container restarted",
        summary=(
            "Docker Sentinel detected an unexpected container exit and restarted the container."
        ),
        status="Restarted automatically",
        header_color="#ef4444",
        status_color="#10b981",
    )


def _docker_managed_copy(container_name: str, is_critical: bool, policy: str) -> _AlertCopy:
    """Copy for containers that Docker restarts itself (Sentinel only alerts)."""
    if is_critical:
        return _AlertCopy(
            subject=f"Docker Sentinel: crash loop detected on {container_name}",
            heading="Crash loop detected",
            summary=(
                "The container crashed repeatedly within the configured time window. "
                f"Docker keeps restarting it because of its restart policy {policy}, and "
                "Docker Sentinel cannot pause Docker's own restarts."
            ),
            status=(
                f"Docker keeps restarting it (restart policy {policy}), "
                "manual intervention required"
            ),
            header_color="#991b1b",
            status_color="#ef4444",
        )
    return _AlertCopy(
        subject=f"Docker Sentinel: container {container_name} crashed",
        heading="Container crashed",
        summary=(
            "Docker Sentinel detected an unexpected container exit. "
            f"Docker restarts the container because of its restart policy {policy}, "
            "so Docker Sentinel did not restart it."
        ),
        status=f"Docker restarts it (restart policy {policy})",
        header_color="#ef4444",
        status_color="#f59e0b",
    )


def _render_text(copy: _AlertCopy, container_name: str, exit_code: str, timestamp: str) -> str:
    return (
        f"{copy.heading}\n"
        f"\n"
        f"{copy.summary}\n"
        f"\n"
        f"Container: {container_name}\n"
        f"Exit code: {exit_code}\n"
        f"Timestamp: {timestamp}\n"
        f"Status:    {copy.status}\n"
        f"\n"
        f"Sent by Docker Sentinel.\n"
    )


def _render_html(copy: _AlertCopy, container_name: str, exit_code: str, timestamp: str) -> str:
    name = html.escape(container_name)
    code = html.escape(exit_code)
    cell = "padding: 10px; border-bottom: 1px solid #eee;"
    status_style = f"{cell} color: {copy.status_color}; font-weight: bold;"
    return f"""\
<html>
  <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
    <div style="max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px;">
      <div style="background-color: {copy.header_color}; color: white; padding: 20px;">
        <h2 style="margin: 0;">{html.escape(copy.heading)}</h2>
      </div>
      <div style="padding: 20px;">
        <p>{html.escape(copy.summary)}</p>
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
          <tr>
            <td style="{cell} font-weight: bold; width: 35%;">Container</td>
            <td style="{cell} font-family: monospace;">{name}</td>
          </tr>
          <tr>
            <td style="{cell} font-weight: bold;">Exit code</td>
            <td style="{cell}">{code}</td>
          </tr>
          <tr>
            <td style="{cell} font-weight: bold;">Timestamp</td>
            <td style="{cell}">{timestamp}</td>
          </tr>
          <tr>
            <td style="{cell} font-weight: bold;">Status</td>
            <td style="{status_style}">{html.escape(copy.status)}</td>
          </tr>
        </table>
        <p style="font-size: 12px; color: #6b7280; margin-top: 30px;">Sent by Docker Sentinel.</p>
      </div>
    </div>
  </body>
</html>
"""


def build_alert_message(
    container_name: str,
    exit_code: str,
    is_critical: bool,
    sender: str,
    recipient: str,
    now: datetime | None = None,
    docker_restart_policy: str | None = None,
) -> MIMEMultipart:
    """Build a multipart/alternative alert with a plain-text and an HTML part.

    ``docker_restart_policy`` names the Docker restart policy when Docker, not
    Sentinel, restarts the container.
    """
    name = _single_line(container_name)
    code = _single_line(exit_code)
    timestamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S UTC")
    policy = None if docker_restart_policy is None else _single_line(docker_restart_policy)
    copy = _alert_copy(name, is_critical, policy)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = copy.subject
    msg["From"] = f"Docker Sentinel <{sender}>"
    msg["To"] = recipient
    # Per RFC 2046 the preferred (richest) alternative goes last.
    msg.attach(MIMEText(_render_text(copy, name, code, timestamp), "plain", "utf-8"))
    msg.attach(MIMEText(_render_html(copy, name, code, timestamp), "html", "utf-8"))
    return msg


def send_alert_email(
    container_name: str,
    exit_code: str,
    is_critical: bool = False,
    smtp_settings: SmtpSettings | None = None,
    docker_restart_policy: str | None = None,
) -> bool:
    """Send an alert email. Returns True when the SMTP server accepted the message."""
    settings = smtp_settings or SmtpSettings.load()
    if not settings.is_complete or settings.user is None or settings.recipient is None:
        logger.warning("SMTP settings incomplete; skipping email alert for %s", container_name)
        return False

    msg = build_alert_message(
        container_name,
        exit_code,
        is_critical,
        sender=settings.user,
        recipient=settings.recipient,
        docker_restart_policy=docker_restart_policy,
    )

    try:
        with smtplib.SMTP(settings.server, settings.port, timeout=settings.timeout) as server:
            server.starttls()
            server.login(settings.user, settings.password or "")
            server.sendmail(settings.user, settings.recipient, msg.as_string())
        logger.info(
            "Alert email sent to %s for container %s",
            settings.recipient,
            _single_line(container_name),
        )
        return True
    except Exception as exc:
        logger.error("Failed to send alert email: %s", exc)
        return False
