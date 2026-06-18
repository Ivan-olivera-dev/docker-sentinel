import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO")

def send_alert_email(container_name: str, exit_code: str, is_critical: bool = False):
    """
    Envía un correo electrónico de alerta. 
    Si is_critical es True, indica un Crash Loop y que el Auto-Healer se ha rendido.
    """
    if not all([SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO]):
        print(f"⚠️ [NOTIFIER] Faltan credenciales SMTP. No se enviará email para {container_name}.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if is_critical:
        subject = f"🛑 FATAL: Crash Loop Detectado en {container_name}"
        header_color = "#991b1b" # Rojo oscuro
        header_text = "🛑 FATAL: Bucle de Muerte Detectado"
        status_color = "#ef4444"
        status_text = "AUTO-HEALER DESACTIVADO ❌ (Requiere intervención humana)"
        message_body = "El contenedor ha muerto repetidamente en un corto periodo de tiempo. Para evitar saturación, el Auto-Healer ha <strong>detenido los reinicios automáticos</strong> para este contenedor."
    else:
        subject = f"🚨 Docker Sentinel: Contenedor Caído ({container_name})"
        header_color = "#ef4444" # Rojo normal
        header_text = "🚨 Alerta: Contenedor Caído"
        status_color = "#10b981"
        status_text = "Reiniciado Automáticamente ✅"
        message_body = "El sistema <strong>Docker Sentinel</strong> ha detectado una caída inesperada y ha procedido a reiniciar el servicio automáticamente."

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <div style="max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
          <div style="background-color: {header_color}; color: white; padding: 20px; text-align: center;">
            <h2 style="margin: 0;">{header_text}</h2>
          </div>
          <div style="padding: 20px;">
            <p>Hola SysAdmin,</p>
            <p>{message_body}</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold; width: 35%;">Contenedor:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee; color: #1f2937; font-family: monospace; font-size: 16px;">{container_name}</td>
              </tr>
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold;">Último Exit Code:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">{exit_code}</td>
              </tr>
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold;">Hora del Incidente:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee;">{timestamp}</td>
              </tr>
              <tr>
                <td style="padding: 10px; border-bottom: 1px solid #eee; font-weight: bold;">Estado Actual:</td>
                <td style="padding: 10px; border-bottom: 1px solid #eee; color: {status_color}; font-weight: bold;">{status_text}</td>
              </tr>
            </table>
            
            <p style="font-size: 12px; color: #6b7280; margin-top: 30px; text-align: center;">
              Enviado automáticamente por <strong>Docker Sentinel Auto-Healer</strong>.
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

    part = MIMEText(html_content, "html")
    msg.attach(part)

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, ALERT_EMAIL_TO, msg.as_string())
        server.quit()
        print(f"✅ [NOTIFIER] Email enviado a {ALERT_EMAIL_TO} sobre {container_name}.")
    except Exception as e:
        print(f"❌ [NOTIFIER] Falló el envío de correo: {str(e)}")
