import os
import smtplib
import html
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

logger = logging.getLogger("sentinel.notifier")


def get_secret(secret_name: str, default=None):
    """Read secret from Docker secrets (/run/secrets/<name>) or fall back to environment variables."""
    secret_path = f"/run/secrets/{secret_name.lower()}"
    if os.path.exists(secret_path):
        with open(secret_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return os.getenv(secret_name, default)


SMTP_SERVER = get_secret("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(get_secret("SMTP_PORT", 587))
SMTP_TIMEOUT = int(get_secret("SMTP_TIMEOUT", 15))
SMTP_USER = get_secret("SMTP_USER")
SMTP_PASSWORD = get_secret("SMTP_PASSWORD")
ALERT_EMAIL_TO = get_secret("ALERT_EMAIL_TO")


def send_alert_email(container_name: str, exit_code: str, is_critical: bool = False) -> bool:
    """
    Send an HTML incident report via SMTP.
    When is_critical is True, reports that the Crash Loop circuit breaker tripped.
    """
    if not all([SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO]):
        logger.warning(
            "⚠️ [NOTIFIER] Missing SMTP credentials. Skipping email alert for '%s'.",
            container_name,
        )
        return False

    safe_container_name = "".join(str(container_name).splitlines())
    safe_container_name_html = html.escape(safe_container_name)
    safe_exit_code_html = html.escape(str(exit_code))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if is_critical:
        subject = f"🛑 FATAL: Crash Loop Detected on {safe_container_name}"
        header_color = "#991b1b"
        header_text = "🛑 FATAL: Crash Loop Circuit Breaker Tripped"
        status_color = "#ef4444"
        status_text = "AUTO-HEALER PAUSED ❌ (Manual intervention required)"
        message_body = (
            "The container crashed repeatedly within the configured time window. "
            "To prevent resource exhaustion, Docker Sentinel has <strong>paused automatic restarts</strong> "
            "for this service."
        )
    else:
        subject = f"🚨 Docker Sentinel: Container Recovered ({safe_container_name})"
        header_color = "#ef4444"
        header_text = "🚨 Incident Alert: Container Crashed"
        status_color = "#10b981"
        status_text = "Automatically Restarted ✅"
        message_body = (
            "<strong>Docker Sentinel</strong> detected an unexpected container exit and "
            "automatically restarted the service."
        )

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <div style="max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
          <div style="background-color: {header_color}; color: white; padding: 20px; text-align: center;">
            <h2 style="margin: 0;">{header_text}</h2>
          </div>
          <div style="padding: 20px;">
            <p>Hello Operator,</p>
            <p>{message_body}</p>
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold; width: 35%;">Container:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee; color: #1f2937; font-family: monospace; font-size: 16px;">{safe_container_name_html}</td>
              </tr>
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold;">Exit Code:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">{safe_exit_code_html}</td>
              </tr>
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold;">Timestamp:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">{timestamp}</td>
              </tr>
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold;">Current Status:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee; color: {status_color}; font-weight: bold;">{status_text}</td>
              </tr>
            </table>
            <p style="font-size: 12px; color: #6b7280; margin-top: 30px; text-align: center;">
              Sent automatically by <strong>Docker Sentinel Auto-Healer</strong>.
            </p>
          </div>
        </div>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Docker Sentinel <{SMTP_USER}>"
    msg["To"] = ALERT_EMAIL_TO
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, ALERT_EMAIL_TO, msg.as_string())
        logger.info("✅ [NOTIFIER] Alert email sent to %s for '%s'.", ALERT_EMAIL_TO, safe_container_name)
        return True
    except Exception as exc:
        logger.error("❌ [NOTIFIER] Failed to send alert email: %s", exc)
        return False
