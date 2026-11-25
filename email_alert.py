import json
import os
import smtplib
import socket
import ssl
import urllib.request
from email.message import EmailMessage


def _resolve_smtp_host(host: str = "smtp.gmail.com") -> str:
    try:
        socket.gethostbyname(host)
        return host
    except Exception:
        try:
            url = f"https://dns.google{host}&type=A"
            data = json.loads(urllib.request.urlopen(url, timeout=5).read())
            for ans in data.get("Answer", []):
                if ans.get("type") == 1:
                    return ans["data"]
        except Exception:
            pass
    return host


def send_email_alert(subject: str, body: str) -> None:
    """Sends instant trade and risk alerts via Gmail SMTP SSL with Google DNS fallback."""
    sender = os.environ.get("GMAIL_USER", "bamideleadedeji2000@gmail.com").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
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
        target_ip = _resolve_smtp_host("smtp.gmail.com")
        context = ssl.create_default_context()
        raw_sock = socket.create_connection((target_ip, 465), timeout=10)
        ssl_sock = context.wrap_socket(raw_sock, server_hostname="smtp.gmail.com")
        with smtplib.SMTP_SSL(host="smtp.gmail.com", port=465, context=context, timeout=10) as server:
            server.sock = ssl_sock
            server.ehlo()
            server.login(sender, app_password)
            server.send_message(msg)
        print(f"  [EMAIL SENT] Alert delivered to {recipient}")
    except Exception as exc:
        print(f"  [WARN] Email alert failed: {exc}")
