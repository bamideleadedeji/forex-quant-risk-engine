import os
import smtplib
import ssl
from email.message import EmailMessage


def send_email_alert(subject: str, body: str) -> None:
    """Sends instant trade and risk alerts via Gmail SMTP SSL."""
    sender = os.environ.get("GMAIL_USER", "bamideleadedeji2000@gmail.com").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("ALERT_EMAIL_TO", sender).strip()

    if not sender or not app_password:
        print("  [INFO] Gmail credentials not set in env; skipping email alert.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"FQRE v3.0 Quant Desk <{sender}>"
    msg["To"] = recipient
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("://gmail.com", 465, context=context, timeout=10) as server:
            server.login(sender, app_password)
            server.send_message(msg)
        print(f"  [EMAIL SENT] Alert delivered to {recipient}")
    except Exception as exc:
        print(f"  [WARN] Email alert failed: {exc}")
